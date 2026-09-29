import subprocess
from pathlib import Path
from unittest.mock import MagicMock

from rosman import exec_env
from rosman.dispatch import _exec_argv


def _fake_run(base: dict[str, str], login: dict[str, str], calls: list):
    def encode(env):
        return b"".join(f"{k}={v}".encode() + b"\0" for k, v in env.items())

    def run(*args, **kwargs):
        calls.append(args)
        return MagicMock(
            returncode=0, stdout=encode(base) + exec_env._SPLIT + encode(login)
        )

    return run


def test_login_env_captures_only_the_delta_and_caches(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    base = {"PATH": "/usr/bin", "HOME": "/root", "PWD": "/"}
    login = {**base, "PATH": "/opt/ros/humble/bin:/usr/bin", "AMENT_PREFIX_PATH": "/opt/ros/humble",
             "SHLVL": "1"}
    calls: list = []
    monkeypatch.setattr(subprocess, "run", _fake_run(base, login, calls))

    first = exec_env.login_env("docker", "c", "id1", tmp_path)
    assert first == {"PATH": "/opt/ros/humble/bin:/usr/bin", "AMENT_PREFIX_PATH": "/opt/ros/humble"}
    assert exec_env.login_env("docker", "c", "id1", tmp_path) == first
    assert len(calls) == 1  # second call served from cache


def test_login_env_recaptures_when_container_or_overlay_changes(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    calls: list = []
    monkeypatch.setattr(
        subprocess, "run", _fake_run({"PATH": "/usr/bin"}, {"PATH": "/opt/ros/bin"}, calls)
    )
    exec_env.login_env("docker", "c", "id1", tmp_path)
    exec_env.login_env("docker", "c", "id2", tmp_path)  # container recreated
    (tmp_path / "install").mkdir()
    (tmp_path / "install" / "setup.bash").write_text("# built")
    exec_env.login_env("docker", "c", "id2", tmp_path)  # colcon build happened
    assert len(calls) == 3


def test_login_env_returns_none_when_capture_fails(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: MagicMock(returncode=1, stdout=b"")
    )
    assert exec_env.login_env("docker", "c", "id1", tmp_path) is None


def test_exec_argv_passes_env_flags(monkeypatch):
    monkeypatch.setattr("rosman.dispatch._docker_binary", lambda: "docker")
    argv = _exec_argv("c", "/workspace", ["ros2", "topic", "list"], {"A": "1"})
    assert argv[argv.index("-e") + 1] == "A=1"
    assert argv[-4:] == ["c", "ros2", "topic", "list"]
