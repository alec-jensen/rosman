"""Command dispatcher: working-directory translation and the actual
`docker exec` for passthrough `ros2`/`colcon` calls and `rosman shell`.

Every other module talks to Docker through docker-py so state checks get
clean error handling. This module is the one deliberate exception: docker-py
has no real equivalent of `docker exec -it` — there's no supported way to
hand it a live TTY wired straight to the current process's stdin/stdout/
stderr — so interactive passthrough shells out to the `docker` CLI itself
via subprocess and simply execs into it (replacing this process on POSIX),
matching real terminal behavior (raw mode, window resizes, Ctrl-C) exactly
because it *is* the same code path `docker exec -it` always uses.
"""

from __future__ import annotations

import os
import shlex
import shutil
import sys
from pathlib import Path

from rosman.config import RosmanConfig
from rosman.errors import RosmanError

CONTAINER_WORKSPACE_PATH = "/workspace"

RESERVED_COMMANDS = {
    "init",
    "up",
    "down",
    "status",
    "rebuild",
    "doctor",
    "shell",
    "push",
    "completion",
    "__complete",
    "help",
}


def translate_cwd(config: RosmanConfig, host_cwd: Path | None = None) -> str:
    """Map the host cwd to the equivalent path inside the container, under
    the mounted workspace root. Falls back to the workspace root (with a
    warning left to the caller) if cwd isn't inside the workspace."""
    host_cwd = (host_cwd or Path.cwd()).resolve()
    workspace_root = config.workspace_root
    try:
        relative = host_cwd.relative_to(workspace_root)
    except ValueError:
        return CONTAINER_WORKSPACE_PATH
    if str(relative) == ".":
        return CONTAINER_WORKSPACE_PATH
    return f"{CONTAINER_WORKSPACE_PATH}/{relative.as_posix()}"


def _docker_binary() -> str:
    binary = shutil.which("docker")
    if not binary:
        raise RosmanError("The `docker` CLI was not found on PATH (needed for interactive exec).")
    return binary


def _exec_argv(container: str, workdir: str, command: list[str]) -> list[str]:
    # -i (keep stdin open) is safe and correct even when stdin isn't a TTY --
    # e.g. `some_script | rosman shell` piping commands in -- and dropping it
    # in that case silently produces a `docker exec` with no stdin attached
    # at all, discarding the piped input entirely. -t (allocate a pseudo-TTY)
    # is the one that actually requires a real terminal on both ends, and
    # docker errors ("the input device is not a TTY") if forced without one.
    flags = "-i"
    if sys.stdin.isatty() and sys.stdout.isatty():
        flags += "t"
    argv = [_docker_binary(), "exec", flags]
    argv += ["-w", workdir, container, *command]
    return argv


def exec_in_container(container_name: str, workdir: str, command: list[str]) -> int:
    """Replace the current process with `docker exec` into the container,
    running `command` at `workdir`. Never returns on POSIX (os.execvp
    replaces the process image); returns the exit code on Windows, where
    `os.execvp` exists but isn't a real process replacement.

    Windows *has* `os.execvp` (so `hasattr(os, "execvp")` is true there too),
    but it's emulated via spawn-then-exit rather than true exec, and its
    argv-to-command-line quoting is unreliable -- e.g. it mangles a docker.exe
    path containing a space ("C:\\Program Files\\Docker\\...") badly enough
    that the child sees a corrupted argv and `docker exec` fails with
    "unknown shorthand flag". `subprocess.call` quotes correctly there via
    `subprocess.list2cmdline`, so Windows always goes through that branch."""
    argv = _exec_argv(container_name, workdir, command)
    if os.name == "posix":
        os.execvp(argv[0], argv)  # noqa: S606 - intentional, see module docstring
    import subprocess

    return subprocess.call(argv)


def dispatch_passthrough(args: list[str], container_name: str, workdir: str) -> int:
    """Forward a non-reserved `rosman <args>` call into the container.

    `rosman colcon build`  -> `docker exec ... colcon build`
    `rosman rosdep update` -> `docker exec ... rosdep update`
    anything else          -> `docker exec ... ros2 <args>`

    (`rosman rosdep install` specifically is intercepted earlier, in
    cli.main, before it ever reaches here -- it needs to also write
    rosman.lock, which a process-replacing exec can't do anything after.
    Every other rosdep subcommand is plain passthrough like this.)

    Routed through `bash -lc "<command>"` (a login shell), not run as a
    bare argv, because `ros2`/`colcon`/`rosdep` only end up on $PATH once
    `/opt/ros/<distro>/setup.bash` is sourced -- the image bakes that into
    `/etc/profile.d/rosman-ros.sh`, which only login shells read. A plain
    `docker exec container ros2 ...` gets a fresh, un-sourced environment
    and fails with "ros2: executable file not found in $PATH".
    """
    if not args:
        raise RosmanError("No command given. Run `rosman --help` for usage.")
    if args[0] in ("colcon", "rosdep"):
        command = list(args)
    else:
        command = ["ros2", *args]
    return exec_in_container(container_name, workdir, ["bash", "-lc", shlex.join(command)])


def shell_command(container_name: str, workdir: str, shell: str = "bash") -> int:
    """Drop into an interactive login shell, so the same `/etc/profile.d`
    ROS sourcing that `dispatch_passthrough` relies on applies here too."""
    return exec_in_container(container_name, workdir, [shell, "-l"])
