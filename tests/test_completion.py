import subprocess
from pathlib import Path
from unittest.mock import MagicMock

from rosman.completion import build_inner_command, complete
from rosman.config import parse_config


def make_config(tmp_path: Path):
    path = tmp_path / "rosman.yml"
    return parse_config("ros_distro: humble\n", path)


def test_build_inner_command_prefixes_ros2_by_default():
    assert build_inner_command(["topic", "ec"]) == ["ros2", "topic", "ec"]


def test_build_inner_command_leaves_colcon_alone():
    assert build_inner_command(["colcon", "bui"]) == ["colcon", "bui"]


def test_build_inner_command_none_for_empty_words():
    assert build_inner_command([]) is None


def test_build_inner_command_none_for_reserved_first_word():
    assert build_inner_command(["doctor"]) is None
    assert build_inner_command(["shell"]) is None


def test_complete_returns_empty_for_reserved_words(tmp_path: Path):
    config = make_config(tmp_path)
    result = complete(MagicMock(), MagicMock(), config, ["doctor"])
    assert result == []


def test_complete_returns_empty_when_no_container(tmp_path: Path, monkeypatch):
    config = make_config(tmp_path)
    monkeypatch.setattr(
        "rosman.completion.ContainerManager.find_container", lambda self, config: None
    )
    result = complete(MagicMock(), MagicMock(), config, ["topic", "ec"])
    assert result == []


def test_complete_returns_empty_when_container_not_running(tmp_path: Path, monkeypatch):
    config = make_config(tmp_path)
    container = MagicMock()
    container.status = "exited"
    monkeypatch.setattr(
        "rosman.completion.ContainerManager.find_container", lambda self, config: container
    )
    result = complete(MagicMock(), MagicMock(), config, ["topic", "ec"])
    assert result == []


def test_complete_relays_ifs_separated_output(tmp_path: Path, monkeypatch):
    config = make_config(tmp_path)
    container = MagicMock()
    container.status = "running"
    container.name = "rosman-test"
    monkeypatch.setattr(
        "rosman.completion.ContainerManager.find_container", lambda self, config: container
    )
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/docker")
    fake_result = MagicMock()
    fake_result.stdout = b"echo\x0becho_sub\x0b"
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: fake_result)

    result = complete(MagicMock(), MagicMock(), config, ["topic", "ec"])
    assert result == ["echo", "echo_sub"]


def test_complete_returns_empty_on_subprocess_error(tmp_path: Path, monkeypatch):
    config = make_config(tmp_path)
    container = MagicMock()
    container.status = "running"
    container.name = "rosman-test"
    monkeypatch.setattr(
        "rosman.completion.ContainerManager.find_container", lambda self, config: container
    )
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/docker")

    def raise_timeout(*a, **k):
        raise subprocess.TimeoutExpired(cmd="docker", timeout=3)

    monkeypatch.setattr(subprocess, "run", raise_timeout)

    result = complete(MagicMock(), MagicMock(), config, ["topic", "ec"])
    assert result == []


def test_complete_returns_empty_without_docker_binary(tmp_path: Path, monkeypatch):
    config = make_config(tmp_path)
    container = MagicMock()
    container.status = "running"
    monkeypatch.setattr(
        "rosman.completion.ContainerManager.find_container", lambda self, config: container
    )
    monkeypatch.setattr("shutil.which", lambda name: None)

    result = complete(MagicMock(), MagicMock(), config, ["topic", "ec"])
    assert result == []
