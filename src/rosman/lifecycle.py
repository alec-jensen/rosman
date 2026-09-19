"""Container lifecycle manager: create/start/stop/remove the single
persistent container for a workspace, detect config drift, and build the
per-workspace image with a host-UID-matched user baked in.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

import docker
from docker.errors import BuildError, ImageNotFound, NotFound
from docker.types import DeviceRequest

from rosman.config import RosmanConfig
from rosman.docker_client import (
    CONFIG_HASH_LABEL,
    DISTRO_LABEL,
    DOMAIN_ID_LABEL,
    MANAGED_LABEL,
    NETWORK_GROUP_LABEL,
    RESTART_POLICY_LABEL,
    WORKSPACE_LABEL,
)
from rosman.errors import ContainerError
from rosman.naming import container_name, image_name, volume_name, workspace_hash
from rosman.networking import (
    CYCLONEDDS_CONTAINER_PATH,
    RMW_IMPLEMENTATION_ENV,
    ensure_network,
    refresh_peers,
)
from rosman.platform_support import gui_passthrough
from rosman.state import RosmanState, state_dir

CONTAINER_WORKSPACE_PATH = "/workspace"
DEFAULT_USERNAME = "rosman"
SETUP_SCRIPT_CONTAINER_NAME = "rosman-setup.sh"

# Bump whenever render_dockerfile changes in a way that affects the built
# image (a new apt package, a fixed bug like the /etc/profile.d ROS sourcing
# fix). Config-hash inputs otherwise only cover *user-facing* rosman.yml
# fields, so without this a rosman upgrade that fixes something in the
# template would silently leave existing users on their old, buggy cached
# image forever -- `rosman up` would just find the old tag and reuse it.
DOCKERFILE_TEMPLATE_VERSION = 4

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


def compute_config_hash(config: RosmanConfig, uid: int, gid: int) -> str:
    """Hash of everything that affects the built image. Used both as the
    image tag and as a container label, so `rosman status`/drift detection
    can tell whether a running container matches the current rosman.yml
    without re-parsing anything."""
    payload = "|".join(
        [
            str(DOCKERFILE_TEMPLATE_VERSION),
            config.ros_distro,
            config.rmw_implementation,
            ",".join(sorted(config.extra_apt_packages)),
            str(uid),
            str(gid),
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


def render_dockerfile(config: RosmanConfig, uid: int, gid: int) -> str:
    base = config.base_image or f"ros:{config.ros_distro}"
    extra_packages = " ".join(config.extra_apt_packages)
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

    setup_block = ""
    if config.setup_script:
        # Run as the rosman user, not root: installers that write into the
        # user's home (SDK licenses/caches, pip --user, etc.) need HOME set
        # correctly, matching how the container actually runs at `rosman up`.
        setup_block = f"""
COPY {SETUP_SCRIPT_CONTAINER_NAME} /tmp/{SETUP_SCRIPT_CONTAINER_NAME}
RUN chmod +x /tmp/{SETUP_SCRIPT_CONTAINER_NAME}
USER $USERNAME
RUN /tmp/{SETUP_SCRIPT_CONTAINER_NAME}
USER root
RUN rm /tmp/{SETUP_SCRIPT_CONTAINER_NAME}
"""

    return f"""FROM {base}
ARG USERNAME={DEFAULT_USERNAME}
ARG USER_UID={uid}
ARG USER_GID={gid}

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
RUN (getent group $USER_GID || groupadd --gid $USER_GID $USERNAME) \\
    && (getent passwd $USER_UID || \\
        useradd --uid $USER_UID --gid $USER_GID -m -s /bin/bash $USERNAME) \\
    && apt-get update \\
    && apt-get install -y --no-install-recommends \\
        sudo python3-colcon-common-extensions \\
        ros-{config.ros_distro}-rmw-cyclonedds-cpp{install_extra} \\
    && echo "$USERNAME ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/$USERNAME \\
    && chmod 0440 /etc/sudoers.d/$USERNAME \\
    && rm -rf /var/lib/apt/lists/*

# build/install/log are mounted as named volumes (see naming.py::volume_name),
# not part of the workspace bind mount -- a fresh named volume is empty, and
# Docker only inherits the *image's* ownership/permissions at that path into
# it on first mount. Without pre-creating these owned by the rosman user, the
# volumes come up root-owned and every `colcon build` fails with EACCES.
RUN mkdir -p {CONTAINER_WORKSPACE_PATH}/build {CONTAINER_WORKSPACE_PATH}/install \\
        {CONTAINER_WORKSPACE_PATH}/log \\
    && chown -R $USER_UID:$USER_GID {CONTAINER_WORKSPACE_PATH}

ENV RMW_IMPLEMENTATION={RMW_IMPLEMENTATION_ENV}
ENV RCUTILS_COLORIZED_OUTPUT=1
ENV ROS_DISTRO={config.ros_distro}

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
WORKDIR {CONTAINER_WORKSPACE_PATH}
"""


@dataclass
class DriftReport:
    drifted: bool
    reasons: list[str]


class ContainerManager:
    def __init__(self, client: docker.DockerClient, state: RosmanState):
        self.client = client
        self.state = state

    # -- image -----------------------------------------------------------

    def ensure_image(self, config: RosmanConfig, config_hash: str) -> str:
        uid, gid = host_uid_gid()
        tag = image_name(config.workspace_root, config.ros_distro, config_hash)
        try:
            self.client.images.get(tag)
            return tag
        except ImageNotFound:
            pass

        setup_script_source = None
        if config.setup_script:
            setup_script_source = (config.project_root / config.setup_script).resolve()
            if not setup_script_source.is_file():
                raise ContainerError(
                    f"setup_script '{config.setup_script}' (resolved to "
                    f"{setup_script_source}) does not exist."
                )

        dockerfile = render_dockerfile(config, uid, gid)
        with tempfile.TemporaryDirectory(prefix="rosman-build-") as build_dir_str:
            build_dir = Path(build_dir_str)
            (build_dir / "Dockerfile").write_text(dockerfile)
            if setup_script_source is not None:
                shutil.copy(setup_script_source, build_dir / SETUP_SCRIPT_CONTAINER_NAME)
            try:
                self.client.images.build(path=str(build_dir), tag=tag, rm=True)
            except BuildError as exc:
                base = config.base_image or f"ros:{config.ros_distro}"
                raise ContainerError(
                    f"Failed to build image for ros_distro '{config.ros_distro}': {exc}\n"
                    f"Check that '{base}' is a valid image and, if set, that "
                    f"setup_script '{config.setup_script}' runs cleanly."
                ) from exc
        return tag

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
        uid, gid = host_uid_gid()
        expected_hash = compute_config_hash(config, uid, gid)
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

    def create_container(self, config: RosmanConfig):
        uid, gid = host_uid_gid()
        config_hash = compute_config_hash(config, uid, gid)
        image_tag = self.ensure_image(config, config_hash)
        domain_id = self.resolve_domain_id(config)
        name = container_name(config.workspace_root)

        ensure_network(self.client, config.network)
        cyclonedds_path = refresh_peers(self.client, config.network)

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
            restart_policy={"Name": config.restart_policy},
            labels=labels,
            working_dir=CONTAINER_WORKSPACE_PATH,
            user=f"{uid}:{gid}",
        )
        network = ensure_network(self.client, config.network)
        network.connect(container, aliases=[name])
        container.start()
        refresh_peers(self.client, config.network)
        self.state.set_container_name(config.workspace_root, name)
        return container

    def _volume_exists(self, name: str) -> bool:
        try:
            self.client.volumes.get(name)
            return True
        except NotFound:
            return False

    def ensure_running(self, config: RosmanConfig):
        """Find-or-create the workspace container and make sure it's started.

        Returns (container, created: bool).
        """
        container = self.find_container(config)
        if container is None:
            return self.create_container(config), True

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
        refresh_peers(self.client, config.network)
        gui_dir = state_dir() / "gui" / workspace_hash(config.workspace_root)
        if gui_dir.exists():
            for f in gui_dir.iterdir():
                f.unlink()
            gui_dir.rmdir()
        return True

    def rebuild(self, config: RosmanConfig):
        """Force-recreate the container (and its image, if config changed)."""
        self.remove(config)
        return self.create_container(config)
