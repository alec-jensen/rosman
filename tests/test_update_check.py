import json
import subprocess
import urllib.error
from datetime import timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

from rosman.state import RosmanState
from rosman.update_check import (
    CHECK_INTERVAL,
    NOTIFY_INTERVAL,
    _now,
    check_for_update,
    pending_notice,
    run_scheduled_update_check,
    schedule_update_check,
)


def make_state(tmp_path: Path) -> RosmanState:
    return RosmanState.load(tmp_path / "state.json")


def fake_response(tag_name: str):
    body = json.dumps({"tag_name": tag_name}).encode()
    cm = MagicMock()
    cm.__enter__.return_value.read.return_value = body
    return cm


def test_check_for_update_fetches_and_caches_latest_version(tmp_path: Path):
    state = make_state(tmp_path)
    with patch("urllib.request.urlopen", return_value=fake_response("v0.2.0")):
        check_for_update(state)
    assert state.update_check.latest_version == "0.2.0"
    assert state.update_check.last_checked is not None


def test_check_for_update_skips_when_not_due(tmp_path: Path):
    state = make_state(tmp_path)
    state.update_check.last_checked = _now().isoformat()
    state.update_check.latest_version = "0.1.0"
    state.save()

    with patch("urllib.request.urlopen") as mock_urlopen:
        check_for_update(state)
    mock_urlopen.assert_not_called()


def test_check_for_update_runs_again_after_interval_elapses(tmp_path: Path):
    state = make_state(tmp_path)
    state.update_check.last_checked = (_now() - CHECK_INTERVAL - timedelta(seconds=1)).isoformat()
    state.save()

    with patch("urllib.request.urlopen", return_value=fake_response("v0.3.0")):
        check_for_update(state)
    assert state.update_check.latest_version == "0.3.0"


def test_check_for_update_swallows_network_errors(tmp_path: Path):
    state = make_state(tmp_path)
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("no network")):
        check_for_update(state)  # must not raise
    assert state.update_check.latest_version is None
    assert state.update_check.last_checked is not None  # still recorded the attempt


def test_check_for_update_swallows_malformed_json(tmp_path: Path):
    state = make_state(tmp_path)
    cm = MagicMock()
    cm.__enter__.return_value.read.return_value = b"not json"
    with patch("urllib.request.urlopen", return_value=cm):
        check_for_update(state)  # must not raise
    assert state.update_check.latest_version is None


def test_pending_notice_none_when_no_latest_known(tmp_path: Path):
    state = make_state(tmp_path)
    assert pending_notice(state, "0.1.0") is None


def test_pending_notice_none_when_versions_match(tmp_path: Path):
    state = make_state(tmp_path)
    state.update_check.latest_version = "0.1.0"
    assert pending_notice(state, "0.1.0") is None


def test_pending_notice_fires_when_newer_version_cached(tmp_path: Path):
    state = make_state(tmp_path)
    state.update_check.latest_version = "0.2.0"
    notice = pending_notice(state, "0.1.0")
    assert notice is not None
    assert "0.2.0" in notice
    assert "0.1.0" in notice


def test_pending_notice_throttled_to_once_per_interval(tmp_path: Path):
    state = make_state(tmp_path)
    state.update_check.latest_version = "0.2.0"

    first = pending_notice(state, "0.1.0")
    second = pending_notice(state, "0.1.0")

    assert first is not None
    assert second is None  # already notified within NOTIFY_INTERVAL


def test_pending_notice_fires_again_after_interval_elapses(tmp_path: Path):
    state = make_state(tmp_path)
    state.update_check.latest_version = "0.2.0"
    state.update_check.last_notified = (_now() - NOTIFY_INTERVAL - timedelta(seconds=1)).isoformat()

    assert pending_notice(state, "0.1.0") is not None


def test_schedule_update_check_spawns_detached_worker_when_due(tmp_path: Path):
    state = make_state(tmp_path)
    with patch("subprocess.Popen") as popen:
        schedule_update_check(state)

    saved = RosmanState.load(state.path)
    assert saved.update_check.last_checked is not None
    command = popen.call_args.args[0]
    assert "__check_update" in command
    assert str(state.path) in command
    assert popen.call_args.kwargs["stdout"] == subprocess.DEVNULL


def test_schedule_update_check_does_not_spawn_when_not_due(tmp_path: Path):
    state = make_state(tmp_path)
    state.update_check.last_checked = _now().isoformat()
    with patch("subprocess.Popen") as popen:
        schedule_update_check(state)
    popen.assert_not_called()


def test_scheduled_worker_refreshes_cache_even_after_parent_marked_attempt(tmp_path: Path):
    state = make_state(tmp_path)
    state.update_check.last_checked = _now().isoformat()
    state.save()
    with patch("urllib.request.urlopen", return_value=fake_response("v0.4.3")):
        run_scheduled_update_check(state.path)
    assert RosmanState.load(state.path).update_check.latest_version == "0.4.3"


def test_scheduled_worker_keeps_state_changes_made_during_fetch(tmp_path: Path):
    state = make_state(tmp_path)
    state.save()

    def update_state_during_fetch(*args, **kwargs):
        concurrent = RosmanState.load(state.path)
        concurrent.assign_domain_id(tmp_path / "workspace")
        concurrent.update_check.last_notified = _now().isoformat()
        concurrent.save()
        return fake_response("v0.4.3")

    with patch("urllib.request.urlopen", side_effect=update_state_during_fetch):
        run_scheduled_update_check(state.path)

    saved = RosmanState.load(state.path)
    assert saved.update_check.latest_version == "0.4.3"
    assert saved.update_check.last_notified is not None
    assert saved.get_project(tmp_path / "workspace").domain_id is not None
