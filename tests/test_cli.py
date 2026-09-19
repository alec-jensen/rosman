import argparse
from pathlib import Path
from unittest.mock import patch

from rosman.cli import GITIGNORE_ENTRY, _maybe_show_update_notice, cmd_init


def make_args(path: Path, distro: str = "humble", force: bool = False) -> argparse.Namespace:
    return argparse.Namespace(path=str(path), distro=distro, force=force)


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
        patch("rosman.cli.check_for_update"),
        patch("rosman.cli.pending_notice", return_value="a new version exists"),
        patch("rosman.cli.err_console.print") as mock_print,
    ):
        mock_load.return_value = object()
        _maybe_show_update_notice()
    mock_print.assert_called_once()
    assert "a new version exists" in mock_print.call_args[0][0]
