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


def _extract_packages(line_tokens: list[str], marker_tokens: set[str]) -> list[str] | None:
    """If this line's tokens contain every token in `marker_tokens` plus
    "install", returns the non-flag tokens after "install" (the package
    names); otherwise None."""
    if not marker_tokens.issubset(line_tokens) or "install" not in line_tokens:
        return None
    idx = line_tokens.index("install")
    return [t for t in line_tokens[idx + 1 :] if not t.startswith("-")]


def parse_simulate_output(text: str) -> tuple[list[str], list[str]]:
    """Parses rosdep's own `--simulate` output. Two installer types have
    been confirmed against a real rosdep install, one package per line
    each (rosdep doesn't currently combine multiple packages onto one
    command line, but this handles that shape too in case a future
    version does):

        #[apt] Installation commands:
          sudo -H apt-get install -y ros-humble-example-interfaces

        #[pip] Installation commands:
          sudo -H --preserve-env=PIP_BREAK_SYSTEM_PACKAGES pip3 install -U Adafruit-ADS1x15

    Returns `(apt_packages, pip_packages)` -- a package declared via
    `exec_depend` can resolve to *either* installer depending on whether
    Ubuntu packages it for apt, and treating pip-resolved deps as if they
    were apt-resolved (or silently dropping them) previously made
    `rosman rosdep install` claim "nothing to install" for a workspace
    whose only unmet dependency was pip-only -- confirmed live against a
    real rosdep key (`adafruit-ads1x15-pip`) before this fix.
    """
    apt_packages: list[str] = []
    pip_packages: list[str] = []
    for line in text.splitlines():
        tokens = line.split()
        apt_match = _extract_packages(tokens, {"apt-get"})
        if apt_match is not None:
            apt_packages.extend(apt_match)
            continue
        pip_match = _extract_packages(tokens, {"pip3"}) or _extract_packages(tokens, {"pip"})
        if pip_match is not None:
            pip_packages.extend(pip_match)
    return sorted(set(apt_packages)), sorted(set(pip_packages))


def resolve_packages(
    container, extra_args: list[str]
) -> tuple[int, list[str], list[str], str]:
    """Dry-run rosdep to get the resolved apt/pip package lists without
    installing anything yet.

    Returns `(exit_code, apt_packages, pip_packages, output)` rather than
    just the package lists -- confirmed against a real container that an
    unresolvable rosdep key (a typo, or a package not in the rosdistro
    index) makes `--simulate` exit 1 with a clear error and no
    installation-command lines at all. Silently treating that the same as
    "nothing to install" would misreport a real failure as full success --
    the caller must check `exit_code` before trusting empty package lists.
    """
    command = _rosdep_command(extra_args, simulate=True)
    exit_code, output = container.exec_run(["bash", "-lc", command])
    text = output.decode(errors="replace")
    apt_packages, pip_packages = parse_simulate_output(text)
    return exit_code, apt_packages, pip_packages, text


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
