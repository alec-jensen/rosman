"""`rosman` entrypoint.

Reserved rosman subcommands (init/up/down/status/rebuild/doctor/shell) are
handled here directly. Anything else is assumed to be a `ros2`/`colcon`
passthrough call and is forwarded verbatim into the workspace container —
rosman does not reimplement any part of the ros2 CLI surface (spec §2.3).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from rich.console import Console
from rich.table import Table

from rosman import __version__
from rosman.completion import BASH_SCRIPT, ZSH_SCRIPT
from rosman.completion import complete as complete_words
from rosman.config import KNOWN_ROS_DISTROS, RosmanConfig, resolve_config
from rosman.dispatch import RESERVED_COMMANDS, dispatch_passthrough, shell_command, translate_cwd
from rosman.errors import RosmanError
from rosman.progress import RichReporter
from rosman.state import RosmanState
from rosman.update_check import check_for_update, pending_notice

if TYPE_CHECKING:
    # Deferred at runtime -- see main()'s `__complete` fast path, which
    # must not pay for importing docker-py (measured ~100ms) on every
    # keystroke of tab-completion. Each cmd_* below that actually talks to
    # Docker imports these locally instead.
    from rosman.lifecycle import ContainerManager, ImageResult

console = Console()
err_console = Console(stderr=True)

INIT_TEMPLATE = """\
ros_distro: {distro}          # required -- any distro with an official ros:<tag> image
rmw_implementation: cyclonedds  # default; cyclonedds is the only supported path today
domain_id: auto               # "auto" assigns + persists one per project; or an explicit int
network: default               # Docker network group; shared projects can discover each other
gpu: false                    # true enables nvidia-container-toolkit passthrough
devices: []                   # e.g. ["/dev/ttyUSB0"]
workspace_dir: .              # path (relative to this file) mounted as the container workspace root
extra_apt_packages: []        # optional list, installed into the image on first build
restart_policy: "no"          # docker restart policy; "no" (default) requires explicit `rosman up`
                               # after a host reboot -- see docs/spec.md #6 for why

# Per-machine overrides (e.g. a device that's at a different path on your
# machine) go in a gitignored rosman.local.yml next to this file -- any
# field set there replaces the value here. See README.md.
"""

GITIGNORE_ENTRY = "rosman.local.yml"


def _ensure_gitignored(target_dir: Path) -> None:
    gitignore_path = target_dir / ".gitignore"
    if not gitignore_path.is_file():
        return
    existing = gitignore_path.read_text()
    if GITIGNORE_ENTRY in existing:
        return
    separator = "" if existing.endswith("\n") or not existing else "\n"
    gitignore_path.write_text(f"{existing}{separator}{GITIGNORE_ENTRY}\n")
    console.print(f"Added {GITIGNORE_ENTRY!r} to {gitignore_path}")


def cmd_init(args: argparse.Namespace) -> int:
    target_dir = Path(args.path).resolve()
    config_path = target_dir / "rosman.yml"
    if config_path.exists() and not args.force:
        err_console.print(f"[red]{config_path} already exists.[/red] Use --force to overwrite.")
        return 1
    if args.distro not in KNOWN_ROS_DISTROS - {"noetic"}:
        err_console.print(
            f"[red]Unknown ros_distro '{args.distro}'.[/red] Expected one of: "
            f"{', '.join(sorted(KNOWN_ROS_DISTROS - {'noetic'}))}"
        )
        return 1
    config_path.write_text(INIT_TEMPLATE.format(distro=args.distro))
    console.print(f"[green]Created {config_path}[/green]")
    _ensure_gitignored(target_dir)
    return 0


def _load_config_or_exit() -> RosmanConfig:
    try:
        return resolve_config()
    except RosmanError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise SystemExit(1) from None


def cmd_up(args: argparse.Namespace) -> int:
    from rosman.docker_client import get_client
    from rosman.lifecycle import ContainerManager

    config = _load_config_or_exit()
    client = get_client()
    state = RosmanState.load()
    manager = ContainerManager(client, state)

    existing = manager.find_container(config)
    if existing is not None:
        existing.reload()
        drift = manager.detect_drift(existing, config)
        if drift.drifted and not args.force:
            err_console.print(
                f"[yellow]Container config has drifted from {config.config_path}:[/yellow]"
            )
            for reason in drift.reasons:
                err_console.print(f"  - {reason}")
            err_console.print("Run `rosman rebuild` to recreate it, or `rosman up --force`.")
            return 1

    console.print(
        f"Starting rosman container for this workspace ({config.ros_distro})..."
    )
    container, created = manager.ensure_running(config, reporter=RichReporter(console))
    if created and manager.last_image_result is not None:
        _print_image_source(config, manager.last_image_result)
    verb = "Started" if not created else "Created and started"
    console.print(f"[green]{verb}[/green] {container.name}")
    return 0


def _print_image_source(config: RosmanConfig, result: ImageResult) -> None:
    if not config.registry_image:
        return
    if result.source == "pulled":
        console.print(f"[green]Pulled[/green] shared image {result.tag}")
    elif result.source == "built":
        console.print(
            f"Built image locally (not yet on {config.registry_image}) -- "
            f"run `rosman push` to share it with your team."
        )


def cmd_down(args: argparse.Namespace) -> int:
    from rosman.docker_client import get_client
    from rosman.lifecycle import ContainerManager

    config = _load_config_or_exit()
    client = get_client()
    state = RosmanState.load()
    manager = ContainerManager(client, state)

    if args.remove:
        removed = manager.remove(config)
        if removed:
            console.print(f"[green]Removed[/green] container for {config.project_name}")
        else:
            console.print("No container to remove.")
        return 0

    stopped = manager.stop(config)
    if stopped:
        console.print(f"[green]Stopped[/green] container for {config.project_name}")
    else:
        console.print("No running container for this workspace.")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    from rosman.docker_client import (
        DISTRO_LABEL,
        DOMAIN_ID_LABEL,
        NETWORK_GROUP_LABEL,
        WORKSPACE_LABEL,
        get_client,
    )
    from rosman.lifecycle import ContainerManager

    client = get_client()
    state = RosmanState.load()
    manager = ContainerManager(client, state)

    containers = manager.list_managed()
    if not containers:
        console.print("No rosman-managed containers found.")
        return 0

    table = Table()
    table.add_column("name")
    table.add_column("status")
    table.add_column("distro")
    table.add_column("network")
    table.add_column("domain id")
    table.add_column("workspace")
    for container in containers:
        labels = container.labels or {}
        table.add_row(
            container.name,
            container.status,
            labels.get(DISTRO_LABEL, "?"),
            labels.get(NETWORK_GROUP_LABEL, "?"),
            labels.get(DOMAIN_ID_LABEL, "?"),
            labels.get(WORKSPACE_LABEL, "?"),
        )
    console.print(table)
    return 0


def cmd_rebuild(args: argparse.Namespace) -> int:
    from rosman.docker_client import get_client
    from rosman.lifecycle import ContainerManager

    config = _load_config_or_exit()
    client = get_client()
    state = RosmanState.load()
    manager = ContainerManager(client, state)

    if not args.yes:
        try:
            answer = input(
                f"This will destroy and recreate the container for {config.project_name} "
                "(build/install/log volumes are preserved). Continue? [y/N] "
            )
        except EOFError:
            err_console.print(
                "[red]No input available to confirm.[/red] Pass --yes/-y to rebuild "
                "non-interactively."
            )
            return 1
        if answer.strip().lower() not in ("y", "yes"):
            console.print("Aborted.")
            return 1

    console.print(f"Rebuilding container for this workspace ({config.ros_distro})...")
    container = manager.rebuild(config, reporter=RichReporter(console))
    console.print(f"[green]Rebuilt[/green] {container.name}")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    from rosman.doctor import run_checks

    config = _load_config_or_exit()
    checks = run_checks(config, network_check=args.network_check)
    ok = True
    for check in checks:
        icon = "[green]OK[/green]  " if check.ok else "[red]FAIL[/red]"
        console.print(f"{icon} {check.name}: {check.detail}")
        ok = ok and check.ok
    return 0 if ok else 1


def _ensure_running_with_notice(manager: ContainerManager, config: RosmanConfig):
    """Like ContainerManager.ensure_running, but prints the "starting" notice
    *before* a slow first-time build/create, not after -- otherwise an
    implicit auto-start on a passthrough/shell command looks like a hang.
    """
    container = manager.find_container(config)
    if container is None:
        console.print(
            f"Starting rosman container for this workspace ({config.ros_distro})..."
        )
        return manager.create_container(config, reporter=RichReporter(console)), True
    container.reload()
    if container.status != "running":
        container.start()
    return container, False


def cmd_shell(args: argparse.Namespace) -> int:
    from rosman.docker_client import get_client
    from rosman.lifecycle import ContainerManager

    config = _load_config_or_exit()
    client = get_client()
    state = RosmanState.load()
    manager = ContainerManager(client, state)
    container, _ = _ensure_running_with_notice(manager, config)
    workdir = translate_cwd(config)
    return shell_command(container.name, workdir, shell=args.shell)


def cmd_push(args: argparse.Namespace) -> int:
    from rosman.docker_client import get_client
    from rosman.lifecycle import ContainerManager

    config = _load_config_or_exit()
    if config.registry_image:
        console.print(f"Pushing image to {config.registry_image}...")
    client = get_client()
    state = RosmanState.load()
    manager = ContainerManager(client, state)
    tag = manager.push_image(config, reporter=RichReporter(console))
    console.print(f"[green]Pushed[/green] {tag}")
    return 0


def cmd_rosdep_install(extra_args: list[str]) -> int:
    """`rosman rosdep install` -- the one rosdep subcommand that isn't
    plain passthrough (see rosdep.py's module docstring for why): resolves
    apt-level deps declared by workspace `src/` packages, installs them
    into the running container immediately, and writes rosman.lock so
    they're baked into the image on the next `rosman rebuild` and shared
    with the rest of the team via git.
    """
    from rosman.docker_client import get_client
    from rosman.lifecycle import ContainerManager
    from rosman.rosdep import install_packages, resolve_packages

    config = _load_config_or_exit()
    client = get_client()
    state = RosmanState.load()
    manager = ContainerManager(client, state)
    container, _ = _ensure_running_with_notice(manager, config)

    console.print("Resolving dependencies declared under src/ via rosdep...")
    resolve_exit_code, packages, resolve_output = resolve_packages(container, extra_args)
    if resolve_exit_code != 0:
        err_console.print(
            f"[red]rosdep could not resolve dependencies:[/red]\n{resolve_output}"
        )
        return resolve_exit_code
    if not packages:
        console.print("Nothing to install -- all declared dependencies are already satisfied.")
        return 0

    console.print(f"Installing: {', '.join(packages)}")
    exit_code, output = install_packages(container, extra_args)
    if exit_code != 0:
        err_console.print(f"[red]rosdep install failed inside the container:[/red]\n{output}")
        return exit_code

    from rosman.config import write_lock

    write_lock(config.config_path, config.ros_distro, packages)
    console.print(
        f"[green]Wrote[/green] {len(packages)} package(s) to rosman.lock. "
        "Run `rosman rebuild` to bake them into the image (and check "
        "rosman.lock into git so your team gets them too)."
    )
    return 0


def cmd_completion(args: argparse.Namespace) -> int:
    script = BASH_SCRIPT if args.shell == "bash" else ZSH_SCRIPT
    print(script, end="")
    return 0


def cmd_complete(args: argparse.Namespace) -> int:
    """Backs the installed shell completion function (`rosman completion
    bash`/`zsh`) -- never a human-facing command. Must never raise, print
    anything but candidates, or start a container: Tab is not `rosman up`.
    Deliberately avoids docker-py entirely (see completion.py's docstring)
    since this runs on every keystroke of tab-completion.
    """
    try:
        config = resolve_config()
        for candidate in complete_words(config, args.words):
            print(candidate)
    except Exception:
        pass
    return 0


def cmd_passthrough(args: list[str]) -> int:
    from rosman.docker_client import get_client
    from rosman.lifecycle import ContainerManager

    config = _load_config_or_exit()
    client = get_client()
    state = RosmanState.load()
    manager = ContainerManager(client, state)
    container, _ = _ensure_running_with_notice(manager, config)
    workdir = translate_cwd(config)
    return dispatch_passthrough(args, container.name, workdir)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rosman",
        description=(
            "Run ROS 2 anywhere without installing it natively. Any command that isn't "
            "one of rosman's own subcommands below is forwarded to `ros2`/`colcon` inside "
            "this workspace's container."
        ),
    )
    parser.add_argument("--version", action="version", version=f"rosman {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    p_init = subparsers.add_parser("init", help="Scaffold a rosman.yml in the current directory")
    p_init.add_argument("--distro", default="humble", help="ROS 2 distro (default: humble)")
    p_init.add_argument("--path", default=".", help="Directory to write rosman.yml into")
    p_init.add_argument("--force", action="store_true", help="Overwrite an existing rosman.yml")
    p_init.set_defaults(func=cmd_init)

    p_up = subparsers.add_parser("up", help="Start (or create) the workspace container")
    p_up.add_argument(
        "--force", action="store_true", help="Start even if the container has config drift"
    )
    p_up.set_defaults(func=cmd_up)

    p_down = subparsers.add_parser("down", help="Stop the workspace container")
    p_down.add_argument(
        "--remove", action="store_true", help="Remove the container instead of just stopping it"
    )
    p_down.set_defaults(func=cmd_down)

    p_status = subparsers.add_parser("status", help="List rosman-managed containers")
    p_status.set_defaults(func=cmd_status)

    p_rebuild = subparsers.add_parser("rebuild", help="Force-recreate the workspace container")
    p_rebuild.add_argument("-y", "--yes", action="store_true", help="Skip the confirmation prompt")
    p_rebuild.set_defaults(func=cmd_rebuild)

    p_doctor = subparsers.add_parser("doctor", help="Run environment/config sanity checks")
    p_doctor.add_argument(
        "--network-check",
        action="store_true",
        help="Also spin up two ephemeral containers and verify a pub/sub round trip "
        "over the generated CycloneDDS peers (slower; may pull the base ros image)",
    )
    p_doctor.set_defaults(func=cmd_doctor)

    p_shell = subparsers.add_parser("shell", help="Open an interactive shell in the container")
    p_shell.add_argument("--shell", default="bash", help="Shell to run (default: bash)")
    p_shell.set_defaults(func=cmd_shell)

    p_push = subparsers.add_parser(
        "push", help="Build (if needed) and push the image to 'registry_image' for your team"
    )
    p_push.set_defaults(func=cmd_push)

    p_completion = subparsers.add_parser(
        "completion", help="Print a shell tab-completion script to eval in your rc file"
    )
    p_completion.add_argument("shell", choices=["bash", "zsh"])
    p_completion.set_defaults(func=cmd_completion)

    p_complete = subparsers.add_parser(
        "__complete", help=argparse.SUPPRESS  # internal -- backs the completion script
    )
    p_complete.add_argument("words", nargs="*")
    p_complete.set_defaults(func=cmd_complete)

    def _print_help(_args: argparse.Namespace) -> int:
        parser.print_help()
        return 0

    p_help = subparsers.add_parser("help", help="Show this help message")
    p_help.set_defaults(func=_print_help)

    return parser


def _maybe_show_update_notice() -> None:
    """Best-effort only: must never affect the command actually running,
    on failure or otherwise. Printed *before* dispatch, not after -- most
    reserved commands (shell, passthrough) replace this process outright
    via os.execvp on POSIX and never return to Python, so "after" simply
    wouldn't run for them at all. Gated on stderr being a real terminal so
    scripted/CI usage never sees it, and throttled independently of the
    (also throttled) network check itself -- see update_check.py."""
    try:
        if not sys.stderr.isatty():
            return
        state = RosmanState.load()
        check_for_update(state)
        notice = pending_notice(state, __version__)
        if notice:
            err_console.print(f"[yellow]{notice}[/yellow]")
    except Exception:
        pass


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv

    if argv and argv[0] == "__complete":
        # Kept fully separate from dispatch below, on purpose: this fires
        # on every keystroke of tab-completion, so it must never pay for
        # importing docker-py (measured ~100ms) the way every other
        # command legitimately does -- no update-notice check, no
        # `DockerException` import, no argparse subparser construction.
        # `cmd_complete` already catches everything itself and never
        # raises, so none of that machinery is needed here anyway.
        return cmd_complete(argparse.Namespace(words=argv[1:]))

    _maybe_show_update_notice()

    from docker.errors import DockerException

    if argv[:2] == ["rosdep", "install"]:
        # The one rosdep subcommand that isn't plain passthrough -- see
        # rosdep.py's module docstring. Every other `rosman rosdep <...>`
        # falls through to the generic passthrough branch below, exactly
        # like `colcon`.
        try:
            return cmd_rosdep_install(argv[2:])
        except RosmanError as exc:
            err_console.print(f"[red]{exc}[/red]")
            return 1
        except DockerException as exc:
            err_console.print(f"[red]Docker error:[/red] {exc}")
            return 1

    if argv and argv[0] not in RESERVED_COMMANDS and not argv[0].startswith("-"):
        try:
            return cmd_passthrough(argv)
        except RosmanError as exc:
            err_console.print(f"[red]{exc}[/red]")
            return 1
        except DockerException as exc:
            err_console.print(f"[red]Docker error:[/red] {exc}")
            return 1

    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    try:
        return args.func(args)
    except RosmanError as exc:
        err_console.print(f"[red]{exc}[/red]")
        return 1
    except DockerException as exc:
        # Safety net: anything from docker-py that a specific code path
        # didn't already wrap into a clean RosmanError (a name conflict, an
        # invalid device path, "could not select device driver" for a
        # missing GPU runtime, etc.) still gets a one-line message instead
        # of a raw traceback.
        err_console.print(f"[red]Docker error:[/red] {exc}")
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
