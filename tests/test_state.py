from pathlib import Path

import pytest

from rosman.state import RosmanState


def test_assign_domain_id_is_stable(tmp_path: Path):
    state = RosmanState.load(tmp_path / "state.json")
    workspace = tmp_path / "ws1"
    first = state.assign_domain_id(workspace)
    second = state.assign_domain_id(workspace)
    assert first == second


def test_assign_domain_id_avoids_collisions(tmp_path: Path):
    state = RosmanState.load(tmp_path / "state.json")
    a = state.assign_domain_id(tmp_path / "ws-a")
    b = state.assign_domain_id(tmp_path / "ws-b")
    assert a != b


def test_state_persists_across_loads(tmp_path: Path):
    state_path = tmp_path / "state.json"
    state = RosmanState.load(state_path)
    workspace = tmp_path / "ws1"
    assigned = state.assign_domain_id(workspace)

    reloaded = RosmanState.load(state_path)
    project = reloaded.get_project(workspace)
    assert project.domain_id == assigned


def test_explicit_domain_ids_are_not_reassigned(tmp_path: Path):
    state = RosmanState.load(tmp_path / "state.json")
    workspace = tmp_path / "ws1"
    project = state.get_project(workspace)
    project.domain_id = 99
    state.save()

    assert state.assign_domain_id(workspace) == 99


def test_corrupt_state_file_does_not_crash(tmp_path: Path):
    state_path = tmp_path / "state.json"
    state_path.write_text("{not valid json")
    state = RosmanState.load(state_path)
    assert state.projects == {}


def test_exhausted_domain_id_range_raises(tmp_path: Path):
    from rosman.state import MAX_DOMAIN_ID, MIN_DOMAIN_ID

    state = RosmanState.load(tmp_path / "state.json")
    for i in range(MIN_DOMAIN_ID, MAX_DOMAIN_ID + 1):
        state.get_project(tmp_path / f"ws-{i}").domain_id = i
    state.save()

    with pytest.raises(RuntimeError, match="No free ROS_DOMAIN_ID"):
        state.assign_domain_id(tmp_path / "ws-overflow")
