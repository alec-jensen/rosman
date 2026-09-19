"""Thin wrapper around docker-py connection setup.

Everything else in rosman talks to the Docker SDK objects directly; this
module only exists so connection failures produce a clean rosman error
instead of a raw docker-py traceback.
"""

from __future__ import annotations

import docker
from docker.errors import DockerException

from rosman.errors import DockerUnavailableError

MANAGED_LABEL = "rosman.managed"
WORKSPACE_LABEL = "rosman.workspace"
DISTRO_LABEL = "rosman.distro"
CONFIG_HASH_LABEL = "rosman.config_hash"
NETWORK_GROUP_LABEL = "rosman.network_group"
DOMAIN_ID_LABEL = "rosman.domain_id"
RESTART_POLICY_LABEL = "rosman.restart_policy"


def get_client() -> docker.DockerClient:
    try:
        client = docker.from_env()
        client.ping()
    except DockerException as exc:
        raise DockerUnavailableError(
            "Could not connect to Docker. Make sure Docker is installed and running:\n"
            "  - Linux: `sudo systemctl status docker` (and that your user is in the "
            "`docker` group)\n"
            "  - Windows: make sure Docker Desktop is running with the WSL2 backend enabled\n"
            f"(underlying error: {exc})"
        ) from exc
    return client
