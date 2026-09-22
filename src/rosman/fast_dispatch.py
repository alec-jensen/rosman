"""Fast path for commands targeting an already-running, current container.

This is the common interactive case. One Docker CLI inspect provides status
and every drift label; when they all match, exec directly into the container
without importing Rich or docker-py. Missing/stopped/drifted/error cases fall
back to the full lifecycle manager, preserving auto-start, rebuild prompts,
and detailed errors.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys

from rosman.config import resolve_config
from rosman.dispatch import dispatch_passthrough, translate_cwd
from rosman.docker_labels import (
    CONFIG_HASH_LABEL,
    DISTRO_LABEL,
    DOMAIN_ID_LABEL,
    NETWORK_GROUP_LABEL,
    NETWORK_MODE_LABEL,
    REMOTE_PEERS_LABEL,
    RESTART_POLICY_LABEL,
)
from rosman.errors import RosmanError
from rosman.naming import container_name
from rosman.runtime_config import compute_config_hash, resolve_network_mode
from rosman.state import RosmanState

_INSPECT_TIMEOUT_SECONDS = 2
_START_TIMEOUT_SECONDS = 30
_INSPECT_FORMAT = "{{.State.Running}}\n{{json .Config.Labels}}"


def _inspect(docker_bin: str, name: str) -> tuple[bool, dict[str, str]] | None:
    try:
        result = subprocess.run(
            [docker_bin, "inspect", "--format", _INSPECT_FORMAT, name],
            capture_output=True,
            text=True,
            timeout=_INSPECT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    try:
        running_text, labels_text = result.stdout.split("\n", 1)
        labels = json.loads(labels_text)
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(labels, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in labels.items()
    ):
        return None
    return running_text == "true", labels


def _current_labels(config, state: RosmanState) -> dict[str, str]:
    domain_id = (
        int(config.domain_id)
        if config.domain_id != "auto"
        else state.assign_domain_id(config.workspace_root)
    )
    return {
        DISTRO_LABEL: config.ros_distro,
        CONFIG_HASH_LABEL: compute_config_hash(config),
        NETWORK_GROUP_LABEL: config.network,
        NETWORK_MODE_LABEL: resolve_network_mode(config),
        DOMAIN_ID_LABEL: str(domain_id),
        RESTART_POLICY_LABEL: config.restart_policy,
        REMOTE_PEERS_LABEL: json.dumps(config.remote_peers, separators=(",", ":")),
    }


def _start(docker_bin: str, name: str) -> bool:
    try:
        result = subprocess.run(
            [docker_bin, "start", name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=_START_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _maybe_update(state: RosmanState) -> None:
    if not sys.stderr.isatty():
        return
    try:
        from rosman import __version__
        from rosman.update_check import pending_notice, schedule_update_check

        notice = pending_notice(state, __version__)
        if notice:
            print(notice, file=sys.stderr)
        schedule_update_check(state)
    except Exception:
        pass


def try_fast_passthrough(args: list[str]) -> int | None:
    """Execute a warm passthrough, or return None for full-CLI fallback."""
    try:
        config = resolve_config()
    except RosmanError:
        return None
    # Remote peers can change without changing the image/container labels.
    # The normal lifecycle path refreshes the bind-mounted CycloneDDS file;
    # skipping it here would silently leave a changed peer list stale.
    if config.remote_peers:
        return None
    docker_bin = shutil.which("docker")
    if docker_bin is None:
        return None
    name = container_name(config.workspace_root)
    inspected = _inspect(docker_bin, name)
    if inspected is None:
        return None
    running, labels = inspected
    state = RosmanState.load()
    expected = _current_labels(config, state)
    if any(labels.get(key) != value for key, value in expected.items()):
        return None
    if not running:
        print(f"Starting rosman container for this workspace ({config.ros_distro})...")
        if not _start(docker_bin, name):
            return None

    _maybe_update(state)
    return dispatch_passthrough(args, name, translate_cwd(config))
