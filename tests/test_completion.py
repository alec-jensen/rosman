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
    result = complete(config, ["doctor"])
    assert result == []


def _fake_run(inspect_returncode=0, inspect_stdout="true", exec_stdout=b""):
    """A `subprocess.run` stand-in that tells apart the `docker inspect`
    call `complete()` makes first from the `docker exec` relay it makes
    second, by looking for each subcommand in argv -- both go through the
    same patched function since `complete()` no longer takes a docker-py
    client/state to swap out (it's deliberately docker-py-free now, see
    completion.py's docstring)."""

    def _run(argv, **kwargs):
        result = MagicMock()
        if "inspect" in argv:
            result.returncode = inspect_returncode
            result.stdout = inspect_stdout
        else:
            result.returncode = 0
            result.stdout = exec_stdout
        return result

    return _run


def test_complete_returns_empty_when_no_container(tmp_path: Path, monkeypatch):
    config = make_config(tmp_path)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(subprocess, "run", _fake_run(inspect_returncode=1, inspect_stdout=""))

    result = complete(config, ["topic", "ec"])
    assert result == []


def test_complete_returns_empty_when_container_not_running(tmp_path: Path, monkeypatch):
    config = make_config(tmp_path)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(subprocess, "run", _fake_run(inspect_stdout="false"))

    result = complete(config, ["topic", "ec"])
    assert result == []


def test_complete_relays_ifs_separated_output(tmp_path: Path, monkeypatch):
    config = make_config(tmp_path)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(subprocess, "run", _fake_run(exec_stdout=b"echo\x0becho_sub\x0b"))

    result = complete(config, ["topic", "ec"])
    assert result == ["echo", "echo_sub"]


def test_complete_returns_empty_on_subprocess_error(tmp_path: Path, monkeypatch):
    config = make_config(tmp_path)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/docker")

    def flaky_run(argv, **kwargs):
        if "inspect" in argv:
            return _fake_run()(argv, **kwargs)
        raise subprocess.TimeoutExpired(cmd="docker", timeout=3)

    monkeypatch.setattr(subprocess, "run", flaky_run)

    result = complete(config, ["topic", "ec"])
    assert result == []


def test_complete_returns_empty_on_inspect_error(tmp_path: Path, monkeypatch):
    config = make_config(tmp_path)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/docker")

    def raise_timeout(*a, **k):
        raise subprocess.TimeoutExpired(cmd="docker", timeout=2)

    monkeypatch.setattr(subprocess, "run", raise_timeout)

    result = complete(config, ["topic", "ec"])
    assert result == []


def test_complete_returns_empty_without_docker_binary(tmp_path: Path, monkeypatch):
    config = make_config(tmp_path)
    monkeypatch.setattr("shutil.which", lambda name: None)

    result = complete(config, ["topic", "ec"])
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
