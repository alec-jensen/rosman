from pathlib import Path

from rosman.config import parse_config
from rosman.dispatch import RESERVED_COMMANDS, translate_cwd


def make_config(tmp_path: Path):
    path = tmp_path / "rosman.yml"
    return parse_config("ros_distro: humble\n", path)


def test_translate_cwd_at_workspace_root(tmp_path: Path):
    config = make_config(tmp_path)
    assert translate_cwd(config, tmp_path) == "/workspace"


def test_translate_cwd_in_subdirectory(tmp_path: Path):
    config = make_config(tmp_path)
    sub = tmp_path / "src" / "my_pkg"
    sub.mkdir(parents=True)
    assert translate_cwd(config, sub) == "/workspace/src/my_pkg"


def test_translate_cwd_outside_workspace_falls_back(tmp_path: Path):
    config = make_config(tmp_path)
    outside = tmp_path.parent
    assert translate_cwd(config, outside) == "/workspace"


def test_reserved_commands_do_not_shadow_common_ros2_verbs():
    # ros2's own top-level verbs must never collide with rosman's namespace.
    ros2_verbs = {"topic", "launch", "run", "node", "service", "param", "bag", "action", "pkg"}
    assert RESERVED_COMMANDS.isdisjoint(ros2_verbs)
