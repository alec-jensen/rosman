import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

from rosman.config import parse_config
from rosman.docker_labels import (
    CONFIG_HASH_LABEL,
    DISTRO_LABEL,
    DOMAIN_ID_LABEL,
    NETWORK_GROUP_LABEL,
    NETWORK_MODE_LABEL,
    REMOTE_PEERS_LABEL,
    RESTART_POLICY_LABEL,
)
from rosman.fast_dispatch import _inspect, try_fast_passthrough
from rosman.runtime_config import compute_config_hash


def make_config(tmp_path: Path):
    path = tmp_path / "rosman.yml"
    return parse_config("ros_distro: humble\ndomain_id: 7\n", path)


def matching_labels(config) -> dict[str, str]:
    return {
        DISTRO_LABEL: "humble",
        CONFIG_HASH_LABEL: compute_config_hash(config),
        NETWORK_GROUP_LABEL: "default",
        NETWORK_MODE_LABEL: "host",
        DOMAIN_ID_LABEL: "7",
        RESTART_POLICY_LABEL: "no",
        REMOTE_PEERS_LABEL: "[]",
    }


def test_inspect_parses_status_and_labels(monkeypatch):
    result = MagicMock(
        returncode=0,
        stdout="true\n" + json.dumps({DISTRO_LABEL: "humble"}) + "\n",
    )
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: result)
    assert _inspect("/usr/bin/docker", "container") == (
        True,
        {DISTRO_LABEL: "humble"},
    )


def test_inspect_returns_none_on_failure(monkeypatch):
    result = MagicMock(returncode=1, stdout="")
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: result)
    assert _inspect("/usr/bin/docker", "container") is None


def test_warm_matching_container_dispatches_without_full_lifecycle(tmp_path: Path, monkeypatch):
    config = make_config(tmp_path)
    monkeypatch.setattr("rosman.fast_dispatch.resolve_config", lambda: config)
    monkeypatch.setattr("rosman.fast_dispatch.shutil.which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(
        "rosman.fast_dispatch._inspect", lambda docker, name: (True, matching_labels(config))
    )
    monkeypatch.setattr("rosman.fast_dispatch._maybe_update", lambda state: None)
    dispatched = MagicMock(return_value=42)
    monkeypatch.setattr("rosman.fast_dispatch.dispatch_passthrough", dispatched)

    assert try_fast_passthrough(["topic", "list"]) == 42
    dispatched.assert_called_once()
    assert dispatched.call_args.args[0] == ["topic", "list"]


def test_stopped_matching_container_starts_then_dispatches(tmp_path: Path, monkeypatch):
    config = make_config(tmp_path)
    monkeypatch.setattr("rosman.fast_dispatch.resolve_config", lambda: config)
    monkeypatch.setattr("rosman.fast_dispatch.shutil.which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(
        "rosman.fast_dispatch._inspect", lambda docker, name: (False, matching_labels(config))
    )
    monkeypatch.setattr("rosman.fast_dispatch._start", lambda docker, name: True)
    monkeypatch.setattr("rosman.fast_dispatch._maybe_update", lambda state: None)
    dispatched = MagicMock(return_value=42)
    monkeypatch.setattr("rosman.fast_dispatch.dispatch_passthrough", dispatched)
    assert try_fast_passthrough(["topic", "list"]) == 42


def test_stopped_container_start_failure_falls_back(tmp_path: Path, monkeypatch):
    config = make_config(tmp_path)
    monkeypatch.setattr("rosman.fast_dispatch.resolve_config", lambda: config)
    monkeypatch.setattr("rosman.fast_dispatch.shutil.which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(
        "rosman.fast_dispatch._inspect", lambda docker, name: (False, matching_labels(config))
    )
    monkeypatch.setattr("rosman.fast_dispatch._start", lambda docker, name: False)
    assert try_fast_passthrough(["topic", "list"]) is None


def test_drifted_container_falls_back_to_full_lifecycle(tmp_path: Path, monkeypatch):
    config = make_config(tmp_path)
    labels = matching_labels(config)
    labels[CONFIG_HASH_LABEL] = "stale"
    monkeypatch.setattr("rosman.fast_dispatch.resolve_config", lambda: config)
    monkeypatch.setattr("rosman.fast_dispatch.shutil.which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr("rosman.fast_dispatch._inspect", lambda docker, name: (True, labels))
    assert try_fast_passthrough(["topic", "list"]) is None


def test_missing_container_falls_back_to_full_lifecycle(tmp_path: Path, monkeypatch):
    config = make_config(tmp_path)
    monkeypatch.setattr("rosman.fast_dispatch.resolve_config", lambda: config)
    monkeypatch.setattr("rosman.fast_dispatch.shutil.which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr("rosman.fast_dispatch._inspect", lambda docker, name: None)
    assert try_fast_passthrough(["topic", "list"]) is None


def test_remote_peers_fall_back_for_peer_file_refresh(tmp_path: Path, monkeypatch):
    config = parse_config(
        "ros_distro: humble\ndomain_id: 7\nremote_peers: [192.0.2.10]\n",
        tmp_path / "rosman.yml",
    )
    monkeypatch.setattr("rosman.fast_dispatch.resolve_config", lambda: config)
    assert try_fast_passthrough(["topic", "list"]) is None


def test_removed_remote_peers_fall_back_for_peer_file_refresh(tmp_path: Path, monkeypatch):
    config = make_config(tmp_path)
    labels = matching_labels(config)
    labels[REMOTE_PEERS_LABEL] = '["192.0.2.10"]'
    monkeypatch.setattr("rosman.fast_dispatch.resolve_config", lambda: config)
    monkeypatch.setattr("rosman.fast_dispatch.shutil.which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr("rosman.fast_dispatch._inspect", lambda docker, name: (True, labels))
    assert try_fast_passthrough(["topic", "list"]) is None
