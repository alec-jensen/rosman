"""Cached login-shell environment for warm passthrough commands.

Every passthrough used to run ``bash -lc "ros2 ..."`` so the image's
``/etc/profile.d`` would source ROS (and the workspace overlay). That login
shell costs 200-400ms per command. The environment it produces only changes
when the container is recreated or the workspace is rebuilt, so capture it
once and hand it to ``docker exec -e`` instead, running the tool directly.

The cache is keyed on the container ID plus the mtime/size of the overlay's
``install/setup.bash`` files, which colcon rewrites on every build. Any
failure (capture error, unreadable cache) returns None and the caller falls
back to the login shell, so this is purely an optimisation.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from rosman.state import state_dir

_CAPTURE_TIMEOUT_SECONDS = 30
_SPLIT = b"\0\0ROSMAN-LOGIN-ENV\0\0"
# Set by the shell/exec itself and meaningless to carry over.
_IGNORED = {"PWD", "OLDPWD", "SHLVL", "_", "HOSTNAME", "TERM"}

_CAPTURE_SCRIPT = 'env -0; printf "\\0\\0ROSMAN-LOGIN-ENV\\0\\0"; bash -lc "env -0"'


def _cache_key(container_id: str, workspace_root: Path) -> str:
    parts = [container_id]
    for name in ("setup.bash", "local_setup.bash"):
        try:
            stat = (workspace_root / "install" / name).stat()
            parts.append(f"{stat.st_mtime_ns}:{stat.st_size}")
        except OSError:
            parts.append("-")
    return "|".join(parts)


def _cache_path(container_name: str) -> Path:
    return state_dir() / "env" / f"{container_name}.json"


def _parse(blob: bytes) -> dict[str, str]:
    result: dict[str, str] = {}
    for entry in blob.decode(errors="replace").split("\0"):
        key, sep, value = entry.partition("=")
        if sep and key:
            result[key] = value
    return result


def _capture(docker_bin: str, container_name: str) -> dict[str, str] | None:
    try:
        result = subprocess.run(
            [docker_bin, "exec", container_name, "bash", "-c", _CAPTURE_SCRIPT],
            capture_output=True,
            timeout=_CAPTURE_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0 or _SPLIT not in result.stdout:
        return None
    base_blob, login_blob = result.stdout.split(_SPLIT, 1)
    base, login = _parse(base_blob), _parse(login_blob)
    delta = {
        key: value
        for key, value in login.items()
        if key not in _IGNORED and base.get(key) != value
    }
    # Without ROS in the delta the capture is useless (broken profile, etc.).
    return delta if "AMENT_PREFIX_PATH" in delta or "PATH" in delta else None


def login_env(
    docker_bin: str, container_name: str, container_id: str, workspace_root: Path
) -> dict[str, str] | None:
    """Variables a login shell adds on top of the container's base env."""
    key = _cache_key(container_id, workspace_root)
    path = _cache_path(container_name)
    try:
        cached = json.loads(path.read_text())
        if cached.get("key") == key and isinstance(cached.get("env"), dict):
            return cached["env"]
    except (OSError, ValueError):
        pass
    env = _capture(docker_bin, container_name)
    if env is None:
        return None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"key": key, "env": env}))
        tmp.replace(path)
    except OSError:
        pass
    return env
