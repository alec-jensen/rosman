from pathlib import Path
from types import SimpleNamespace

from rosman import platform_support as ps


def test_gui_passthrough_returns_none_without_display(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("DISPLAY", raising=False)
    assert ps.gui_passthrough("/home/rosman", tmp_path / "xauth") is None


def test_gui_passthrough_wsl2_uses_existing_sockets(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setattr(ps, "is_wsl2", lambda: True)
    monkeypatch.setattr("os.path.isdir", lambda p: True)

    result = ps.gui_passthrough("/home/rosman", tmp_path / "xauth")

    assert result is not None
    assert result.environment["DISPLAY"] == ":0"
    assert result.environment["WAYLAND_DISPLAY"] == "wayland-0"
    assert "/tmp/.X11-unix" in result.volumes
    assert "/mnt/wslg" in result.volumes
    # WSLg needs no XAuth cookie -- that's the whole point of WSLg detection.
    assert "XAUTHORITY" not in result.environment


def test_gui_passthrough_linux_without_xauth_binary_degrades_gracefully(
    monkeypatch, tmp_path: Path
):
    # Simulate a host with no `xauth` binary (or no cookie for this display)
    # -- gui_passthrough must not raise, just skip the cookie it couldn't
    # generate rather than crashing `rosman up` over a GUI convenience.
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(ps, "is_wsl2", lambda: False)

    def fake_run(*args, **kwargs):
        raise FileNotFoundError("xauth: command not found")

    monkeypatch.setattr(ps.subprocess, "run", fake_run)

    result = ps.gui_passthrough("/home/rosman", tmp_path / "xauth")

    assert result is not None
    assert result.environment["DISPLAY"] == ":0"
    assert "XAUTHORITY" not in result.environment
    assert not (tmp_path / "xauth").exists()


def test_gui_passthrough_linux_generates_xauth_cookie(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(ps, "is_wsl2", lambda: False)

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[:2] == ["xauth", "nlist"]:
            return SimpleNamespace(stdout="0000  0102030405060708090a0b0c0d0e0f10\n")
        cookie_path = Path(cmd[2])
        cookie_path.write_text("fake-cookie-data")
        return SimpleNamespace(stdout="")

    monkeypatch.setattr(ps.subprocess, "run", fake_run)

    cookie_path = tmp_path / "xauth"
    result = ps.gui_passthrough("/home/rosman", cookie_path)

    assert result is not None
    assert result.environment["XAUTHORITY"] == "/home/rosman/.Xauthority"
    assert str(cookie_path) in result.volumes
    assert cookie_path.exists()
    assert len(calls) == 2


def test_is_wsl2_false_when_proc_version_absent(monkeypatch):
    def fake_open(*args, **kwargs):
        raise OSError("no such file")

    monkeypatch.setattr("builtins.open", fake_open)
    assert ps.is_wsl2() is False


def test_is_wsl2_true_when_microsoft_in_proc_version(tmp_path: Path, monkeypatch):
    fake_proc_version = tmp_path / "version"
    fake_proc_version.write_text("Linux version 5.15.0 (Microsoft@Microsoft.com)")

    real_open = open

    def fake_open(path, *args, **kwargs):
        if path == "/proc/version":
            return real_open(fake_proc_version, *args, **kwargs)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", fake_open)
    assert ps.is_wsl2() is True
