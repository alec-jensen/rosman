import shlex
from pathlib import Path

import pytest

from rosman import dispatch
from rosman.config import load_config, parse_config
from rosman.dispatch import RESERVED_COMMANDS, dispatch_passthrough, shell_command, translate_cwd
from rosman.errors import RosmanError


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


def test_translate_cwd_resolves_symlinked_alias_into_workspace(tmp_path: Path):
    # A common real-world case: the workspace lives at its real path, but
    # the user reaches it through an unrelated symlink alias (e.g. a
    # `~/current-project -> ~/projects/actual-project-2024` convenience
    # link). os.getcwd()/Path.cwd() always return the real, symlink-
    # resolved path already, so translate_cwd needs no special-casing --
    # this locks that behavior in as a regression test.
    real_ws = tmp_path / "actual_project"
    (real_ws / "src" / "pkg").mkdir(parents=True)
    (real_ws / "rosman.yml").write_text("ros_distro: humble\n")
    config = load_config(real_ws / "rosman.yml")

    link = tmp_path / "linked_project"
    link.symlink_to(real_ws)

    assert translate_cwd(config, link) == "/workspace"
    assert translate_cwd(config, link / "src" / "pkg") == "/workspace/src/pkg"


def test_reserved_commands_do_not_shadow_common_ros2_verbs():
    # ros2's own top-level verbs must never collide with rosman's namespace.
    ros2_verbs = {"topic", "launch", "run", "node", "service", "param", "bag", "action", "pkg"}
    assert RESERVED_COMMANDS.isdisjoint(ros2_verbs)


def test_dispatch_passthrough_wraps_ros2_in_a_login_shell(monkeypatch):
    # Regression test: ros2/colcon only end up on $PATH once setup.bash is
    # sourced, which only happens for a login shell (see /etc/profile.d in
    # lifecycle.py). A bare `docker exec container ros2 ...` fails with
    # "ros2: executable file not found in $PATH" against a real container.
    captured = {}
    monkeypatch.setattr(
        dispatch, "exec_in_container", lambda c, w, cmd: captured.setdefault("command", cmd)
    )
    dispatch_passthrough(["topic", "echo", "/chatter"], "my-container", "/workspace")
    assert captured["command"] == ["bash", "-lc", "ros2 topic echo /chatter"]


def test_dispatch_passthrough_colcon_is_not_prefixed_with_ros2(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        dispatch, "exec_in_container", lambda c, w, cmd: captured.setdefault("command", cmd)
    )
    dispatch_passthrough(["colcon", "build"], "my-container", "/workspace")
    assert captured["command"] == ["bash", "-lc", "colcon build"]


def test_dispatch_passthrough_quotes_arguments_safely(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        dispatch, "exec_in_container", lambda c, w, cmd: captured.setdefault("command", cmd)
    )
    dispatch_passthrough(
        ["topic", "pub", "/chatter", "std_msgs/String", "data: hello world"],
        "my-container",
        "/workspace",
    )
    # The message argument contains a space -- it must survive as one shell
    # word, not be split, once the wrapped command is actually parsed by bash.
    parsed = shlex.split(captured["command"][2])
    assert parsed[-1] == "data: hello world"


def test_dispatch_passthrough_empty_args_raises():
    with pytest.raises(RosmanError):
        dispatch_passthrough([], "my-container", "/workspace")


def test_shell_command_uses_login_shell(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        dispatch, "exec_in_container", lambda c, w, cmd: captured.setdefault("command", cmd)
    )
    shell_command("my-container", "/workspace")
    assert captured["command"] == ["bash", "-l"]
