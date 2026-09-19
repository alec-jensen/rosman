from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from docker.errors import NotFound

from rosman import doctor as doctor_mod
from rosman.config import parse_config


def make_config(tmp_path: Path):
    path = tmp_path / "rosman.yml"
    return parse_config("ros_distro: humble\nnetwork: fleet\n", path)


class FakeManager:
    """Duck-types the two ContainerManager methods run_network_roundtrip
    actually calls, so the test doesn't need a real Docker-backed manager."""

    def __init__(self, domain_id: int = 7, image: str = "rosman/test:abc"):
        self.domain_id = domain_id
        self.image = image

    def resolve_domain_id(self, config):
        return self.domain_id

    def ensure_image(self, config, config_hash):
        return self.image


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
