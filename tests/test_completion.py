import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from rosman.completion import BASH_SCRIPT, ZSH_SCRIPT, build_inner_command, complete
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


def _run_completion_script(shell: str, script: str) -> subprocess.CompletedProcess:
    """Actually sources the printed script and invokes the completion
    function under a real shell interpreter -- a regression guard for the
    exact bug class that shipped in v0.1.0: syntax that's valid bash but
    breaks under zsh's own parser (even with bashcompinit loaded), which
    the tests above can't catch since they never touch the shell-script
    text itself, only the Python side."""
    driver = f"""
{script}
COMP_WORDS=(rosman topic ec)
COMP_CWORD=2
COMP_LINE="rosman topic ec"
COMP_POINT=${{#COMP_LINE}}
_rosman_complete
"""
    return subprocess.run(
        [shell, "-c", driver],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not on PATH")
def test_bash_completion_script_has_no_syntax_errors():
    # Not asserting on returncode: with no real `rosman`/container on PATH
    # in this test environment, `_rosman_complete`'s trailing
    # `[[ -n "$out" ]] && ...` naturally exits 1 (a false test, not an
    # error) -- what matters here is that the shell's own parser didn't
    # choke on the script.
    result = _run_completion_script("bash", BASH_SCRIPT)
    assert "unrecognized" not in result.stderr
    assert "syntax error" not in result.stderr


@pytest.mark.skipif(shutil.which("zsh") is None, reason="zsh not on PATH")
def test_zsh_completion_script_has_no_syntax_errors():
    # `compdef` is normally provided by `compinit`, irrelevant to this
    # regression test -- stub it out rather than pull in a real zsh
    # completion system just to source the script.
    script = "compdef() { :; }\n" + ZSH_SCRIPT
    result = _run_completion_script("zsh", script)
    assert "unrecognized modifier" not in result.stderr
    assert "parse error" not in result.stderr
