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
import shutil
import sys
from pathlib import Path

from rosman.config import RosmanConfig
from rosman.errors import RosmanError

CONTAINER_WORKSPACE_PATH = "/workspace"

RESERVED_COMMANDS = {"init", "up", "down", "status", "rebuild", "doctor", "shell", "help"}


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
    interactive = sys.stdin.isatty()
    flags = "-i" if interactive else ""
    flags += "t" if sys.stdout.isatty() else ""
    argv = [_docker_binary(), "exec"]
    if flags:
        argv.append(f"-{flags}")
    argv += ["-w", workdir, container, *command]
    return argv


def exec_in_container(container_name: str, workdir: str, command: list[str]) -> int:
    """Replace the current process with `docker exec` into the container,
    running `command` at `workdir`. Never returns on POSIX (os.execvp
    replaces the process image); returns the exit code on platforms where
    exec isn't available."""
    argv = _exec_argv(container_name, workdir, command)
    if hasattr(os, "execvp"):
        os.execvp(argv[0], argv)  # noqa: S606 - intentional, see module docstring
    import subprocess

    return subprocess.call(argv)


def dispatch_passthrough(args: list[str], container_name: str, workdir: str) -> int:
    """Forward a non-reserved `rosman <args>` call into the container.

    `rosman colcon build` -> `docker exec ... colcon build`
    anything else        -> `docker exec ... ros2 <args>`
    """
    if not args:
        raise RosmanError("No command given. Run `rosman --help` for usage.")
    if args[0] == "colcon":
        command = list(args)
    else:
        command = ["ros2", *args]
    return exec_in_container(container_name, workdir, command)


def shell_command(container_name: str, workdir: str, shell: str = "bash") -> int:
    return exec_in_container(container_name, workdir, [shell])
