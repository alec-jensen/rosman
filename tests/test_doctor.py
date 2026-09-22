from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from docker.errors import NotFound

from rosman import doctor as doctor_mod
from rosman.config import parse_config
from rosman.lifecycle import ImageResult


def make_config(tmp_path: Path):
    path = tmp_path / "rosman.yml"
    # network_mode forced explicitly: these tests exercise the bridge-mode
    # round trip specifically (see the test_*_host_mode_* tests below for
    # the other path) -- "auto" would resolve to "host" on the Linux
    # machines these tests actually run on (CI included), silently testing
    # the wrong code path and calling the real (unmocked)
    # refresh_peers_host_mode, which touches the real state directory.
    return parse_config("ros_distro: humble\nnetwork: fleet\nnetwork_mode: bridge\n", path)


class FakeManager:
    """Duck-types the two ContainerManager methods run_network_roundtrip
    actually calls, so the test doesn't need a real Docker-backed manager."""

    def __init__(self, domain_id: int = 7, image: str = "rosman/test:abc"):
        self.domain_id = domain_id
        self.image = image

    def resolve_domain_id(self, config):
        return self.domain_id

    def ensure_image(self, config, config_hash, reporter=None):
        return ImageResult(tag=self.image, source="built")


def _patch_networking(monkeypatch, tmp_path):
    monkeypatch.setattr(
        doctor_mod,
        "ensure_network",
        lambda client, group: SimpleNamespace(name=f"rosman-net-{group}"),
    )
    monkeypatch.setattr(
        doctor_mod, "refresh_peers", lambda client, group: tmp_path / "cyclonedds.xml"
    )


def test_run_network_roundtrip_success(monkeypatch, tmp_path: Path):
    config = make_config(tmp_path)
    _patch_networking(monkeypatch, tmp_path)

    talker, listener = MagicMock(), MagicMock()
    listener.exec_run.return_value = (0, b'data: "rosman doctor"\n')

    client = MagicMock()
    client.containers.get.side_effect = NotFound("not found")
    client.containers.create.side_effect = [talker, listener]

    check = doctor_mod.run_network_roundtrip(client, FakeManager(), config)

    assert check.ok
    talker.exec_run.assert_called_once()
    listener.exec_run.assert_called_once()
    talker.remove.assert_called_once_with(force=True)
    listener.remove.assert_called_once_with(force=True)


def test_run_network_roundtrip_no_message_received(monkeypatch, tmp_path: Path):
    config = make_config(tmp_path)
    _patch_networking(monkeypatch, tmp_path)

    talker, listener = MagicMock(), MagicMock()
    listener.exec_run.return_value = (124, b"")  # `timeout` killed it: nothing arrived

    client = MagicMock()
    client.containers.get.side_effect = NotFound("not found")
    client.containers.create.side_effect = [talker, listener]

    check = doctor_mod.run_network_roundtrip(client, FakeManager(), config)

    assert not check.ok
    assert "never received" in check.detail
    # Cleanup must still happen even on failure.
    talker.remove.assert_called_once_with(force=True)
    listener.remove.assert_called_once_with(force=True)


def test_run_network_roundtrip_cleans_up_on_exception(monkeypatch, tmp_path: Path):
    from docker.errors import APIError

    config = make_config(tmp_path)
    _patch_networking(monkeypatch, tmp_path)

    talker, listener = MagicMock(), MagicMock()
    listener.exec_run.side_effect = APIError("boom")

    client = MagicMock()
    client.containers.get.side_effect = NotFound("not found")
    client.containers.create.side_effect = [talker, listener]

    check = doctor_mod.run_network_roundtrip(client, FakeManager(), config)

    assert not check.ok
    talker.remove.assert_called_once_with(force=True)
    listener.remove.assert_called_once_with(force=True)


def make_host_mode_config(tmp_path: Path):
    path = tmp_path / "rosman.yml"
    return parse_config("ros_distro: humble\nnetwork_mode: host\n", path)


def _patch_networking_host(monkeypatch, tmp_path):
    monkeypatch.setattr(doctor_mod, "refresh_peers_host_mode", lambda *a: tmp_path / "cdds.xml")


def test_run_network_roundtrip_host_mode_skips_bridge_network(monkeypatch, tmp_path: Path):
    config = make_host_mode_config(tmp_path)
    _patch_networking_host(monkeypatch, tmp_path)

    talker, listener = MagicMock(), MagicMock()
    listener.exec_run.return_value = (0, b'data: "rosman doctor"\n')

    client = MagicMock()
    client.containers.get.side_effect = NotFound("not found")
    client.containers.create.side_effect = [talker, listener]

    check = doctor_mod.run_network_roundtrip(client, FakeManager(), config)

    assert check.ok
    client.networks.get.assert_not_called()
    client.networks.create.assert_not_called()
    _, kwargs = client.containers.create.call_args_list[0]
    assert kwargs["network_mode"] == "host"
    assert kwargs["network"] is None


def test_usbipd_hint_without_binary(monkeypatch):
    monkeypatch.setattr(doctor_mod.shutil, "which", lambda _name: None)
    hint = doctor_mod._usbipd_hint("/dev/ttyUSB0")
    assert "winget install usbipd" in hint


def test_run_checks_network_check_defaults_to_skipped(monkeypatch, tmp_path: Path):
    config = make_config(tmp_path)
    monkeypatch.setattr(doctor_mod, "get_client", lambda: MagicMock())

    from rosman.lifecycle import ContainerManager

    monkeypatch.setattr(
        ContainerManager, "find_container", lambda self, config: None
    )

    checks = doctor_mod.run_checks(config, network_check=False)
    roundtrip = next(c for c in checks if c.name == "networking round-trip")
    assert roundtrip.ok
    assert "skipped" in roundtrip.detail


def make_remote_peers_config(tmp_path: Path):
    path = tmp_path / "rosman.yml"
    return parse_config(
        'ros_distro: humble\ndomain_id: 5\nremote_peers: ["192.168.1.51"]\n', path
    )


def test_run_checks_reports_lan_ip_when_remote_peers_configured(monkeypatch, tmp_path: Path):
    from rosman.lifecycle import ContainerManager

    config = make_remote_peers_config(tmp_path)
    monkeypatch.setattr(doctor_mod, "get_client", lambda: MagicMock())
    monkeypatch.setattr(ContainerManager, "find_container", lambda self, config: None)
    monkeypatch.setattr(doctor_mod, "detect_lan_ip", lambda: "192.168.1.50")

    checks = doctor_mod.run_checks(config, network_check=False)
    check = next(c for c in checks if c.name == "multi-host (LAN)")
    assert check.ok
    assert "192.168.1.50" in check.detail
    assert "8669" in check.detail  # dds_port_range(5).stop - 1


def test_run_checks_fails_lan_check_when_ip_undetectable(monkeypatch, tmp_path: Path):
    from rosman.lifecycle import ContainerManager

    config = make_remote_peers_config(tmp_path)
    monkeypatch.setattr(doctor_mod, "get_client", lambda: MagicMock())
    monkeypatch.setattr(ContainerManager, "find_container", lambda self, config: None)
    monkeypatch.setattr(doctor_mod, "detect_lan_ip", lambda: None)

    checks = doctor_mod.run_checks(config, network_check=False)
    check = next(c for c in checks if c.name == "multi-host (LAN)")
    assert not check.ok


def test_run_checks_skips_lan_check_without_remote_peers(monkeypatch, tmp_path: Path):
    from rosman.lifecycle import ContainerManager

    config = make_config(tmp_path)
    monkeypatch.setattr(doctor_mod, "get_client", lambda: MagicMock())
    monkeypatch.setattr(ContainerManager, "find_container", lambda self, config: None)

    checks = doctor_mod.run_checks(config, network_check=False)
    assert not any(c.name == "multi-host (LAN)" for c in checks)


def test_run_checks_reports_explicit_network_mode(monkeypatch, tmp_path: Path):
    from rosman.lifecycle import ContainerManager

    config = make_config(tmp_path)  # forces network_mode: bridge
    monkeypatch.setattr(doctor_mod, "get_client", lambda: MagicMock())
    monkeypatch.setattr(ContainerManager, "find_container", lambda self, config: None)

    checks = doctor_mod.run_checks(config, network_check=False)
    check = next(c for c in checks if c.name == "network mode")
    assert check.ok
    assert check.detail == "bridge"


def test_run_checks_reports_auto_network_mode_with_note(monkeypatch, tmp_path: Path):
    from rosman.lifecycle import ContainerManager

    path = tmp_path / "rosman.yml"
    config = parse_config("ros_distro: humble\n", path)  # network_mode left at "auto"
    monkeypatch.setattr(doctor_mod, "get_client", lambda: MagicMock())
    monkeypatch.setattr(ContainerManager, "find_container", lambda self, config: None)

    checks = doctor_mod.run_checks(config, network_check=False)
    check = next(c for c in checks if c.name == "network mode")
    assert check.ok
    assert "auto-detected" in check.detail
