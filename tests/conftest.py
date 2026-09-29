from pathlib import Path

import pytest


@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    """A bare temp directory standing in for a project root."""
    return tmp_path


@pytest.fixture(autouse=True)
def _isolated_state_dir(tmp_path_factory, monkeypatch):
    """Config/env caches live under the XDG state dir; never touch the real one."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path_factory.mktemp("xdg-state")))
