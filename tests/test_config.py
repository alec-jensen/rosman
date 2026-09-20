from pathlib import Path

import pytest

from rosman.config import (
    ConfigError,
    find_config_file,
    load_config,
    local_override_path,
    lock_path,
    parse_config,
    resolve_config,
    write_lock,
)

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


def test_registry_image_defaults_to_none(tmp_path: Path):
    path = tmp_path / "rosman.yml"
    config = parse_config(MINIMAL, path)
    assert config.registry_image is None


def test_registry_image_parses(tmp_path: Path):
    path = tmp_path / "rosman.yml"
    text = "ros_distro: humble\nregistry_image: ghcr.io/my-team/my-project\n"
    config = parse_config(text, path)
    assert config.registry_image == "ghcr.io/my-team/my-project"


def test_registry_image_allows_registry_port_colon(tmp_path: Path):
    # A colon before the first "/" is a registry host:port, not a tag --
    # must not be confused with the (rejected) "repo:tag" form below.
    path = tmp_path / "rosman.yml"
    text = "ros_distro: humble\nregistry_image: localhost:5000/my-project\n"
    config = parse_config(text, path)
    assert config.registry_image == "localhost:5000/my-project"


def test_registry_image_rejects_embedded_tag(tmp_path: Path):
    path = tmp_path / "rosman.yml"
    with pytest.raises(ConfigError, match="registry_image"):
        parse_config(
            "ros_distro: humble\nregistry_image: ghcr.io/my-team/my-project:latest\n", path
        )


def test_empty_registry_image_is_rejected(tmp_path: Path):
    path = tmp_path / "rosman.yml"
    with pytest.raises(ConfigError, match="registry_image"):
        parse_config('ros_distro: humble\nregistry_image: ""\n', path)


def test_remote_peers_defaults_to_empty(tmp_path: Path):
    path = tmp_path / "rosman.yml"
    config = parse_config(MINIMAL, path)
    assert config.remote_peers == []


def test_remote_peers_parses_with_explicit_domain_id(tmp_path: Path):
    path = tmp_path / "rosman.yml"
    text = "ros_distro: humble\ndomain_id: 5\nremote_peers: [\"192.168.1.51\"]\n"
    config = parse_config(text, path)
    assert config.remote_peers == ["192.168.1.51"]
    assert config.domain_id == 5


def test_remote_peers_rejects_auto_domain_id(tmp_path: Path):
    # An auto-assigned domain_id is chosen independently on each machine, so
    # two machines running the same rosman.yml could silently end up on
    # different domains and never discover each other -- this must be a
    # loud config error, not a quiet runtime failure.
    path = tmp_path / "rosman.yml"
    text = "ros_distro: humble\nremote_peers: [\"192.168.1.51\"]\n"
    with pytest.raises(ConfigError, match="domain_id"):
        parse_config(text, path)


def test_remote_peers_rejects_non_string_list(tmp_path: Path):
    path = tmp_path / "rosman.yml"
    text = "ros_distro: humble\ndomain_id: 5\nremote_peers: [1, 2]\n"
    with pytest.raises(ConfigError, match="remote_peers"):
        parse_config(text, path)


def test_network_mode_defaults_to_auto(tmp_path: Path):
    path = tmp_path / "rosman.yml"
    config = parse_config(MINIMAL, path)
    assert config.network_mode == "auto"


def test_network_mode_accepts_host_and_bridge(tmp_path: Path):
    path = tmp_path / "rosman.yml"
    assert parse_config("ros_distro: humble\nnetwork_mode: host\n", path).network_mode == "host"
    assert (
        parse_config("ros_distro: humble\nnetwork_mode: bridge\n", path).network_mode == "bridge"
    )


def test_network_mode_rejects_unknown_value(tmp_path: Path):
    path = tmp_path / "rosman.yml"
    with pytest.raises(ConfigError, match="network_mode"):
        parse_config("ros_distro: humble\nnetwork_mode: nope\n", path)


def test_ports_defaults_to_empty(tmp_path: Path):
    path = tmp_path / "rosman.yml"
    config = parse_config(MINIMAL, path)
    assert config.ports == []


def test_ports_accepts_bare_and_mapped_entries(tmp_path: Path):
    path = tmp_path / "rosman.yml"
    text = 'ros_distro: humble\nports: ["10000:10000", "8080"]\n'
    config = parse_config(text, path)
    assert config.ports == ["10000:10000", "8080"]


def test_ports_rejects_non_numeric_entries(tmp_path: Path):
    path = tmp_path / "rosman.yml"
    with pytest.raises(ConfigError, match="ports"):
        parse_config('ros_distro: humble\nports: ["abc"]\n', path)


def test_ports_rejects_too_many_colons(tmp_path: Path):
    path = tmp_path / "rosman.yml"
    with pytest.raises(ConfigError, match="ports"):
        parse_config('ros_distro: humble\nports: ["1:2:3"]\n', path)


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


def test_local_override_path_naming():
    assert local_override_path(Path("/proj/rosman.yml")) == Path("/proj/rosman.local.yml")
    assert local_override_path(Path("/proj/.rosman.yaml")) == Path("/proj/.rosman.local.yaml")


def test_local_override_replaces_devices(tmp_path: Path):
    path = tmp_path / "rosman.yml"
    path.write_text("ros_distro: humble\ndevices: [\"/dev/ttyUSB0\"]\n")
    (tmp_path / "rosman.local.yml").write_text('devices: ["/dev/ttyUSB3"]\n')

    config = load_config(path)

    assert config.devices == ["/dev/ttyUSB3"]
    assert config.ros_distro == "humble"  # untouched fields still come from the main file


def test_local_override_is_optional(tmp_path: Path):
    path = tmp_path / "rosman.yml"
    path.write_text(MINIMAL)
    # No rosman.local.yml present -- must resolve exactly as before.
    config = load_config(path)
    assert config.devices == []


def test_local_override_values_are_validated(tmp_path: Path):
    path = tmp_path / "rosman.yml"
    path.write_text(MINIMAL)
    (tmp_path / "rosman.local.yml").write_text("gpu: not-a-bool\n")

    with pytest.raises(ConfigError, match="gpu"):
        load_config(path)


def test_local_override_can_be_malformed_yaml_and_errors_clearly(tmp_path: Path):
    path = tmp_path / "rosman.yml"
    path.write_text(MINIMAL)
    (tmp_path / "rosman.local.yml").write_text("devices: [unterminated\n")

    with pytest.raises(ConfigError, match="rosman.local.yml"):
        load_config(path)


def test_local_override_can_add_unknown_field_to_get_rejected(tmp_path: Path):
    # Unknown-field rejection still applies to the *merged* result -- a
    # typo in the local override shouldn't silently do nothing.
    path = tmp_path / "rosman.yml"
    path.write_text(MINIMAL)
    (tmp_path / "rosman.local.yml").write_text("devcies: [\"/dev/ttyUSB0\"]\n")

    with pytest.raises(ConfigError, match="unrecognized"):
        load_config(path)


def test_resolve_config_applies_local_override(tmp_path: Path):
    path = tmp_path / "rosman.yml"
    path.write_text("ros_distro: humble\ndomain_id: 5\n")
    (tmp_path / "rosman.local.yml").write_text("domain_id: 42\n")

    config = resolve_config(tmp_path)

    assert config.domain_id == 42


def test_lock_path_is_always_rosman_lock(tmp_path: Path):
    assert lock_path(tmp_path / "rosman.yml") == tmp_path / "rosman.lock"
    assert lock_path(tmp_path / ".rosman.yaml") == tmp_path / "rosman.lock"


def test_no_lockfile_means_empty_locked_packages(tmp_path: Path):
    path = tmp_path / "rosman.yml"
    path.write_text(MINIMAL)

    config = load_config(path)

    assert config.locked_apt_packages == []


def test_write_lock_then_load_config_reads_it_back(tmp_path: Path):
    path = tmp_path / "rosman.yml"
    path.write_text(MINIMAL)
    write_lock(path, "humble", ["ros-humble-example-interfaces", "ros-humble-turtlesim"])

    config = load_config(path)

    assert config.locked_apt_packages == ["ros-humble-example-interfaces", "ros-humble-turtlesim"]


def test_write_lock_sorts_and_dedupes(tmp_path: Path):
    path = tmp_path / "rosman.yml"
    path.write_text(MINIMAL)
    write_lock(path, "humble", ["b-pkg", "a-pkg", "a-pkg"])

    config = load_config(path)

    assert config.locked_apt_packages == ["a-pkg", "b-pkg"]


def test_lock_distro_mismatch_is_a_config_error(tmp_path: Path):
    path = tmp_path / "rosman.yml"
    path.write_text("ros_distro: jazzy\n")
    write_lock(path, "humble", ["ros-humble-example-interfaces"])

    with pytest.raises(ConfigError, match="rosman rosdep install"):
        load_config(path)


def test_malformed_lock_apt_packages_is_a_config_error(tmp_path: Path):
    path = tmp_path / "rosman.yml"
    path.write_text(MINIMAL)
    (tmp_path / "rosman.lock").write_text("ros_distro: humble\napt_packages: not-a-list\n")

    with pytest.raises(ConfigError, match="apt_packages"):
        load_config(path)
