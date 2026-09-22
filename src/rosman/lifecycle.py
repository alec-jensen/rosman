"""Container lifecycle manager: create/start/stop/remove the single
persistent container for a workspace, detect config drift, and build the
per-workspace image.

The image itself bakes in a *fixed* user/UID (see IMAGE_UID/IMAGE_GID) --
deliberately not the builder's own host UID -- so the exact same image can
be shared across a team via `registry_image` (built once, pushed, pulled
by everyone else) without baking in whoever happened to build it first.
Host-UID file-permission matching still happens, but purely at container
*runtime* via `docker run --user`, same as always; see the module docstring
in `platform_support.py`'s neighbor concepts and `create_container` below.
Because arbitrary runtime UIDs won't have a passwd entry in the image, an
ENTRYPOINT script patches one in on every container start (the standard
"arbitrary UID" container pattern), and a few paths that used to be
chowned to a specific UID at build time are instead made world-writable.
"""

from __future__ import annotations

import hashlib
import os
import platform
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

import docker
from docker.errors import APIError, ImageNotFound, NotFound
from docker.types import DeviceRequest

from rosman.config import RosmanConfig
from rosman.docker_client import (
    CONFIG_HASH_LABEL,
    DISTRO_LABEL,
    DOMAIN_ID_LABEL,
    MANAGED_LABEL,
    NETWORK_GROUP_LABEL,
    NETWORK_MODE_LABEL,
    RESTART_POLICY_LABEL,
    WORKSPACE_LABEL,
)
from rosman.errors import ContainerError, RosmanError
from rosman.naming import container_name, image_name, volume_name, workspace_hash
from rosman.networking import (
    CYCLONEDDS_CONTAINER_PATH,
    RMW_IMPLEMENTATION_ENV,
    dds_port_range,
    ensure_network,
    refresh_peers,
    refresh_peers_host_mode,
)
from rosman.platform_support import gui_passthrough
from rosman.progress import DockerStreamError, NullReporter, ProgressReporter
from rosman.state import RosmanState, state_dir

CONTAINER_WORKSPACE_PATH = "/workspace"
DEFAULT_USERNAME = "rosman"
SETUP_SCRIPT_CONTAINER_NAME = "rosman-setup.sh"
ENTRYPOINT_CONTAINER_PATH = "/usr/local/bin/rosman-entrypoint.sh"

# Fixed identity baked into every rosman image, deliberately *not* the
# builder's host UID/GID -- this is what makes a `registry_image` byte-
# identical (and therefore its tag reusable) regardless of who builds it.
# The actual host UID/GID is still applied at container-*runtime* via
# `docker run --user` in create_container, exactly as before.
IMAGE_UID = 1000
IMAGE_GID = 1000

# Standard Debian/Ubuntu groups that own hardware device nodes by default
# (serial adapters under dialout, cameras under video, block devices under
# disk, etc). `--device` alone only grants cgroup-level access to the node;
# the node's own Unix permissions (typically owner root, group one of these,
# mode 0660) still block a non-root, non-member process from opening it.
# Passed to `containers.create(group_add=...)` at runtime (so it applies
# regardless of which UID/GID `--user` ends up being) and guaranteed to
# exist in the image via `render_dockerfile` (some minimal base images may
# be missing one, e.g. `plugdev`, so the build creates whichever are absent
# rather than assuming every base image already has them).
DEVICE_GROUPS = ["dialout", "video", "audio", "plugdev", "disk", "tty", "uucp"]

# Bump whenever render_dockerfile changes in a way that affects the built
# image (a new apt package, a fixed bug like the /etc/profile.d ROS sourcing
# fix). Config-hash inputs otherwise only cover *user-facing* rosman.yml
# fields, so without this a rosman upgrade that fixes something in the
# template would silently leave existing users on their old, buggy cached
# image forever -- `rosman up` would just find the old tag and reuse it.
DOCKERFILE_TEMPLATE_VERSION = 8

# Ubuntu codename ROS 2 apt packages are published under for each distro, used
# only when `base_image` overrides the default `ros:<distro>` image and rosman
# has to apt-install ROS 2 onto an arbitrary base itself (spec extension:
# some projects need a base other than the stock ros image -- e.g. an
# nvidia/cuda image for GPU-heavy stacks like the ZED SDK -- so `base_image`
# lets a project pick its own base and rosman still wires ROS 2 on top).
# This mapping is a fixed historical fact per distro except for `rolling`,
# which tracks whatever Ubuntu release is current for it and may need
# updating over time.
UBUNTU_CODENAME_FOR_DISTRO = {
    "foxy": "focal",
    "galactic": "focal",
    "humble": "jammy",
    "iron": "jammy",
    "jazzy": "noble",
    "kilted": "noble",
    "rolling": "noble",
}


def host_uid_gid() -> tuple[int, int]:
    if hasattr(os, "getuid"):
        return os.getuid(), os.getgid()  # type: ignore[attr-defined]
    # Native Windows (no WSL2) has no POSIX uid/gid; fall back to a
    # conventional first-user id. rosman's supported Windows path runs
    # through WSL2, where os.getuid() is always available.
    return 1000, 1000


def resolve_network_mode(config: RosmanConfig) -> str:
    """`network_mode: auto` resolves to "host" on Linux (including WSL2,
    which reports `platform.system() == "Linux"` -- it's a real Linux
    kernel, so host networking there is exactly as reliable as native
    Linux) and "bridge" everywhere else. As of 2026, Docker Desktop's own
    host-networking support on native Windows still has real, current,
    documented issues (see spec.md's sixth addendum) -- this is a live
    fact worth rechecking if revisiting this default, not a permanent one.
    `network_mode: host`/`bridge` in rosman.yml force one or the other
    regardless of platform."""
    if config.network_mode == "auto":
        return "host" if platform.system() == "Linux" else "bridge"
    return config.network_mode


def _parse_port_spec(spec: str) -> tuple[str, int]:
    """"10000:10000" -> ("10000/tcp", 10000); "10000" -> ("10000/tcp", 10000)."""
    parts = spec.split(":")
    host_port = parts[0]
    container_port = parts[-1]
    return f"{container_port}/tcp", int(host_port)


def resolve_ports(config: RosmanConfig, domain_id: int) -> dict[str, int] | None:
    """Bridge-mode-only port publishing: `ports:` (user-declared, TCP,
    e.g. a rosbridge/ros_tcp_endpoint-style service) merged with the
    `remote_peers`-driven Cyclone DDS port window (see
    `networking.dds_port_range`). Host mode needs neither -- every
    container port already *is* the host's port -- so callers should only
    reach this when `resolve_network_mode` returned "bridge"."""
    ports: dict[str, int] = {}
    for spec in config.ports:
        key, host_port = _parse_port_spec(spec)
        ports[key] = host_port
    if config.remote_peers:
        for port in dds_port_range(domain_id):
            ports[f"{port}/udp"] = port
    return ports or None


def _setup_script_digest(config: RosmanConfig) -> str:
    """Hash of the setup script's *contents*, not just its path, so editing
    the script (e.g. bumping a ZED SDK installer version) is picked up as
    config drift and triggers a rebuild -- not just renaming/removing it."""
    if not config.setup_script:
        return ""
    script_path = (config.project_root / config.setup_script).resolve()
    try:
        return hashlib.sha256(script_path.read_bytes()).hexdigest()
    except OSError:
        return f"MISSING:{config.setup_script}"


def compute_config_hash(config: RosmanConfig) -> str:
    """Hash of everything that affects the built image. Used both as the
    image tag and as a container label, so `rosman status`/drift detection
    can tell whether a running container matches the current rosman.yml
    without re-parsing anything.

    Deliberately does *not* include the host UID/GID: the image bakes in a
    fixed identity (IMAGE_UID/IMAGE_GID) regardless of who builds it, so two
    teammates with different host UIDs building the same rosman.yml still
    get the same hash/tag -- required for `registry_image` sharing to mean
    anything (a tag that changed per-builder couldn't be shared at all)."""
    payload = "|".join(
        [
            str(DOCKERFILE_TEMPLATE_VERSION),
            config.ros_distro,
            config.rmw_implementation,
            ",".join(sorted(config.extra_apt_packages)),
            ",".join(sorted(config.locked_apt_packages)),
            ",".join(sorted(config.locked_pip_packages)),
            config.base_image or "",
            config.setup_script or "",
            _setup_script_digest(config),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def _render_ros_install_block(config: RosmanConfig) -> str:
    """Only needed when `base_image` overrides the default `ros:<distro>`
    image: that image already has ROS 2 installed, but an arbitrary base
    (e.g. `nvidia/cuda:...`) doesn't, so rosman adds the ROS 2 apt repo and
    installs ros-base itself, matching the official install instructions."""
    if not config.base_image:
        return ""
    codename = UBUNTU_CODENAME_FOR_DISTRO.get(config.ros_distro)
    if codename is None:
        raise ContainerError(
            f"Don't know the Ubuntu codename to install ros_distro '{config.ros_distro}' "
            "onto a custom base_image. Known distros: "
            f"{', '.join(sorted(UBUNTU_CODENAME_FOR_DISTRO))}."
        )
    # The sources.list line is built from separate quoted `echo` arguments
    # (echo joins them with single spaces) rather than one string broken
    # across continuation lines -- breaking a *quoted* string across
    # Dockerfile continuation lines leaves the continuation lines' leading
    # whitespace embedded literally in the value, corrupting the apt entry.
    return f"""
# base_image override: install ROS 2 {config.ros_distro} onto {config.base_image}
RUN apt-get update \\
    && apt-get install -y --no-install-recommends curl gnupg lsb-release ca-certificates \\
    && curl -sSL https://raw.githubusercontent.com/ros/rosdistro/master/ros.key \\
        -o /usr/share/keyrings/ros-archive-keyring.gpg \\
    && echo "deb [arch=$(dpkg --print-architecture)" \\
        "signed-by=/usr/share/keyrings/ros-archive-keyring.gpg]" \\
        "http://packages.ros.org/ros2/ubuntu {codename} main" \\
        > /etc/apt/sources.list.d/ros2.list \\
    && apt-get update \\
    && apt-get install -y --no-install-recommends ros-{config.ros_distro}-ros-base \\
    && rm -rf /var/lib/apt/lists/*
"""


def _render_entrypoint_script() -> str:
    """Standard "arbitrary UID" container pattern: patch a passwd entry for
    whatever UID `docker run --user` actually sets at container start,
    since the image's own baked user (IMAGE_UID/IMAGE_GID) is a fixed
    placeholder, not necessarily the runtime UID. Without this, anything
    that calls getpwuid (bash's own prompt, git, some colcon/rosdep paths,
    sudo's own PAM checks) misbehaves for a teammate whose host UID isn't
    exactly IMAGE_UID -- which, sharing one `registry_image`, is everyone
    except whoever happens to be IMAGE_UID.

    Each line is a separate single-quoted printf argument (never a quoted
    string broken across Dockerfile continuation lines) for the same reason
    as the CycloneDDS/profile.d generators: it's the only way to guarantee
    no stray whitespace or premature `$`-expansion sneaks into the file.
    """
    passwd_append_line = (
        f'    echo "rosman:x:$(id -u):$(id -g)::/home/{DEFAULT_USERNAME}:/bin/bash" '
        ">> /etc/passwd"
    )
    return f"""
RUN printf '%s\\n' \\
        '#!/bin/bash' \\
        'if ! getent passwd "$(id -u)" > /dev/null 2>&1; then' \\
        '{passwd_append_line}' \\
        'fi' \\
        'exec "$@"' \\
        > {ENTRYPOINT_CONTAINER_PATH} \\
    && chmod +x {ENTRYPOINT_CONTAINER_PATH}
"""


def render_dockerfile(config: RosmanConfig) -> str:
    base = config.base_image or f"ros:{config.ros_distro}"
    # `locked_apt_packages` (from rosman.lock, see rosdep.py) are folded in
    # alongside `extra_apt_packages` -- same install line, just a different
    # source (rosdep-resolved vs. hand-listed), deduped since a package
    # could plausibly appear in both.
    all_packages = sorted(set(config.extra_apt_packages) | set(config.locked_apt_packages))
    extra_packages = " ".join(all_packages)
    install_extra = f" {extra_packages}" if extra_packages else ""
    ros_install_block = _render_ros_install_block(config)

    # Built as plain Python strings (not inline in the Dockerfile f-string
    # below) specifically so each can stay a single, unbroken shell string
    # regardless of how long the substituted paths are -- see the regression
    # test for _render_ros_install_block: breaking a *quoted* shell string
    # across Dockerfile continuation lines corrupts it with stray whitespace.
    ros_setup_path = f"/opt/ros/{config.ros_distro}/setup.bash"
    ws_setup_path = f"{CONTAINER_WORKSPACE_PATH}/install/setup.bash"
    ros_profile_line = f"[ -f {ros_setup_path} ] && . {ros_setup_path}"
    ws_profile_line = f"[ -f {ws_setup_path} ] && . {ws_setup_path}"

    pip_install_block = ""
    if config.locked_pip_packages:
        # Locked via `rosman rosdep install` -- see rosdep.py. Installed
        # system-wide as root (we're still root at this point in the
        # build, before any USER switch), not `--user`, so it doesn't
        # depend on the arbitrary-UID/HOME-matching machinery at all.
        # PIP_BREAK_SYSTEM_PACKAGES=1 mirrors exactly what rosdep's own
        # pip installer does at runtime (confirmed against a real rosdep
        # install: `sudo -H --preserve-env=PIP_BREAK_SYSTEM_PACKAGES pip3
        # install ...`) -- needed on newer Ubuntu/Debian (PEP 668), a
        # harmlessly-ignored env var on older ones that predate it.
        pip_packages_str = " ".join(sorted(set(config.locked_pip_packages)))
        pip_install_block = f"""
RUN PIP_BREAK_SYSTEM_PACKAGES=1 pip3 install --no-cache-dir {pip_packages_str}
"""

    setup_block = ""
    if config.setup_script:
        # Run as the rosman user, not root: installers that write into the
        # user's home (SDK licenses/caches, pip --user, etc.) need HOME set
        # correctly, matching how the container actually runs at `rosman up`.
        setup_block = f"""
COPY {SETUP_SCRIPT_CONTAINER_NAME} /tmp/{SETUP_SCRIPT_CONTAINER_NAME}
RUN chmod +x /tmp/{SETUP_SCRIPT_CONTAINER_NAME}
USER {DEFAULT_USERNAME}
RUN /tmp/{SETUP_SCRIPT_CONTAINER_NAME}
USER root
RUN rm /tmp/{SETUP_SCRIPT_CONTAINER_NAME}
"""

    entrypoint_block = _render_entrypoint_script()

    return f"""FROM {base}

# Must come before any package installation below: on a bare Ubuntu/Debian
# base (e.g. a `base_image` override like nvidia/cuda, which -- unlike
# ros:<distro> -- doesn't set this itself), installing ca-certificates
# pulls in tzdata as a dependency, and tzdata's postinst prompts
# interactively for a timezone. In a non-interactive `docker build` that
# has no TTY to answer it, this doesn't error out -- it hangs forever.
# Found by an actual build hanging for 20+ minutes on
# `dpkg --configure tzdata` before being traced to it.
ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Etc/UTC
{ros_install_block}
RUN (getent group {IMAGE_GID} || groupadd --gid {IMAGE_GID} {DEFAULT_USERNAME}) \\
    && (getent passwd {IMAGE_UID} || \\
        useradd --uid {IMAGE_UID} --gid {IMAGE_GID} -m -s /bin/bash {DEFAULT_USERNAME}) \\
    && (for grp in {" ".join(DEVICE_GROUPS)}; do getent group "$grp" > /dev/null \\
        || groupadd --system "$grp"; done) \\
    && apt-get update \\
    && apt-get install -y --no-install-recommends \\
        sudo python3-colcon-common-extensions python3-rosdep python3-pip \\
        ros-{config.ros_distro}-rmw-cyclonedds-cpp{install_extra} \\
    && echo "ALL ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/rosman \\
    && chmod 0440 /etc/sudoers.d/rosman \\
    && rm -rf /var/lib/apt/lists/* \\
    && (rosdep init || true) \\
    && su -l {DEFAULT_USERNAME} -c "rosdep update"
{pip_install_block}
# Everything below is world-writable/-readable rather than owned by a
# specific UID: the image's baked identity ({IMAGE_UID}:{IMAGE_GID}) is
# deliberately not tied to whichever host UID actually runs the container
# (see IMAGE_UID's docstring above) -- a `registry_image` shared across a
# team gets pulled and run by many different host UIDs, none of which need
# match {IMAGE_UID}, so nothing that a runtime UID must write to can be
# chowned to a single fixed UID at build time.
RUN chmod 0777 /home/{DEFAULT_USERNAME} \\
    && chmod 0666 /etc/passwd

# build/install/log are mounted as named volumes (see naming.py::volume_name),
# not part of the workspace bind mount -- a fresh named volume is empty, and
# Docker only inherits the *image's* permissions at that path into it on
# first mount. Without this, the volumes come up root-owned and every
# `colcon build` fails with EACCES under an arbitrary runtime UID.
RUN mkdir -p {CONTAINER_WORKSPACE_PATH}/build {CONTAINER_WORKSPACE_PATH}/install \\
        {CONTAINER_WORKSPACE_PATH}/log \\
    && chmod -R 0777 {CONTAINER_WORKSPACE_PATH}

ENV RMW_IMPLEMENTATION={RMW_IMPLEMENTATION_ENV}
ENV RCUTILS_COLORIZED_OUTPUT=1
ENV ROS_DISTRO={config.ros_distro}
ENV HOME=/home/{DEFAULT_USERNAME}

# `ros2`/colcon overlay setup only takes effect once setup.bash is sourced,
# and that's a per-shell action, not a static PATH -- the ros:<distro>
# image's own ENTRYPOINT sources it for the container's main process, but
# `docker exec` sessions get a fresh environment and never see it. rosman
# always runs commands through a login shell (`bash -lc`/`bash -l`, see
# dispatch.py), so putting the sourcing in /etc/profile.d makes every
# ros2/colcon passthrough call and `rosman shell` pick it up automatically.
RUN printf '%s\\n' \\
        "{ros_profile_line}" \\
        "{ws_profile_line}" \\
        > /etc/profile.d/rosman-ros.sh \\
    && chmod +x /etc/profile.d/rosman-ros.sh
{setup_block}
{entrypoint_block}
WORKDIR {CONTAINER_WORKSPACE_PATH}
ENTRYPOINT ["{ENTRYPOINT_CONTAINER_PATH}"]
"""


@dataclass
class DriftReport:
    drifted: bool
    reasons: list[str]


@dataclass
class ImageResult:
    tag: str
    source: str  # "cached" | "pulled" | "built"


# Matches tags from images built *before* MANAGED_LABEL was added at
# build time (`rosman/<distro>-<workspace_hash>:<config_hash>`, see
# naming.image_name) -- a fallback so upgrading to `rosman prune` doesn't
# leave every pre-existing image invisible to it just because it predates
# labeling. Doesn't cover `registry_image`-tagged images built before this
# either, since those have no distinguishing prefix at all; those only
# become prunable once rebuilt/repushed with the new labeled version.
_LEGACY_IMAGE_TAG_RE = re.compile(r"^rosman/[a-z0-9-]+-[0-9a-f]{8}:[0-9a-f]{12}$")


def list_managed_images(client: docker.DockerClient) -> list:
    """Every rosman-built image on this machine, across all workspaces --
    both labeled (built since `rosman prune` support was added) and
    legacy (identified by tag pattern instead, see
    `_LEGACY_IMAGE_TAG_RE`)."""
    labeled = client.images.list(filters={"label": f"{MANAGED_LABEL}=true"})
    labeled_ids = {image.id for image in labeled}
    legacy = [
        image
        for image in client.images.list()
        if image.id not in labeled_ids
        and any(_LEGACY_IMAGE_TAG_RE.match(tag) for tag in (image.tags or []))
    ]
    return labeled + legacy


def list_prunable_images(client: docker.DockerClient) -> list:
    """rosman-managed images not referenced by any existing container
    (running or stopped) -- safe to remove regardless of *why* they're
    orphaned (a config change produced a new hash-tagged image and the
    old one was never cleaned up, or the workspace itself was deleted
    entirely). Never removes an image a live container still depends on."""
    referenced_ids = {c.attrs["Image"] for c in client.containers.list(all=True)}
    return [image for image in list_managed_images(client) if image.id not in referenced_ids]


def remove_images(client: docker.DockerClient, images: list) -> tuple[int, int]:
    """Removes exactly the images given (the same list already shown to
    the user for confirmation, not recomputed -- avoids any race between
    preview and removal). Returns (count_removed, bytes_reclaimed);
    skips (doesn't raise) any image that fails to remove, e.g. because
    something referenced it in the meantime.

    Removes by *tag*, one at a time, not by bare image ID -- confirmed
    live that several small rosman workspaces with identical config
    commonly produce byte-identical images sharing one ID under many
    different repo names, and `remove(image_id, force=False)` refuses
    outright ("image is referenced in multiple repositories") whenever
    more than one tag points at it. Untagging each reference in turn (the
    same thing `docker rmi tag1 tag2 ...` does) removes the underlying
    image once its last tag is gone, without ever needing `force=True`.
    """
    removed_count = 0
    reclaimed_bytes = 0
    for image in images:
        refs = image.tags or [image.id]
        try:
            for ref in refs:
                client.images.remove(ref, force=False)
        except (APIError, NotFound):
            continue
        removed_count += 1
        reclaimed_bytes += image.attrs.get("Size", 0)
    return removed_count, reclaimed_bytes


class ContainerManager:
    def __init__(self, client: docker.DockerClient, state: RosmanState):
        self.client = client
        self.state = state
        # Set by create_container whenever it actually resolves an image
        # (cached/pulled/built), so the CLI layer can tell the user whether
        # a `registry_image` pull actually happened -- without changing
        # ensure_running/create_container's return shape for every caller.
        self.last_image_result: ImageResult | None = None

    # -- image -----------------------------------------------------------

    def ensure_image(
        self,
        config: RosmanConfig,
        config_hash: str,
        reporter: ProgressReporter | None = None,
    ) -> ImageResult:
        reporter = reporter or NullReporter()
        tag = image_name(
            config.workspace_root, config.ros_distro, config_hash, config.registry_image
        )
        try:
            self.client.images.get(tag)
            return ImageResult(tag=tag, source="cached")
        except ImageNotFound:
            pass

        if config.registry_image:
            try:
                pull_stream = self.client.api.pull(tag, stream=True, decode=True)
                reporter.pull(pull_stream)
                return ImageResult(tag=tag, source="pulled")
            except (APIError, DockerStreamError):
                # Not pushed yet, or no registry access -- fall back to a
                # local build below rather than failing `rosman up` outright.
                pass

        setup_script_source = None
        if config.setup_script:
            setup_script_source = (config.project_root / config.setup_script).resolve()
            if not setup_script_source.is_file():
                raise ContainerError(
                    f"setup_script '{config.setup_script}' (resolved to "
                    f"{setup_script_source}) does not exist."
                )

        dockerfile = render_dockerfile(config)
        with tempfile.TemporaryDirectory(prefix="rosman-build-") as build_dir_str:
            build_dir = Path(build_dir_str)
            (build_dir / "Dockerfile").write_text(dockerfile)
            if setup_script_source is not None:
                shutil.copy(setup_script_source, build_dir / SETUP_SCRIPT_CONTAINER_NAME)
            image_labels = {
                MANAGED_LABEL: "true",
                WORKSPACE_LABEL: str(config.workspace_root),
                DISTRO_LABEL: config.ros_distro,
                CONFIG_HASH_LABEL: config_hash,
            }
            try:
                build_stream = self.client.api.build(
                    path=str(build_dir), tag=tag, rm=True, decode=True, labels=image_labels
                )
                reporter.build(build_stream)
            except (APIError, DockerStreamError) as exc:
                base = config.base_image or f"ros:{config.ros_distro}"
                raise ContainerError(
                    f"Failed to build image for ros_distro '{config.ros_distro}': {exc}\n"
                    f"Check that '{base}' is a valid image and, if set, that "
                    f"setup_script '{config.setup_script}' runs cleanly."
                ) from exc
        return ImageResult(tag=tag, source="built")

    def push_image(
        self, config: RosmanConfig, reporter: ProgressReporter | None = None
    ) -> str:
        """Build (if needed) and push the workspace's image to
        `registry_image`, so teammates' `rosman up` can pull it instead of
        building locally. Requires the registry credentials to already be
        set up via `docker login <registry>` -- rosman doesn't manage
        registry auth itself, same as the plain `docker` CLI wouldn't."""
        if not config.registry_image:
            raise RosmanError(
                "Set 'registry_image' in rosman.yml before running `rosman push` "
                "(e.g. registry_image: ghcr.io/my-team/my-project)."
            )
        reporter = reporter or NullReporter()
        config_hash = compute_config_hash(config)
        result = self.ensure_image(config, config_hash, reporter=reporter)
        try:
            push_log = self.client.images.push(result.tag, stream=True, decode=True)
            reporter.push(push_log)
        except (APIError, DockerStreamError) as exc:
            raise ContainerError(
                f"Failed to push {result.tag}: {exc}\n"
                f"Make sure you're logged in: `docker login` against that registry."
            ) from exc
        return result.tag

    # -- lookup ------------------------------------------------------------

    def find_container(self, config: RosmanConfig):
        name = container_name(config.workspace_root)
        try:
            return self.client.containers.get(name)
        except NotFound:
            return None

    def list_managed(self):
        return self.client.containers.list(all=True, filters={"label": f"{MANAGED_LABEL}=true"})

    # -- drift ---------------------------------------------------------

    def detect_drift(self, container, config: RosmanConfig) -> DriftReport:
        expected_hash = compute_config_hash(config)
        labels = container.labels or {}
        reasons = []
        if labels.get(DISTRO_LABEL) != config.ros_distro:
            reasons.append(
                f"ros_distro changed ({labels.get(DISTRO_LABEL)!r} -> {config.ros_distro!r})"
            )
        if labels.get(CONFIG_HASH_LABEL) != expected_hash:
            reasons.append(
                "image config changed (distro, rmw, extra_apt_packages, base_image, "
                "or setup_script)"
            )
        if labels.get(NETWORK_GROUP_LABEL) != config.network:
            reasons.append(
                f"network group changed ({labels.get(NETWORK_GROUP_LABEL)!r} -> {config.network!r})"
            )
        resolved_network_mode = resolve_network_mode(config)
        if labels.get(NETWORK_MODE_LABEL) != resolved_network_mode:
            reasons.append(
                f"network mode changed ({labels.get(NETWORK_MODE_LABEL)!r} -> "
                f"{resolved_network_mode!r})"
            )
        domain_id = self.resolve_domain_id(config)
        if labels.get(DOMAIN_ID_LABEL) != str(domain_id):
            reasons.append(
                f"domain_id changed ({labels.get(DOMAIN_ID_LABEL)!r} -> {domain_id!r})"
            )
        if labels.get(RESTART_POLICY_LABEL) != config.restart_policy:
            reasons.append(
                f"restart_policy changed ({labels.get(RESTART_POLICY_LABEL)!r} -> "
                f"{config.restart_policy!r})"
            )
        return DriftReport(drifted=bool(reasons), reasons=reasons)

    # -- domain id -------------------------------------------------------

    def resolve_domain_id(self, config: RosmanConfig) -> int:
        if config.domain_id != "auto":
            return int(config.domain_id)
        return self.state.assign_domain_id(config.workspace_root)

    # -- create / start ----------------------------------------------------

    def create_container(
        self, config: RosmanConfig, reporter: ProgressReporter | None = None
    ):
        uid, gid = host_uid_gid()
        config_hash = compute_config_hash(config)
        image_result = self.ensure_image(config, config_hash, reporter=reporter)
        self.last_image_result = image_result
        image_tag = image_result.tag
        domain_id = self.resolve_domain_id(config)
        name = container_name(config.workspace_root)
        network_mode = resolve_network_mode(config)

        # Host mode: every container already shares this machine's real
        # network namespace, so there's no bridge network to create/join
        # and no port publishing to compute at all (see networking.py's
        # module docstring). Bridge mode: unchanged from before.
        ports: dict[str, int] | None = None
        if network_mode == "host":
            cyclonedds_path = refresh_peers_host_mode(config.remote_peers)
        else:
            ensure_network(self.client, config.network)
            cyclonedds_path = refresh_peers(self.client, config.network, config.remote_peers)
            ports = resolve_ports(config, domain_id)

        volumes = {
            str(config.workspace_root): {"bind": CONTAINER_WORKSPACE_PATH, "mode": "rw"},
            str(cyclonedds_path): {"bind": CYCLONEDDS_CONTAINER_PATH, "mode": "ro"},
        }
        for kind in ("build", "install", "log"):
            vol_name = volume_name(config.workspace_root, config.ros_distro, kind)
            if not self._volume_exists(vol_name):
                self.client.volumes.create(vol_name)
            volumes[vol_name] = {
                "bind": f"{CONTAINER_WORKSPACE_PATH}/{kind}",
                "mode": "rw",
            }

        environment = {
            "RMW_IMPLEMENTATION": RMW_IMPLEMENTATION_ENV,
            "ROS_DOMAIN_ID": str(domain_id),
            "CYCLONEDDS_URI": f"file://{CYCLONEDDS_CONTAINER_PATH}",
        }

        devices = [f"{d}:{d}:rwm" for d in config.devices] if config.devices else None
        # `--device` only grants cgroup-level access; the device node's own
        # Unix permissions (usually group-owned, e.g. dialout/video/disk)
        # still block the container's non-root user without this. See
        # DEVICE_GROUPS' docstring -- these are guaranteed to exist in the
        # image regardless of base_image, so this is safe unconditionally.
        group_add = DEVICE_GROUPS if config.devices else None
        device_requests = None
        if config.gpu:
            device_requests = [DeviceRequest(count=-1, capabilities=[["gpu"]])]

        cookie_path = state_dir() / "gui" / workspace_hash(config.workspace_root) / "xauth"
        gui = gui_passthrough(f"/home/{DEFAULT_USERNAME}", cookie_path)
        if gui is not None:
            environment.update(gui.environment)
            volumes.update(gui.volumes)

        labels = {
            MANAGED_LABEL: "true",
            WORKSPACE_LABEL: str(config.workspace_root),
            DISTRO_LABEL: config.ros_distro,
            CONFIG_HASH_LABEL: config_hash,
            NETWORK_GROUP_LABEL: config.network,
            NETWORK_MODE_LABEL: network_mode,
            DOMAIN_ID_LABEL: str(domain_id),
            RESTART_POLICY_LABEL: config.restart_policy,
        }

        container = self.client.containers.create(
            image_tag,
            name=name,
            command=["sleep", "infinity"],
            detach=True,
            tty=True,
            stdin_open=True,
            volumes=volumes,
            environment=environment,
            devices=devices,
            device_requests=device_requests,
            group_add=group_add,
            ports=ports,
            network_mode="host" if network_mode == "host" else None,
            restart_policy={"Name": config.restart_policy},
            labels=labels,
            working_dir=CONTAINER_WORKSPACE_PATH,
            user=f"{uid}:{gid}",
        )
        if network_mode != "host":
            network = ensure_network(self.client, config.network)
            network.connect(container, aliases=[name])
        container.start()
        if network_mode == "host":
            refresh_peers_host_mode(config.remote_peers)
        else:
            refresh_peers(self.client, config.network, config.remote_peers)
        self.state.set_container_name(config.workspace_root, name)
        return container

    def _volume_exists(self, name: str) -> bool:
        try:
            self.client.volumes.get(name)
            return True
        except NotFound:
            return False

    def ensure_running(self, config: RosmanConfig, reporter: ProgressReporter | None = None):
        """Find-or-create the workspace container and make sure it's started.

        Returns (container, created: bool).
        """
        container = self.find_container(config)
        if container is None:
            return self.create_container(config, reporter=reporter), True

        container.reload()
        if container.status != "running":
            container.start()
        return container, False

    # -- stop / remove -------------------------------------------------

    def stop(self, config: RosmanConfig) -> bool:
        container = self.find_container(config)
        if container is None:
            return False
        container.reload()
        if container.status == "running":
            container.stop()
        return True

    def remove(self, config: RosmanConfig) -> bool:
        container = self.find_container(config)
        if container is None:
            return False
        container.remove(force=True)
        refresh_peers(self.client, config.network, config.remote_peers)
        gui_dir = state_dir() / "gui" / workspace_hash(config.workspace_root)
        if gui_dir.exists():
            for f in gui_dir.iterdir():
                f.unlink()
            gui_dir.rmdir()
        return True

    def rebuild(self, config: RosmanConfig, reporter: ProgressReporter | None = None):
        """Force-recreate the container (and its image, if config changed)."""
        self.remove(config)
        return self.create_container(config, reporter=reporter)
