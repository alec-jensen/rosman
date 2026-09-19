"""`rosman rosdep install` -- resolves and installs the apt dependencies
declared by source packages in a workspace's `src/`, and records the
resolved list in `rosman.lock` so it's reproducible and shareable rather
than a one-off change to a single running container.

Deliberately not plain passthrough (unlike every other `rosdep` subcommand,
which forwards verbatim like `colcon` does -- see dispatch.py): writing
rosman.lock requires running more Python *after* the in-container command
finishes, which a process-replacing `docker exec` (how normal passthrough
works) structurally cannot do. So this specific subcommand is intercepted
in `cli.main` before it ever reaches passthrough dispatch, and instead
orchestrates two docker-py `exec_run` calls (like `doctor.py`'s network
round trip does) -- one dry run to resolve the package list cleanly, one
real install so the current container is usable immediately without
waiting for a rebuild.
"""

from __future__ import annotations

from rosman.dispatch import CONTAINER_WORKSPACE_PATH

SRC_PATH = f"{CONTAINER_WORKSPACE_PATH}/src"


def _rosdep_command(extra_args: list[str], simulate: bool) -> str:
    flags = "--simulate" if simulate else ""
    extra = " ".join(extra_args)
    parts = [
        "rosdep install",
        f"--from-paths {SRC_PATH}",
        "--ignore-src -y",
        flags,
        extra,
    ]
    return " ".join(p for p in parts if p)


def parse_simulate_output(text: str) -> list[str]:
    """Parses rosdep's own `--simulate` output, e.g.:

        #[apt] Installation commands:
          sudo -H apt-get install -y ros-humble-example-interfaces
          sudo -H apt-get install -y ros-humble-turtlesim

    (Verified against a real rosdep install, one package per line --
    doesn't currently combine multiple packages onto one command line, but
    this handles that shape too in case a future rosdep version does.)
    """
    packages: list[str] = []
    for line in text.splitlines():
        tokens = line.split()
        if "apt-get" not in tokens or "install" not in tokens:
            continue
        idx = tokens.index("install")
        for token in tokens[idx + 1 :]:
            if not token.startswith("-"):
                packages.append(token)
    return sorted(set(packages))


def resolve_packages(container, extra_args: list[str]) -> list[str]:
    """Dry-run rosdep to get the resolved apt package list without
    installing anything yet."""
    command = _rosdep_command(extra_args, simulate=True)
    _exit_code, output = container.exec_run(["bash", "-lc", command])
    return parse_simulate_output(output.decode(errors="replace"))


def install_packages(container, extra_args: list[str]) -> tuple[int, str]:
    """The real install, for the currently running container -- separate
    from the image build, which only picks this up on the next `rosman
    rebuild` once rosman.lock has been written.

    `sudo apt-get update` first: the image strips `/var/lib/apt/lists/*`
    after its own build to save space, so a running container has no
    cached package index at all -- rosdep's apt installer doesn't run
    `apt-get update` itself before installing (confirmed against a real
    container: it fails with "Unable to locate package" without this,
    even though the same package name resolved correctly under
    `--simulate`, which never touches apt at all).
    """
    command = f"sudo apt-get update && {_rosdep_command(extra_args, simulate=False)}"
    exit_code, output = container.exec_run(["bash", "-lc", command])
    return exit_code, output.decode(errors="replace")
