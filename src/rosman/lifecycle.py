"""Container lifecycle manager: create/start/stop/remove the single
persistent container for a workspace, detect config drift, and build the
per-workspace image with a host-UID-matched user baked in.
"""

from __future__ import annotations

import hashlib
import io
import os
from dataclasses import dataclass

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
    WORKSPACE_LABEL,
)
from rosman.errors import ContainerError
from rosman.naming import container_name, image_name, volume_name
from rosman.networking import (
    CYCLONEDDS_CONTAINER_PATH,
    RMW_IMPLEMENTATION_ENV,
    ensure_network,
    refresh_peers,
)
from rosman.state import RosmanState

CONTAINER_WORKSPACE_PATH = "/workspace"
DEFAULT_USERNAME = "rosman"


def host_uid_gid() -> tuple[int, int]:
    if hasattr(os, "getuid"):
        return os.getuid(), os.getgid()  # type: ignore[attr-defined]
    # Native Windows (no WSL2) has no POSIX uid/gid; fall back to a
    # conventional first-user id. rosman's supported Windows path runs
    # through WSL2, where os.getuid() is always available.
    return 1000, 1000


def compute_config_hash(config: RosmanConfig, uid: int, gid: int) -> str:
    """Hash of everything that affects the built image. Used both as the
    image tag and as a container label, so `rosman status`/drift detection
    can tell whether a running container matches the current rosman.yml
    without re-parsing anything."""
    payload = "|".join(
        [
            config.ros_distro,
            config.rmw_implementation,
            ",".join(sorted(config.extra_apt_packages)),
            str(uid),
            str(gid),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def render_dockerfile(config: RosmanConfig, uid: int, gid: int) -> str:
    extra_packages = " ".join(config.extra_apt_packages)
    install_extra = f" {extra_packages}" if extra_packages else ""
    return f"""FROM ros:{config.ros_distro}
ARG USERNAME={DEFAULT_USERNAME}
ARG USER_UID={uid}
ARG USER_GID={gid}

RUN (getent group $USER_GID || groupadd --gid $USER_GID $USERNAME) \\
    && (getent passwd $USER_UID || \\
        useradd --uid $USER_UID --gid $USER_GID -m -s /bin/bash $USERNAME) \\
    && apt-get update \\
    && apt-get install -y --no-install-recommends \\
        sudo ros-{config.ros_distro}-rmw-cyclonedds-cpp{install_extra} \\
    && echo "$USERNAME ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/$USERNAME \\
    && chmod 0440 /etc/sudoers.d/$USERNAME \\
    && rm -rf /var/lib/apt/lists/*

ENV RMW_IMPLEMENTATION={RMW_IMPLEMENTATION_ENV}
ENV RCUTILS_COLORIZED_OUTPUT=1

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

        dockerfile = render_dockerfile(config, uid, gid)
        fileobj = io.BytesIO(dockerfile.encode("utf-8"))
        try:
            self.client.images.build(fileobj=fileobj, tag=tag, rm=True)
        except BuildError as exc:
            raise ContainerError(
                f"Failed to build image for ros_distro '{config.ros_distro}': {exc}\n"
                f"Check that 'ros:{config.ros_distro}' is a valid tag on Docker Hub."
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
            reasons.append("image config changed (distro, rmw, or extra_apt_packages)")
        if labels.get(NETWORK_GROUP_LABEL) != config.network:
            reasons.append(
                f"network group changed ({labels.get(NETWORK_GROUP_LABEL)!r} -> {config.network!r})"
            )
        domain_id = self._resolve_domain_id(config)
        if labels.get(DOMAIN_ID_LABEL) != str(domain_id):
            reasons.append(
                f"domain_id changed ({labels.get(DOMAIN_ID_LABEL)!r} -> {domain_id!r})"
            )
        return DriftReport(drifted=bool(reasons), reasons=reasons)

    # -- domain id -------------------------------------------------------

    def _resolve_domain_id(self, config: RosmanConfig) -> int:
        if config.domain_id != "auto":
            return int(config.domain_id)
        return self.state.assign_domain_id(config.workspace_root)

    # -- create / start ----------------------------------------------------

    def create_container(self, config: RosmanConfig):
        uid, gid = host_uid_gid()
        config_hash = compute_config_hash(config, uid, gid)
        image_tag = self.ensure_image(config, config_hash)
        domain_id = self._resolve_domain_id(config)
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

        labels = {
            MANAGED_LABEL: "true",
            WORKSPACE_LABEL: str(config.workspace_root),
            DISTRO_LABEL: config.ros_distro,
            CONFIG_HASH_LABEL: config_hash,
            NETWORK_GROUP_LABEL: config.network,
            DOMAIN_ID_LABEL: str(domain_id),
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
        return True

    def rebuild(self, config: RosmanConfig):
        """Force-recreate the container (and its image, if config changed)."""
        self.remove(config)
        return self.create_container(config)
