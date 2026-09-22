from unittest.mock import MagicMock

from rosman.rosdep import install_packages, parse_simulate_output, resolve_packages


def test_parse_simulate_output_single_apt_package():
    text = (
        "#[apt] Installation commands:\n"
        "  sudo -H apt-get install -y ros-humble-example-interfaces\n"
    )
    apt, pip = parse_simulate_output(text)
    assert apt == ["ros-humble-example-interfaces"]
    assert pip == []


def test_parse_simulate_output_multiple_apt_packages():
    text = (
        "#[apt] Installation commands:\n"
        "  sudo -H apt-get install -y ros-humble-example-interfaces\n"
        "  sudo -H apt-get install -y ros-humble-turtlesim\n"
    )
    apt, _pip = parse_simulate_output(text)
    assert apt == ["ros-humble-example-interfaces", "ros-humble-turtlesim"]


def test_parse_simulate_output_dedupes_and_sorts():
    text = (
        "sudo -H apt-get install -y b-pkg\n"
        "sudo -H apt-get install -y a-pkg\n"
        "sudo -H apt-get install -y a-pkg\n"
    )
    apt, _pip = parse_simulate_output(text)
    assert apt == ["a-pkg", "b-pkg"]


def test_parse_simulate_output_nothing_missing():
    assert parse_simulate_output("") == ([], [])
    assert parse_simulate_output("#[apt] All required rosdeps installed\n") == ([], [])


def test_parse_simulate_output_ignores_unrelated_lines():
    text = "executing command\nsome other apt-get update noise\n"
    assert parse_simulate_output(text) == ([], [])


def test_parse_simulate_output_pip_package():
    # Regression test: confirmed against a real rosdep key
    # (adafruit-ads1x15-pip) that pip-resolved deps use a completely
    # different command shape than apt ones -- silently dropping these
    # meant `rosman rosdep install` claimed "nothing to install" for a
    # workspace whose only unmet dependency was pip-only.
    text = (
        "#[pip] Installation commands:\n"
        "  sudo -H --preserve-env=PIP_BREAK_SYSTEM_PACKAGES pip3 install -U "
        "Adafruit-ADS1x15\n"
    )
    apt, pip = parse_simulate_output(text)
    assert apt == []
    assert pip == ["Adafruit-ADS1x15"]


def test_parse_simulate_output_mixed_apt_and_pip():
    text = (
        "#[apt] Installation commands:\n"
        "  sudo -H apt-get install -y ros-humble-example-interfaces\n"
        "#[pip] Installation commands:\n"
        "  sudo -H --preserve-env=PIP_BREAK_SYSTEM_PACKAGES pip3 install -U "
        "Adafruit-ADS1x15\n"
    )
    apt, pip = parse_simulate_output(text)
    assert apt == ["ros-humble-example-interfaces"]
    assert pip == ["Adafruit-ADS1x15"]


def test_resolve_packages_runs_simulate_and_parses(monkeypatch):
    container = MagicMock()
    container.exec_run.return_value = (
        0,
        b"sudo -H apt-get install -y ros-humble-example-interfaces\n",
    )

    exit_code, apt, pip, _output = resolve_packages(container, [])

    assert exit_code == 0
    assert apt == ["ros-humble-example-interfaces"]
    assert pip == []
    argv = container.exec_run.call_args[0][0]
    command = argv[-1]
    assert "--simulate" in command
    assert "--from-paths /workspace/src" in command
    assert "--ignore-src" in command


def test_resolve_packages_surfaces_nonzero_exit_code(monkeypatch):
    # Regression test: an unresolvable rosdep key makes `--simulate` exit 1
    # with an ERROR message and no installation-command lines -- confirmed
    # against a real container. Silently treating that the same as "zero
    # packages needed" would misreport a real failure as full success.
    container = MagicMock()
    container.exec_run.return_value = (
        1,
        b"ERROR: the following packages/stacks could not have their rosdep "
        b"keys resolved to system dependencies:\nbadpkg: Cannot locate rosdep "
        b"definition for [nonexistent_key]\n",
    )

    exit_code, apt, pip, output = resolve_packages(container, [])

    assert exit_code == 1
    assert apt == []
    assert pip == []
    assert "Cannot locate rosdep definition" in output


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


def test_resolve_packages_returns_empty_when_genuinely_satisfied():
    container = MagicMock()
    container.exec_run.return_value = (0, b"#[apt] All required rosdeps installed\n")

    exit_code, apt, pip, _output = resolve_packages(container, [])

    assert exit_code == 0
    assert apt == []
    assert pip == []
