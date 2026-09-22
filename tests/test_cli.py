import argparse
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from rich.console import Console as RichConsole

from rosman.cli import (
    GITIGNORE_ENTRY,
    _ensure_running_with_notice,
    _fix_config_drift,
    _maybe_show_update_notice,
    cmd_config,
    cmd_init,
    cmd_prune,
)
from rosman.config import parse_config
from rosman.errors import RosmanError
from rosman.lifecycle import DriftReport
from rosman.state import RosmanState


def make_args(path: Path, distro: str = "humble", force: bool = False) -> argparse.Namespace:
    return argparse.Namespace(path=str(path), distro=distro, force=force)


def make_real_config(tmp_path: Path, extra: str = ""):
    """A real RosmanConfig (not a MagicMock stand-in) -- needed wherever a
    test exercises actual config-resolution logic (domain_id/network_mode
    "auto" handling), not just Docker-facing plumbing."""
    path = tmp_path / "rosman.yml"
    path.write_text(f"ros_distro: humble\n{extra}")
    return parse_config(path.read_text(), path)


def render_table_text(table) -> str:
    """Renders a rich Table to plain text for substring assertions,
    without depending on Table's internal row-storage API."""
    recorder = RichConsole(record=True, width=200)
    recorder.print(table)
    return recorder.export_text()


def test_cmd_init_creates_config(tmp_path: Path):
    rc = cmd_init(make_args(tmp_path))
    assert rc == 0
    assert (tmp_path / "rosman.yml").is_file()
    assert "ros_distro: humble" in (tmp_path / "rosman.yml").read_text()


def test_cmd_init_refuses_to_overwrite_without_force(tmp_path: Path):
    (tmp_path / "rosman.yml").write_text("ros_distro: jazzy\n")
    rc = cmd_init(make_args(tmp_path))
    assert rc == 1
    assert "jazzy" in (tmp_path / "rosman.yml").read_text()


def test_cmd_init_overwrites_with_force(tmp_path: Path):
    (tmp_path / "rosman.yml").write_text("ros_distro: jazzy\n")
    rc = cmd_init(make_args(tmp_path, force=True))
    assert rc == 0
    assert "ros_distro: humble" in (tmp_path / "rosman.yml").read_text()


def test_cmd_init_rejects_unknown_distro(tmp_path: Path):
    rc = cmd_init(make_args(tmp_path, distro="bogus"))
    assert rc == 1
    assert not (tmp_path / "rosman.yml").exists()


def test_cmd_init_appends_to_existing_gitignore(tmp_path: Path):
    (tmp_path / ".gitignore").write_text("__pycache__/\n")
    cmd_init(make_args(tmp_path))
    gitignore = (tmp_path / ".gitignore").read_text()
    assert "__pycache__/" in gitignore
    assert GITIGNORE_ENTRY in gitignore


def test_cmd_init_does_not_duplicate_gitignore_entry(tmp_path: Path):
    (tmp_path / ".gitignore").write_text(f"{GITIGNORE_ENTRY}\n")
    cmd_init(make_args(tmp_path))
    gitignore = (tmp_path / ".gitignore").read_text()
    assert gitignore.count(GITIGNORE_ENTRY) == 1


def test_cmd_init_does_not_create_gitignore_if_absent(tmp_path: Path):
    cmd_init(make_args(tmp_path))
    assert not (tmp_path / ".gitignore").exists()


def test_cmd_init_gitignore_handles_missing_trailing_newline(tmp_path: Path):
    (tmp_path / ".gitignore").write_text("__pycache__/")  # no trailing newline
    cmd_init(make_args(tmp_path))
    gitignore = (tmp_path / ".gitignore").read_text()
    assert "__pycache__/\nrosman.local.yml\n" == gitignore


def test_update_notice_skipped_when_stderr_not_a_tty():
    with (
        patch("sys.stderr.isatty", return_value=False),
        patch("rosman.cli.RosmanState.load") as mock_load,
    ):
        _maybe_show_update_notice()
    mock_load.assert_not_called()


def test_update_notice_never_raises_on_internal_failure():
    # Any failure here (network, disk, whatever) must never propagate --
    # it would otherwise take down every single rosman command.
    with (
        patch("sys.stderr.isatty", return_value=True),
        patch("rosman.cli.RosmanState.load", side_effect=RuntimeError("boom")),
    ):
        _maybe_show_update_notice()  # must not raise


def test_update_notice_prints_when_available():
    with (
        patch("sys.stderr.isatty", return_value=True),
        patch("rosman.cli.RosmanState.load") as mock_load,
        patch("rosman.cli.schedule_update_check"),
        patch("rosman.cli.pending_notice", return_value="a new version exists"),
        patch("rosman.cli.err_console.print") as mock_print,
    ):
        mock_load.return_value = object()
        _maybe_show_update_notice()
    mock_print.assert_called_once()
    assert "a new version exists" in mock_print.call_args[0][0]


def test_main_skips_update_notice_for_completion_command(monkeypatch):
    # Regression test: `rosman completion bash/zsh` is typically invoked
    # once per shell startup via `eval "$(rosman completion bash)"` in
    # .bashrc/.zshrc, not a deliberate "run a rosman command" action --
    # letting it trigger the update-notice check ties that check to shell
    # startup for anyone with tab-completion set up, exactly what "not
    # when the shell loads" was supposed to avoid. Only `__complete` was
    # ever excluded, not this, until this was reported and fixed.
    import rosman.cli as cli_mod

    called = []
    monkeypatch.setattr(cli_mod, "_maybe_show_update_notice", lambda: called.append(True))

    rc = cli_mod.main(["completion", "bash"])

    assert rc == 0
    assert called == []


def test_main_still_checks_update_notice_for_a_real_command(monkeypatch):
    import rosman.cli as cli_mod

    called = []
    monkeypatch.setattr(cli_mod, "_maybe_show_update_notice", lambda: called.append(True))

    cli_mod.main(["help"])

    assert called == [True]


def test_help_path_does_not_import_docker_sdk(monkeypatch):
    import rosman.cli as cli_mod

    monkeypatch.setattr(cli_mod, "_maybe_show_update_notice", lambda: None)
    with patch.dict("sys.modules", {"docker": None, "docker.errors": None}):
        assert cli_mod.main(["help"]) == 0


def test_run_command_formats_docker_sdk_errors():
    from docker.errors import DockerException

    from rosman.cli import _run_command

    def fail() -> int:
        raise DockerException("daemon failed")

    with patch("rosman.cli.err_console.print") as printed:
        assert _run_command(fail) == 1
    assert "Docker error" in printed.call_args.args[0]


def _make_manager(drifted: bool, reasons=("ros_distro changed",)):
    manager = MagicMock()
    container = MagicMock()
    container.status = "running"
    manager.find_container.return_value = container
    manager.detect_drift.return_value = DriftReport(drifted=drifted, reasons=list(reasons))
    return manager, container


@pytest.fixture
def mock_peer_refresh():
    with (
        patch("rosman.networking.refresh_peers") as bridge,
        patch("rosman.networking.refresh_peers_host_mode") as host,
    ):
        yield bridge, host


def test_ensure_running_no_prompt_when_no_drift(mock_peer_refresh):
    manager, container = _make_manager(drifted=False)
    with patch("builtins.input") as mock_input:
        result, created = _ensure_running_with_notice(
            manager, MagicMock(config_path="x.yml", remote_peers=[])
        )
    mock_input.assert_not_called()
    manager.rebuild.assert_not_called()
    assert result is container
    assert created is False


def test_ensure_running_rebuilds_on_yes(tmp_path: Path, mock_peer_refresh):
    manager, container = _make_manager(drifted=True)
    rebuilt = MagicMock()
    manager.rebuild.return_value = rebuilt
    config = MagicMock(config_path=tmp_path / "rosman.yml", ros_distro="humble", remote_peers=[])

    with (
        patch("sys.stdin.isatty", return_value=True),
        patch("builtins.input", return_value="y"),
    ):
        result, _ = _ensure_running_with_notice(manager, config)

    manager.rebuild.assert_called_once()
    assert result is rebuilt


def test_ensure_running_keeps_existing_on_no(tmp_path: Path, mock_peer_refresh):
    manager, container = _make_manager(drifted=True)
    config = MagicMock(config_path=tmp_path / "rosman.yml", ros_distro="humble", remote_peers=[])

    with (
        patch("sys.stdin.isatty", return_value=True),
        patch("builtins.input", return_value="n"),
    ):
        result, _ = _ensure_running_with_notice(manager, config)

    manager.rebuild.assert_not_called()
    assert result is container


def test_ensure_running_skips_prompt_when_not_a_tty(tmp_path: Path, mock_peer_refresh):
    manager, container = _make_manager(drifted=True)
    config = MagicMock(config_path=tmp_path / "rosman.yml", ros_distro="humble", remote_peers=[])

    with (
        patch("sys.stdin.isatty", return_value=False),
        patch("builtins.input") as mock_input,
    ):
        result, _ = _ensure_running_with_notice(manager, config)

    mock_input.assert_not_called()
    manager.rebuild.assert_not_called()
    assert result is container


def test_ensure_running_keeps_existing_on_eof(tmp_path: Path, mock_peer_refresh):
    manager, container = _make_manager(drifted=True)
    config = MagicMock(config_path=tmp_path / "rosman.yml", ros_distro="humble", remote_peers=[])

    with (
        patch("sys.stdin.isatty", return_value=True),
        patch("builtins.input", side_effect=EOFError),
    ):
        result, _ = _ensure_running_with_notice(manager, config)

    manager.rebuild.assert_not_called()
    assert result is container


def test_ensure_running_refreshes_peers_when_remote_list_is_removed(
    tmp_path: Path, mock_peer_refresh
):
    manager, _ = _make_manager(drifted=False)
    config = make_real_config(tmp_path, "network_mode: host\nremote_peers: []\n")
    _ensure_running_with_notice(manager, config)
    _, host = mock_peer_refresh
    host.assert_called_once_with([])


# -- cmd_config ------------------------------------------------------------


def _patch_config_docker(monkeypatch, docker_unreachable=True):
    """cmd_config wraps its own Docker access in try/except RosmanError so
    it still works as pure config introspection when the daemon isn't
    reachable -- that's the common case exercised here, since the domain
    id/network mode display logic doesn't need Docker at all."""
    if docker_unreachable:
        monkeypatch.setattr(
            "rosman.docker_client.get_client",
            MagicMock(side_effect=RosmanError("docker unreachable")),
        )
    else:
        monkeypatch.setattr("rosman.docker_client.get_client", lambda: MagicMock())


def test_cmd_config_domain_id_not_yet_assigned(tmp_path: Path, monkeypatch):
    import rosman.cli as cli_mod

    config = make_real_config(tmp_path, "network_mode: bridge\n")
    monkeypatch.setattr(cli_mod, "_load_config_or_exit", lambda: config)
    monkeypatch.setattr(cli_mod.RosmanState, "load", lambda *a, **kw: RosmanState(projects={}))
    _patch_config_docker(monkeypatch)
    captured = {}
    monkeypatch.setattr(cli_mod.console, "print", lambda table: captured.setdefault("table", table))

    rc = cmd_config(argparse.Namespace())

    assert rc == 0
    text = render_table_text(captured["table"])
    assert "not yet assigned" in text


def test_cmd_config_domain_id_auto_assigned(tmp_path: Path, monkeypatch):
    import rosman.cli as cli_mod

    config = make_real_config(tmp_path, "network_mode: bridge\n")
    monkeypatch.setattr(cli_mod, "_load_config_or_exit", lambda: config)
    project = SimpleNamespace(domain_id=13, container_name=None)
    fake_state = RosmanState(projects={str(config.workspace_root): project})
    monkeypatch.setattr(cli_mod.RosmanState, "load", lambda *a, **kw: fake_state)
    _patch_config_docker(monkeypatch)
    captured = {}
    monkeypatch.setattr(cli_mod.console, "print", lambda table: captured.setdefault("table", table))

    cmd_config(argparse.Namespace())

    text = render_table_text(captured["table"])
    assert "13 (auto-assigned)" in text


def test_cmd_config_explicit_domain_id_shown_bare(tmp_path: Path, monkeypatch):
    import rosman.cli as cli_mod

    config = make_real_config(tmp_path, "network_mode: bridge\ndomain_id: 42\n")
    monkeypatch.setattr(cli_mod, "_load_config_or_exit", lambda: config)
    monkeypatch.setattr(cli_mod.RosmanState, "load", lambda *a, **kw: RosmanState(projects={}))
    _patch_config_docker(monkeypatch)
    captured = {}
    monkeypatch.setattr(cli_mod.console, "print", lambda table: captured.setdefault("table", table))

    cmd_config(argparse.Namespace())

    text = render_table_text(captured["table"])
    assert "42" in text
    assert "auto-assigned" not in text
    assert "not yet assigned" not in text


def test_cmd_config_network_mode_auto_detected(tmp_path: Path, monkeypatch):
    import rosman.cli as cli_mod

    config = make_real_config(tmp_path)  # network_mode left at "auto"
    monkeypatch.setattr(cli_mod, "_load_config_or_exit", lambda: config)
    monkeypatch.setattr(cli_mod.RosmanState, "load", lambda *a, **kw: RosmanState(projects={}))
    _patch_config_docker(monkeypatch)
    monkeypatch.setattr("platform.system", lambda: "Linux")
    captured = {}
    monkeypatch.setattr(cli_mod.console, "print", lambda table: captured.setdefault("table", table))

    cmd_config(argparse.Namespace())

    text = render_table_text(captured["table"])
    assert "host (auto-detected)" in text


def test_cmd_config_network_mode_explicit_shown_bare(tmp_path: Path, monkeypatch):
    import rosman.cli as cli_mod

    config = make_real_config(tmp_path, "network_mode: bridge\n")
    monkeypatch.setattr(cli_mod, "_load_config_or_exit", lambda: config)
    monkeypatch.setattr(cli_mod.RosmanState, "load", lambda *a, **kw: RosmanState(projects={}))
    _patch_config_docker(monkeypatch)
    captured = {}
    monkeypatch.setattr(cli_mod.console, "print", lambda table: captured.setdefault("table", table))

    cmd_config(argparse.Namespace())

    text = render_table_text(captured["table"])
    assert "bridge" in text
    assert "auto-detected" not in text


def test_cmd_config_does_not_crash_when_docker_unreachable(tmp_path: Path, monkeypatch):
    import rosman.cli as cli_mod

    config = make_real_config(tmp_path, "network_mode: bridge\n")
    monkeypatch.setattr(cli_mod, "_load_config_or_exit", lambda: config)
    monkeypatch.setattr(cli_mod.RosmanState, "load", lambda *a, **kw: RosmanState(projects={}))
    _patch_config_docker(monkeypatch, docker_unreachable=True)
    monkeypatch.setattr(cli_mod.console, "print", lambda table: None)

    rc = cmd_config(argparse.Namespace())

    assert rc == 0


def test_cmd_config_shows_container_status_when_docker_reachable(tmp_path: Path, monkeypatch):
    import rosman.cli as cli_mod

    config = make_real_config(tmp_path, "network_mode: bridge\n")
    monkeypatch.setattr(cli_mod, "_load_config_or_exit", lambda: config)
    monkeypatch.setattr(cli_mod.RosmanState, "load", lambda *a, **kw: RosmanState(projects={}))
    _patch_config_docker(monkeypatch, docker_unreachable=False)
    container = MagicMock()
    container.status = "running"
    monkeypatch.setattr(
        "rosman.lifecycle.ContainerManager",
        lambda client, state: SimpleNamespace(find_container=lambda config: container),
    )
    captured = {}
    monkeypatch.setattr(cli_mod.console, "print", lambda table: captured.setdefault("table", table))

    cmd_config(argparse.Namespace())

    text = render_table_text(captured["table"])
    assert "running" in text


# -- cmd_prune ---------------------------------------------------------------


def _make_prune_image(tags, size):
    image = MagicMock(tags=tags)
    image.attrs = {"Size": size}
    return image


def test_cmd_prune_nothing_to_prune(monkeypatch):
    import rosman.cli as cli_mod

    monkeypatch.setattr("rosman.docker_client.get_client", lambda: MagicMock())
    monkeypatch.setattr("rosman.lifecycle.list_prunable_images", lambda client: [])
    printed = []
    monkeypatch.setattr(cli_mod.console, "print", lambda msg: printed.append(msg))

    rc = cmd_prune(argparse.Namespace(yes=False))

    assert rc == 0
    assert any("Nothing to prune" in m for m in printed)


def test_cmd_prune_yes_flag_skips_prompt_and_removes(monkeypatch):
    import rosman.cli as cli_mod

    image = _make_prune_image(["rosman/humble-abc:def"], 1000)
    monkeypatch.setattr("rosman.docker_client.get_client", lambda: MagicMock())
    monkeypatch.setattr("rosman.lifecycle.list_prunable_images", lambda client: [image])
    remove_calls = {}

    def fake_remove(client, images):
        remove_calls["images"] = images
        return 1, 1000

    monkeypatch.setattr("rosman.lifecycle.remove_images", fake_remove)
    monkeypatch.setattr(
        "builtins.input", MagicMock(side_effect=AssertionError("must not prompt with --yes"))
    )
    monkeypatch.setattr(cli_mod.console, "print", lambda *a, **kw: None)

    rc = cmd_prune(argparse.Namespace(yes=True))

    assert rc == 0
    assert remove_calls["images"] == [image]


def test_cmd_prune_non_interactive_without_yes_does_not_remove(monkeypatch):
    import rosman.cli as cli_mod

    image = _make_prune_image(["rosman/humble-abc:def"], 1000)
    monkeypatch.setattr("rosman.docker_client.get_client", lambda: MagicMock())
    monkeypatch.setattr("rosman.lifecycle.list_prunable_images", lambda client: [image])
    remove_mock = MagicMock()
    monkeypatch.setattr("rosman.lifecycle.remove_images", remove_mock)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr(cli_mod.console, "print", lambda *a, **kw: None)
    monkeypatch.setattr(cli_mod.err_console, "print", lambda *a, **kw: None)

    rc = cmd_prune(argparse.Namespace(yes=False))

    assert rc == 0
    remove_mock.assert_not_called()


def test_cmd_prune_interactive_yes_removes(monkeypatch):
    import rosman.cli as cli_mod

    image = _make_prune_image(["rosman/humble-abc:def"], 1000)
    monkeypatch.setattr("rosman.docker_client.get_client", lambda: MagicMock())
    monkeypatch.setattr("rosman.lifecycle.list_prunable_images", lambda client: [image])
    remove_mock = MagicMock(return_value=(1, 1000))
    monkeypatch.setattr("rosman.lifecycle.remove_images", remove_mock)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    monkeypatch.setattr(cli_mod.console, "print", lambda *a, **kw: None)

    rc = cmd_prune(argparse.Namespace(yes=False))

    assert rc == 0
    remove_mock.assert_called_once()


def test_cmd_prune_interactive_no_does_not_remove(monkeypatch):
    import rosman.cli as cli_mod

    image = _make_prune_image(["rosman/humble-abc:def"], 1000)
    monkeypatch.setattr("rosman.docker_client.get_client", lambda: MagicMock())
    monkeypatch.setattr("rosman.lifecycle.list_prunable_images", lambda client: [image])
    remove_mock = MagicMock()
    monkeypatch.setattr("rosman.lifecycle.remove_images", remove_mock)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    monkeypatch.setattr(cli_mod.console, "print", lambda *a, **kw: None)

    rc = cmd_prune(argparse.Namespace(yes=False))

    assert rc == 0
    remove_mock.assert_not_called()


def test_cmd_prune_interactive_eof_does_not_remove(monkeypatch):
    import rosman.cli as cli_mod

    image = _make_prune_image(["rosman/humble-abc:def"], 1000)
    monkeypatch.setattr("rosman.docker_client.get_client", lambda: MagicMock())
    monkeypatch.setattr("rosman.lifecycle.list_prunable_images", lambda client: [image])
    remove_mock = MagicMock()
    monkeypatch.setattr("rosman.lifecycle.remove_images", remove_mock)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", MagicMock(side_effect=EOFError))
    monkeypatch.setattr(cli_mod.console, "print", lambda *a, **kw: None)
    monkeypatch.setattr(cli_mod.err_console, "print", lambda *a, **kw: None)

    rc = cmd_prune(argparse.Namespace(yes=False))

    assert rc == 0
    remove_mock.assert_not_called()


# -- doctor --fix (_fix_config_drift) ----------------------------------------


def test_fix_config_drift_rebuilds_when_drifted(tmp_path: Path, monkeypatch):
    import rosman.cli as cli_mod

    config = MagicMock(config_path=tmp_path / "rosman.yml", ros_distro="humble")
    manager, _container = _make_manager(drifted=True)
    monkeypatch.setattr("rosman.docker_client.get_client", lambda: MagicMock())
    monkeypatch.setattr("rosman.lifecycle.ContainerManager", lambda client, state: manager)
    monkeypatch.setattr(cli_mod.RosmanState, "load", lambda *a, **kw: MagicMock())
    monkeypatch.setattr(cli_mod.console, "print", lambda *a, **kw: None)

    _fix_config_drift(config)

    manager.rebuild.assert_called_once()


def test_fix_config_drift_does_nothing_when_not_drifted(tmp_path: Path, monkeypatch):
    import rosman.cli as cli_mod

    config = MagicMock(config_path=tmp_path / "rosman.yml", ros_distro="humble")
    manager, _container = _make_manager(drifted=False)
    monkeypatch.setattr("rosman.docker_client.get_client", lambda: MagicMock())
    monkeypatch.setattr("rosman.lifecycle.ContainerManager", lambda client, state: manager)
    monkeypatch.setattr(cli_mod.RosmanState, "load", lambda *a, **kw: MagicMock())

    _fix_config_drift(config)

    manager.rebuild.assert_not_called()


def test_fix_config_drift_does_nothing_when_no_container(tmp_path: Path, monkeypatch):
    config = MagicMock(config_path=tmp_path / "rosman.yml", ros_distro="humble")
    manager = MagicMock()
    manager.find_container.return_value = None
    monkeypatch.setattr("rosman.docker_client.get_client", lambda: MagicMock())
    monkeypatch.setattr("rosman.lifecycle.ContainerManager", lambda client, state: manager)
    monkeypatch.setattr("rosman.cli.RosmanState.load", lambda *a, **kw: MagicMock())

    _fix_config_drift(config)

    manager.rebuild.assert_not_called()
    manager.detect_drift.assert_not_called()


def test_fix_config_drift_noop_when_docker_unreachable(tmp_path: Path, monkeypatch):
    config = MagicMock(config_path=tmp_path / "rosman.yml", ros_distro="humble")
    monkeypatch.setattr(
        "rosman.docker_client.get_client",
        MagicMock(side_effect=RosmanError("docker unreachable")),
    )

    _fix_config_drift(config)  # must not raise
