from pathlib import Path

import pytest

from rosman.config import ConfigError, find_config_file, load_config, parse_config, resolve_config

MINIMAL = "ros_distro: humble\n"


def test_parse_minimal_config_applies_defaults(tmp_path: Path):
    path = tmp_path / "rosman.yml"
    config = parse_config(MINIMAL, path)
    assert config.ros_distro == "humble"
    assert config.rmw_implementation == "cyclonedds"
    assert config.domain_id == "auto"
    assert config.network == "default"
    assert config.gpu is False
    assert config.devices == []
    assert config.workspace_dir == "."
    assert config.extra_apt_packages == []
    assert config.restart_policy == "no"
    assert config.config_path == path


def test_parse_full_config(tmp_path: Path):
    path = tmp_path / "rosman.yml"
    text = """
ros_distro: jazzy
rmw_implementation: cyclonedds
domain_id: 42
network: fleet
gpu: true
devices: ["/dev/ttyUSB0"]
workspace_dir: ros_ws
extra_apt_packages: ["ros-jazzy-nav2-bringup"]
restart_policy: unless-stopped
"""
    config = parse_config(text, path)
    assert config.ros_distro == "jazzy"
    assert config.domain_id == 42
    assert config.network == "fleet"
    assert config.gpu is True
    assert config.devices == ["/dev/ttyUSB0"]
    assert config.workspace_dir == "ros_ws"
    assert config.extra_apt_packages == ["ros-jazzy-nav2-bringup"]
    assert config.restart_policy == "unless-stopped"
    assert config.workspace_root == (path.parent / "ros_ws").resolve()


def test_invalid_restart_policy(tmp_path: Path):
    path = tmp_path / "rosman.yml"
    with pytest.raises(ConfigError, match="restart_policy"):
        parse_config("ros_distro: humble\nrestart_policy: always-and-forever\n", path)


def test_base_image_and_setup_script_default_to_none(tmp_path: Path):
    path = tmp_path / "rosman.yml"
    config = parse_config(MINIMAL, path)
    assert config.base_image is None
    assert config.setup_script is None


def test_base_image_and_setup_script_parse(tmp_path: Path):
    path = tmp_path / "rosman.yml"
    text = (
        "ros_distro: humble\n"
        "base_image: nvidia/cuda:12.4.1-devel-ubuntu22.04\n"
        "setup_script: docker/install_zed_sdk.sh\n"
    )
    config = parse_config(text, path)
    assert config.base_image == "nvidia/cuda:12.4.1-devel-ubuntu22.04"
    assert config.setup_script == "docker/install_zed_sdk.sh"


def test_empty_base_image_is_rejected(tmp_path: Path):
    path = tmp_path / "rosman.yml"
    with pytest.raises(ConfigError, match="base_image"):
        parse_config('ros_distro: humble\nbase_image: ""\n', path)


def test_setup_script_rejects_absolute_path(tmp_path: Path):
    path = tmp_path / "rosman.yml"
    with pytest.raises(ConfigError, match="setup_script"):
        parse_config("ros_distro: humble\nsetup_script: /etc/passwd\n", path)


def test_setup_script_rejects_parent_traversal(tmp_path: Path):
    path = tmp_path / "rosman.yml"
    with pytest.raises(ConfigError, match="setup_script"):
        parse_config("ros_distro: humble\nsetup_script: ../../etc/passwd\n", path)


def test_setup_script_rejects_windows_absolute_path(tmp_path: Path):
    # rosman.yml is a portable config file that may be checked into a repo
    # shared across Linux and Windows machines, so a Windows-style absolute
    # path must be rejected even when rosman itself is running on Linux
    # (where `Path` alone wouldn't recognize it as absolute).
    path = tmp_path / "rosman.yml"
    with pytest.raises(ConfigError, match="setup_script"):
        parse_config(
            "ros_distro: humble\nsetup_script: 'C:\\Windows\\System32\\evil.sh'\n", path
        )


def test_missing_required_field(tmp_path: Path):
    path = tmp_path / "rosman.yml"
    with pytest.raises(ConfigError, match="ros_distro"):
        parse_config("rmw_implementation: cyclonedds\n", path)


def test_unknown_field(tmp_path: Path):
    path = tmp_path / "rosman.yml"
    with pytest.raises(ConfigError, match="unrecognized"):
        parse_config("ros_distro: humble\nfoo: bar\n", path)


def test_unknown_distro(tmp_path: Path):
    path = tmp_path / "rosman.yml"
    with pytest.raises(ConfigError, match="unknown ros_distro"):
        parse_config("ros_distro: bogus\n", path)


def test_ros1_distro_gets_specific_error(tmp_path: Path):
    path = tmp_path / "rosman.yml"
    with pytest.raises(ConfigError, match="ROS 1"):
        parse_config("ros_distro: noetic\n", path)


def test_invalid_domain_id(tmp_path: Path):
    path = tmp_path / "rosman.yml"
    with pytest.raises(ConfigError, match="domain_id"):
        parse_config("ros_distro: humble\ndomain_id: 9999\n", path)


def test_invalid_yaml(tmp_path: Path):
    path = tmp_path / "rosman.yml"
    with pytest.raises(ConfigError, match="not valid YAML"):
        parse_config("ros_distro: [unterminated\n", path)


def test_empty_file(tmp_path: Path):
    path = tmp_path / "rosman.yml"
    with pytest.raises(ConfigError, match="empty"):
        parse_config("", path)


def test_find_config_file_walks_up(tmp_path: Path):
    root = tmp_path / "project"
    nested = root / "src" / "pkg"
    nested.mkdir(parents=True)
    (root / "rosman.yml").write_text(MINIMAL)

    found = find_config_file(nested)
    assert found == root / "rosman.yml"


def test_find_config_file_prefers_dotfile_variant(tmp_path: Path):
    (tmp_path / ".rosman.yml").write_text(MINIMAL)
    found = find_config_file(tmp_path)
    assert found == tmp_path / ".rosman.yml"


def test_find_config_file_returns_none_when_absent(tmp_path: Path):
    assert find_config_file(tmp_path) is None


def test_resolve_config_raises_clean_error_when_absent(tmp_path: Path):
    with pytest.raises(ConfigError, match="rosman init"):
        resolve_config(tmp_path)


def test_load_config_reads_file(tmp_path: Path):
    path = tmp_path / "rosman.yml"
    path.write_text(MINIMAL)
    config = load_config(path)
    assert config.ros_distro == "humble"
