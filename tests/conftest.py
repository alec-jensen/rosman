from pathlib import Path

import pytest


@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    """A bare temp directory standing in for a project root."""
    return tmp_path
