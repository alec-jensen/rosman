"""Linux X11 GUI passthrough and Windows/WSL2 detection (spec §8).

Linux native: bind-mount `/tmp/.X11-unix` and an XAuth cookie file generated
the same way `osrf/rocker`'s X11 extension does — `xauth nlist $DISPLAY`
with the raw cookie family byte masked to `ffff`, merged into a fresh xauth
file — so GUI apps like rviz2/rqt work without leaving `xhost +` open on the
host. Reusing rocker's exact approach rather than reinventing it, per spec.

Windows (WSL2 + WSLg): WSLg already forwards X11/Wayland into the WSL2
instance and points `$DISPLAY`/`$WAYLAND_DISPLAY` at sockets under
`/tmp/.X11-unix` and `/mnt/wslg` — no XAuth handling needed. rosman detects
WSL2 and only bind-mounts those existing sockets through untouched, per
spec §8's explicit instruction not to run the Linux X11 mounting logic
there.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


def is_wsl2() -> bool:
    try:
        with open("/proc/version") as f:
            return "microsoft" in f.read().lower()
    except OSError:
        return False


@dataclass
class GuiPassthrough:
    environment: dict[str, str] = field(default_factory=dict)
    volumes: dict[str, dict[str, str]] = field(default_factory=dict)


def _generate_xauth_cookie(display: str, cookie_path: Path) -> bool:
    """Write a fresh Xauthority file at `cookie_path` containing only the
    current display's cookie (masked family byte, exactly like rocker),
    so the whole host `~/.Xauthority` never has to be shared with the
    container. Returns False (and leaves no file behind) on any failure —
    GUI passthrough is a best-effort convenience, not something that
    should block `rosman up` if `xauth` isn't installed."""
    try:
        nlist = subprocess.run(
            ["xauth", "nlist", display],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        ).stdout
        if not nlist.strip():
            return False
        cookie = "ffff" + nlist[4:]
        cookie_path.parent.mkdir(parents=True, exist_ok=True)
        if cookie_path.exists():
            cookie_path.unlink()
        subprocess.run(
            ["xauth", "-f", str(cookie_path), "nmerge", "-"],
            input=cookie,
            text=True,
            capture_output=True,
            check=True,
            timeout=5,
        )
        cookie_path.chmod(0o644)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def gui_passthrough(container_home: str, cookie_path: Path) -> GuiPassthrough | None:
    """Build the env/volumes needed for GUI apps (rviz2/rqt/Gazebo) to work,
    or None if there's no $DISPLAY to forward (headless host/session).

    `cookie_path` is a stable, rosman-owned location to (re)write the
    generated XAuth cookie on Linux; callers should key it by workspace so
    it persists for the life of the container rather than living in a
    tempfile that could vanish from under a running container.
    """
    display = os.environ.get("DISPLAY")
    if not display:
        return None

    if is_wsl2():
        result = GuiPassthrough(environment={"DISPLAY": display})
        if os.environ.get("WAYLAND_DISPLAY"):
            result.environment["WAYLAND_DISPLAY"] = os.environ["WAYLAND_DISPLAY"]
        if os.path.isdir("/tmp/.X11-unix"):
            result.volumes["/tmp/.X11-unix"] = {"bind": "/tmp/.X11-unix", "mode": "rw"}
        if os.path.isdir("/mnt/wslg"):
            result.volumes["/mnt/wslg"] = {"bind": "/mnt/wslg", "mode": "rw"}
        return result

    result = GuiPassthrough(environment={"DISPLAY": display})
    if os.path.isdir("/tmp/.X11-unix"):
        result.volumes["/tmp/.X11-unix"] = {"bind": "/tmp/.X11-unix", "mode": "rw"}
    if _generate_xauth_cookie(display, cookie_path):
        container_xauth = f"{container_home}/.Xauthority"
        result.volumes[str(cookie_path)] = {"bind": container_xauth, "mode": "ro"}
        result.environment["XAUTHORITY"] = container_xauth
    return result
