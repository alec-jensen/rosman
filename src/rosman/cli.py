"""`rosman` entrypoint.

Reserved rosman subcommands (init/up/down/status/rebuild/doctor/shell) are
handled here directly. Anything else is assumed to be a `ros2`/`colcon`
passthrough call and is forwarded verbatim into the workspace container —
rosman does not reimplement any part of the ros2 CLI surface (spec §2.3).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
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
from rosman.update_check import pending_notice, schedule_update_check

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
restart_policy: "no"          # do not auto-start with Docker after a reboot;
                               # the next rosman command starts this workspace on demand

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
    from rosman.docker_client import get_client
    from rosman.docker_labels import (
        DISTRO_LABEL,
        DOMAIN_ID_LABEL,
        NETWORK_GROUP_LABEL,
        WORKSPACE_LABEL,
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


def cmd_config(args: argparse.Namespace) -> int:
    """Prints the fully *resolved* effective config for this workspace --
    distinct from `rosman doctor` (a broader health-check bundle that
    touches the Docker daemon): this is pure config resolution, showing
    what "auto" values (`domain_id`, `network_mode`) actually resolved to,
    without needing to piece it together from doctor/status output."""
    from rosman.docker_client import get_client
    from rosman.lifecycle import ContainerManager, compute_config_hash, resolve_network_mode
    from rosman.naming import container_name, image_name

    config = _load_config_or_exit()
    state = RosmanState.load()
    domain_id_display = str(config.domain_id)
    if config.domain_id == "auto":
        # resolve_domain_id assigns+persists on first call, so this must
        # not run just to *display* a value -- peek at persisted state
        # instead, falling back to "not yet assigned" rather than
        # side-effecting an assignment from a read-only command. Goes
        # through state.get_project (not state.projects directly) so the
        # workspace-path-to-key hashing stays owned by state.py's own
        # _key(), rather than a second, easy-to-drift copy of that logic
        # living here too.
        assigned = state.get_project(config.workspace_root)
        domain_id_display = (
            f"{assigned.domain_id} (auto-assigned)"
            if assigned.domain_id is not None
            else "auto (not yet assigned -- assigned on first `rosman up`)"
        )

    network_mode = resolve_network_mode(config)
    network_mode_display = (
        f"{network_mode} (auto-detected)" if config.network_mode == "auto" else network_mode
    )

    table = Table(show_header=False)
    table.add_column("field", style="bold")
    table.add_column("value")
    table.add_row("config file", str(config.config_path))
    table.add_row("ros_distro", config.ros_distro)
    table.add_row("rmw_implementation", config.rmw_implementation)
    table.add_row("domain_id", domain_id_display)
    table.add_row("network_mode", network_mode_display)
    if network_mode != "host":
        table.add_row("network group", config.network)
    table.add_row("gpu", str(config.gpu))
    if config.devices:
        table.add_row("devices", ", ".join(config.devices))
    table.add_row("workspace_root", str(config.workspace_root))
    table.add_row("restart_policy", config.restart_policy)
    if config.base_image:
        table.add_row("base_image", config.base_image)
    if config.setup_script:
        table.add_row("setup_script", config.setup_script)
    if config.registry_image:
        table.add_row("registry_image", config.registry_image)
    if config.remote_peers:
        table.add_row("remote_peers", ", ".join(config.remote_peers))
    if config.ports:
        table.add_row("ports", ", ".join(config.ports))
    if config.extra_apt_packages:
        table.add_row("extra_apt_packages", ", ".join(config.extra_apt_packages))
    if config.locked_apt_packages:
        table.add_row("locked_apt_packages (rosman.lock)", ", ".join(config.locked_apt_packages))
    if config.locked_pip_packages:
        table.add_row("locked_pip_packages (rosman.lock)", ", ".join(config.locked_pip_packages))

    config_hash = compute_config_hash(config)
    image_tag = image_name(
        config.workspace_root, config.ros_distro, config_hash, config.registry_image
    )
    table.add_row("image tag", image_tag)
    table.add_row("container name", container_name(config.workspace_root))

    try:
        client = get_client()
        manager = ContainerManager(client, state)
        container = manager.find_container(config)
        if container is not None:
            container.reload()
            table.add_row("container status", container.status)
    except RosmanError:
        pass  # config-only introspection shouldn't fail just because Docker isn't reachable

    console.print(table)
    return 0


def _format_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f}{unit}" if unit != "B" else f"{int(size)}B"
        size /= 1024
    return f"{size:.1f}GB"


def cmd_prune(args: argparse.Namespace) -> int:
    """Removes old rosman-built images not needed by a container or as a
    workspace's latest image. These accumulate over time, since a config
    change (rosman.yml, rosman.lock, or a rosman upgrade that bumps
    DOCKERFILE_TEMPLATE_VERSION) produces a new hash-tagged image and
    nothing else ever removes the old one. A global operation (not scoped
    to the current workspace), since a deleted workspace's old images are
    exactly as orphaned as a rebuilt one's.
    """
    from rosman.docker_client import get_client
    from rosman.lifecycle import list_prunable_images, remove_images

    client = get_client()
    candidates = list_prunable_images(client)

    if not candidates:
        console.print("Nothing to prune -- no old, unused rosman-managed images found.")
        return 0

    total_size = sum(image.attrs.get("Size", 0) for image in candidates)
    console.print(
        f"Found {len(candidates)} old, unused rosman image(s), {_format_size(total_size)} total:"
    )
    for image in candidates:
        tags = ", ".join(image.tags) if image.tags else image.short_id
        console.print(f"  - {tags} ({_format_size(image.attrs.get('Size', 0))})")

    if not args.yes:
        if not sys.stdin.isatty():
            err_console.print(
                "[yellow]Non-interactive session -- pass --yes to actually remove these.[/yellow]"
            )
            return 0
        try:
            answer = input("Remove these images? [y/N] ")
        except EOFError:
            err_console.print("[yellow]No input available -- not removing anything.[/yellow]")
            return 0
        if answer.strip().lower() not in ("y", "yes"):
            console.print("Aborted.")
            return 0

    removed_count, reclaimed = remove_images(client, candidates)
    console.print(
        f"[green]Removed[/green] {removed_count} image(s), reclaimed {_format_size(reclaimed)}."
    )
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


def _fix_config_drift(config: RosmanConfig) -> None:
    """The one thing `rosman doctor --fix` actually auto-fixes: config
    drift. Everything else doctor reports is either informational (gpu,
    multi-host LAN) or needs something outside rosman entirely (a usbipd
    bind, which needs Windows-side admin rights rosman has no reliable
    way to trigger non-interactively) -- scoped narrow deliberately,
    rather than attempt something that could silently fail needing
    elevation.
    """
    from rosman.docker_client import get_client
    from rosman.lifecycle import ContainerManager

    try:
        client = get_client()
    except RosmanError:
        return  # let the normal checks below report the daemon issue
    manager = ContainerManager(client, RosmanState.load())
    container = manager.find_container(config)
    if container is None:
        return
    container.reload()
    if not manager.detect_drift(container, config).drifted:
        return
    console.print("[yellow]--fix: config has drifted, rebuilding...[/yellow]")
    manager.rebuild(config, reporter=RichReporter(console))
    console.print("[green]--fix: rebuilt.[/green]")


def cmd_doctor(args: argparse.Namespace) -> int:
    from rosman.doctor import run_checks

    config = _load_config_or_exit()
    if args.fix:
        _fix_config_drift(config)
    checks = run_checks(
        config,
        network_check=args.network_check,
        reporter=RichReporter(console) if args.network_check else None,
    )
    ok = True
    for check in checks:
        icon = "[green]OK[/green]  " if check.ok else "[red]FAIL[/red]"
        console.print(f"{icon} {check.name}: {check.detail}")
        ok = ok and check.ok
    return 0 if ok else 1


def _offer_rebuild(manager: ContainerManager, config: RosmanConfig, container, drift):
    """Surfaces config drift on an implicit auto-start (passthrough/shell),
    which previously had no drift signal at all -- unlike `rosman up`,
    which already refuses outright. Prompts interactively; in a
    non-interactive session (no tty, or stdin already closed/piped), just
    warns and keeps using the existing container rather than blocking a
    scripted/CI invocation on a prompt that can never be answered."""
    err_console.print(
        f"[yellow]Container config has drifted from {config.config_path}:[/yellow]"
    )
    for reason in drift.reasons:
        err_console.print(f"  - {reason}")
    if not sys.stdin.isatty():
        err_console.print(
            "[yellow]Non-interactive session -- using the existing container as-is. "
            "Run `rosman rebuild` to apply the change.[/yellow]"
        )
        return container
    try:
        answer = input("Rebuild now? [y/N] ")
    except EOFError:
        err_console.print(
            "[yellow]No input available -- using the existing container as-is.[/yellow]"
        )
        return container
    if answer.strip().lower() not in ("y", "yes"):
        return container
    console.print(f"Rebuilding container for this workspace ({config.ros_distro})...")
    rebuilt = manager.rebuild(config, reporter=RichReporter(console))
    console.print(f"[green]Rebuilt[/green] {rebuilt.name}")
    return rebuilt


def _ensure_running_with_notice(manager: ContainerManager, config: RosmanConfig):
    """Like ContainerManager.ensure_running, but prints the "starting" notice
    *before* a slow first-time build/create, not after -- otherwise an
    implicit auto-start on a passthrough/shell command looks like a hang.
    Also checks for config drift on every call, not just `rosman up`/
    `rosman doctor` -- otherwise a passthrough command silently keeps using
    a stale container with no signal at all that rosman.yml/rosman.lock
    changed since it was built.
    """
    container = manager.find_container(config)
    if container is None:
        console.print(
            f"Starting rosman container for this workspace ({config.ros_distro})..."
        )
        return manager.create_container(config, reporter=RichReporter(console)), True

    container.reload()
    drift = manager.detect_drift(container, config)
    if drift.drifted:
        container = _offer_rebuild(manager, config, container, drift)

    if container.status != "running":
        container.start()
    from rosman.networking import refresh_peers, refresh_peers_host_mode
    from rosman.runtime_config import resolve_network_mode

    if resolve_network_mode(config) == "host":
        refresh_peers_host_mode(config.remote_peers)
    else:
        refresh_peers(manager.client, config.network, config.remote_peers)
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
    command = args.shell_args[1:] if args.shell_args[:1] == ["--"] else args.shell_args
    return shell_command(container.name, workdir, shell=args.shell, command=command)


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
    resolve_exit_code, apt_packages, pip_packages, resolve_output = resolve_packages(
        container, extra_args
    )
    if resolve_exit_code != 0:
        err_console.print(
            f"[red]rosdep could not resolve dependencies:[/red]\n{resolve_output}"
        )
        return resolve_exit_code
    # Check both lists, not just apt -- a workspace whose only unmet
    # dependency resolves via pip previously fell through here as "nothing
    # to install" even though it genuinely had something to do (see
    # rosdep.py's parse_simulate_output docstring).
    if not apt_packages and not pip_packages:
        console.print("Nothing to install -- all declared dependencies are already satisfied.")
        return 0

    console.print(f"Installing: {', '.join(apt_packages + pip_packages)}")
    exit_code, output = install_packages(container, extra_args)
    if exit_code != 0:
        err_console.print(f"[red]rosdep install failed inside the container:[/red]\n{output}")
        return exit_code

    from rosman.config import write_lock

    write_lock(config.config_path, config.ros_distro, apt_packages, pip_packages)
    total = len(apt_packages) + len(pip_packages)
    console.print(
        f"[green]Wrote[/green] {total} package(s) to rosman.lock. "
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

    p_config = subparsers.add_parser(
        "config", help="Show the fully resolved effective config for this workspace"
    )
    p_config.set_defaults(func=cmd_config)

    p_prune = subparsers.add_parser(
        "prune", help="Remove old rosman-built images, preserving each workspace's latest"
    )
    p_prune.add_argument("-y", "--yes", action="store_true", help="Skip the confirmation prompt")
    p_prune.set_defaults(func=cmd_prune)

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
    p_doctor.add_argument(
        "--fix",
        action="store_true",
        help="Automatically rebuild if config drift is found, before reporting checks. "
        "Only fixes config drift -- every other check doctor reports is informational "
        "or needs something outside rosman (e.g. usbipd) to actually resolve.",
    )
    p_doctor.set_defaults(func=cmd_doctor)

    p_shell = subparsers.add_parser(
        "shell", help="Open a shell or run a command in the container"
    )
    p_shell.add_argument("--shell", default="bash", help="Shell to run (default: bash)")
    p_shell.add_argument(
        "shell_args",
        nargs=argparse.REMAINDER,
        metavar="COMMAND",
        help="Command to run in the login shell; omit for an interactive shell",
    )
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
        notice = pending_notice(state, __version__)
        if notice:
            err_console.print(f"[yellow]{notice}[/yellow]")
        schedule_update_check(state)
    except Exception:
        pass


def _run_command(command: Callable[[], int]) -> int:
    """Run one dispatched command with user-facing error handling.

    docker-py costs roughly 70ms to import on this machine. Import its
    exception class only after a non-rosman exception actually occurs;
    Docker-backed commands already import it through their own work, while
    `help`, `init`, and manual completion setup stay Docker-free.
    """
    try:
        return command()
    except RosmanError as exc:
        err_console.print(f"[red]{exc}[/red]")
        return 1
    except Exception as exc:
        from docker.errors import DockerException

        if isinstance(exc, DockerException):
            err_console.print(f"[red]Docker error:[/red] {exc}")
            return 1
        raise


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

    # `rosman completion bash/zsh` is typically invoked once per shell
    # startup, via `eval "$(rosman completion bash)"` in .bashrc/.zshrc --
    # not a deliberate "run a rosman command" action by the user. Letting
    # it trigger the update-notice check ties that check to shell startup
    # for anyone with tab-completion set up, exactly the "not when the
    # shell loads" behavior this was explicitly designed to avoid. Real
    # bug: only `__complete` (above) was ever excluded, not this.
    if not (argv and argv[0] == "completion"):
        _maybe_show_update_notice()

    if argv[:2] == ["rosdep", "install"]:
        # The one rosdep subcommand that isn't plain passthrough -- see
        # rosdep.py's module docstring. Every other `rosman rosdep <...>`
        # falls through to the generic passthrough branch below, exactly
        # like `colcon`.
        return _run_command(lambda: cmd_rosdep_install(argv[2:]))

    if argv and argv[0] not in RESERVED_COMMANDS and not argv[0].startswith("-"):
        return _run_command(lambda: cmd_passthrough(argv))

    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    try:
        return _run_command(lambda: args.func(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
