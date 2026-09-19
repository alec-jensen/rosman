import argparse
from pathlib import Path

from rosman.cli import GITIGNORE_ENTRY, cmd_init


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
