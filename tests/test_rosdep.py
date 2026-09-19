from unittest.mock import MagicMock

from rosman.rosdep import install_packages, parse_simulate_output, resolve_packages


def test_parse_simulate_output_single_package():
    text = (
        "#[apt] Installation commands:\n"
        "  sudo -H apt-get install -y ros-humble-example-interfaces\n"
    )
    assert parse_simulate_output(text) == ["ros-humble-example-interfaces"]


def test_parse_simulate_output_multiple_packages():
    text = (
        "#[apt] Installation commands:\n"
        "  sudo -H apt-get install -y ros-humble-example-interfaces\n"
        "  sudo -H apt-get install -y ros-humble-turtlesim\n"
    )
    assert parse_simulate_output(text) == ["ros-humble-example-interfaces", "ros-humble-turtlesim"]


def test_parse_simulate_output_dedupes_and_sorts():
    text = (
        "sudo -H apt-get install -y b-pkg\n"
        "sudo -H apt-get install -y a-pkg\n"
        "sudo -H apt-get install -y a-pkg\n"
    )
    assert parse_simulate_output(text) == ["a-pkg", "b-pkg"]


def test_parse_simulate_output_nothing_missing():
    assert parse_simulate_output("") == []
    assert parse_simulate_output("#[apt] All required rosdeps installed\n") == []


def test_parse_simulate_output_ignores_unrelated_lines():
    text = "executing command\nsome other apt-get update noise\n"
    assert parse_simulate_output(text) == []


def test_resolve_packages_runs_simulate_and_parses(monkeypatch):
    container = MagicMock()
    container.exec_run.return_value = (
        0,
        b"sudo -H apt-get install -y ros-humble-example-interfaces\n",
    )

    result = resolve_packages(container, [])

    assert result == ["ros-humble-example-interfaces"]
    argv = container.exec_run.call_args[0][0]
    command = argv[-1]
    assert "--simulate" in command
    assert "--from-paths /workspace/src" in command
    assert "--ignore-src" in command


def test_install_packages_runs_apt_update_first():
    container = MagicMock()
    container.exec_run.return_value = (0, b"Setting up ros-humble-example-interfaces ...\n")

    exit_code, output = install_packages(container, [])

    assert exit_code == 0
    assert "Setting up" in output
    argv = container.exec_run.call_args[0][0]
    command = argv[-1]
    assert command.startswith("sudo apt-get update &&")
    assert "--simulate" not in command


def test_resolve_packages_passes_through_extra_args(monkeypatch):
    container = MagicMock()
    container.exec_run.return_value = (0, b"")

    resolve_packages(container, ["--rosdistro", "humble"])

    argv = container.exec_run.call_args[0][0]
    command = argv[-1]
    assert "--rosdistro humble" in command
