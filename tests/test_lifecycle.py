import re
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from docker.errors import APIError, ImageNotFound, NotFound

from rosman.config import parse_config
from rosman.docker_labels import WORKSPACE_LABEL
from rosman.errors import ContainerError, RosmanError
from rosman.lifecycle import (
    DEVICE_GROUPS,
    IMAGE_GID,
    IMAGE_UID,
    ContainerManager,
    ImageResult,
    compute_config_hash,
    list_managed_images,
    list_prunable_images,
    remove_images,
    render_dockerfile,
    resolve_network_mode,
    resolve_ports,
)
from rosman.naming import workspace_hash
from rosman.networking import dds_port_range
from rosman.state import RosmanState


def make_config(tmp_path: Path, extra: str = ""):
    path = tmp_path / "rosman.yml"
    path.write_text(f"ros_distro: humble\n{extra}")
    return parse_config(path.read_text(), path)


def test_render_dockerfile_default_uses_ros_image(tmp_path: Path):
    config = make_config(tmp_path)
    dockerfile = render_dockerfile(config)
    assert dockerfile.startswith("FROM ros:humble\n")
    assert "ros-humble-rmw-cyclonedds-cpp" in dockerfile
    assert "python3-colcon-common-extensions" in dockerfile
    assert "COPY" not in dockerfile


def test_render_dockerfile_installs_and_initializes_rosdep(tmp_path: Path):
    config = make_config(tmp_path)
    dockerfile = render_dockerfile(config)
    assert "python3-rosdep" in dockerfile
    assert "python3-pip" in dockerfile
    assert "rosdep init" in dockerfile
    assert "rosdep update" in dockerfile


def test_render_dockerfile_no_pip_install_step_without_locked_pip_packages(tmp_path: Path):
    config = make_config(tmp_path)
    dockerfile = render_dockerfile(config)
    assert "pip3 install --no-cache-dir" not in dockerfile


def test_render_dockerfile_installs_locked_pip_packages(tmp_path: Path):
    config = make_config(tmp_path)
    config.locked_pip_packages = ["Adafruit-ADS1x15"]
    dockerfile = render_dockerfile(config)
    assert "PIP_BREAK_SYSTEM_PACKAGES=1 pip3 install --no-cache-dir Adafruit-ADS1x15" in dockerfile


def test_render_dockerfile_includes_locked_apt_packages(tmp_path: Path):
    config = make_config(tmp_path)
    config.locked_apt_packages = ["ros-humble-example-interfaces"]
    dockerfile = render_dockerfile(config)
    assert "ros-humble-example-interfaces" in dockerfile


def test_render_dockerfile_dedupes_extra_and_locked_packages(tmp_path: Path):
    config = make_config(tmp_path, 'extra_apt_packages: ["ros-humble-example-interfaces"]\n')
    config.locked_apt_packages = ["ros-humble-example-interfaces"]
    dockerfile = render_dockerfile(config)
    assert dockerfile.count("ros-humble-example-interfaces") == 1


def test_project_package_change_keeps_common_ros_setup_cacheable(tmp_path: Path):
    plain = render_dockerfile(make_config(tmp_path))
    with_package = render_dockerfile(make_config(tmp_path, "extra_apt_packages: [curl]\n"))
    project_step = "RUN apt-get update \\\n    && apt-get install -y --no-install-recommends curl"
    assert project_step in with_package
    stable_prefix = with_package.split(project_step)[0]
    assert plain.startswith(stable_prefix)
    assert "rosdep update" in stable_prefix
    assert '\nENTRYPOINT ["' not in stable_prefix
    assert "/etc/profile.d/rosman-ros.sh" in stable_prefix
    assert "rosman-entrypoint.sh" in stable_prefix


def test_render_dockerfile_sets_noninteractive_before_any_apt_install(tmp_path: Path):
    # Regression test: on a bare Ubuntu base_image (unlike ros:<distro>,
    # which already sets this), installing ca-certificates pulls in tzdata,
    # whose postinst prompts interactively for a timezone -- and hangs
    # forever in a non-interactive `docker build` with no TTY to answer it.
    # Caught via an actual build hanging 20+ minutes on `dpkg --configure
    # tzdata` before being traced to this. DEBIAN_FRONTEND must be set
    # before the *first* `apt-get install` line, not just present somewhere.
    config = make_config(tmp_path, "base_image: nvidia/cuda:12.4.1-devel-ubuntu22.04\n")
    dockerfile = render_dockerfile(config)
    env_index = dockerfile.index("ENV DEBIAN_FRONTEND=noninteractive")
    # Match an actual `apt-get install` invocation, not this test file's own
    # description of one -- a real prior bug in this test matched a comment.
    first_install_match = re.search(r"^\s*(&&\s*)?apt-get install\b", dockerfile, re.MULTILINE)
    assert first_install_match is not None, dockerfile
    assert env_index < first_install_match.start()


def test_render_dockerfile_with_base_image_installs_ros(tmp_path: Path):
    config = make_config(tmp_path, "base_image: nvidia/cuda:12.4.1-devel-ubuntu22.04\n")
    dockerfile = render_dockerfile(config)
    assert dockerfile.startswith("FROM nvidia/cuda:12.4.1-devel-ubuntu22.04\n")
    assert "ros-archive-keyring.gpg" in dockerfile
    assert "jammy main" in dockerfile  # humble -> jammy
    assert "ros-humble-ros-base" in dockerfile


def test_ros2_apt_source_line_has_no_stray_whitespace(tmp_path: Path):
    # Regression test: the `echo "deb [...] ..."` shell fragment is built
    # from Dockerfile continuation lines. Splitting a *quoted* shell string
    # across those lines previously left the continuation lines' leading
    # indentation embedded literally in the value. Extract just that `echo`
    # invocation and run it for real to confirm the apt source line it
    # produces is exactly correct, not just "close enough".
    config = make_config(tmp_path, "base_image: nvidia/cuda:12.4.1-devel-ubuntu22.04\n")
    dockerfile = render_dockerfile(config)

    match = re.search(r'echo "deb \[.*?main"', dockerfile, re.DOTALL)
    assert match is not None, dockerfile
    echo_command = match.group(0)

    result = subprocess.run(
        ["bash", "-c", echo_command], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == (
        "deb [arch=amd64 signed-by=/usr/share/keyrings/ros-archive-keyring.gpg] "
        "http://packages.ros.org/ros2/ubuntu jammy main"
    )


def test_render_dockerfile_unknown_distro_with_base_image_raises(tmp_path: Path, monkeypatch):
    from rosman import lifecycle

    monkeypatch.delitem(lifecycle.UBUNTU_CODENAME_FOR_DISTRO, "humble")
    config = make_config(tmp_path, "base_image: nvidia/cuda:12.4.1-devel-ubuntu22.04\n")
    with pytest.raises(ContainerError, match="codename"):
        render_dockerfile(config)


def test_render_dockerfile_with_setup_script_copies_and_runs_it(tmp_path: Path):
    config = make_config(tmp_path, "setup_script: docker/setup.sh\n")
    dockerfile = render_dockerfile(config)
    assert "COPY rosman-setup.sh /tmp/rosman-setup.sh" in dockerfile
    assert "RUN /tmp/rosman-setup.sh" in dockerfile
    assert "rm /tmp/rosman-setup.sh" in dockerfile


def test_compute_config_hash_changes_with_base_image(tmp_path: Path):
    plain = make_config(tmp_path)
    with_base = make_config(tmp_path, "base_image: nvidia/cuda:12.4.1-devel-ubuntu22.04\n")
    assert compute_config_hash(plain) != compute_config_hash(with_base)


def test_compute_config_hash_changes_with_locked_apt_packages(tmp_path: Path):
    plain = make_config(tmp_path)
    locked = make_config(tmp_path)
    locked.locked_apt_packages = ["ros-humble-example-interfaces"]
    assert compute_config_hash(plain) != compute_config_hash(locked)


def test_compute_config_hash_changes_with_locked_pip_packages(tmp_path: Path):
    plain = make_config(tmp_path)
    locked = make_config(tmp_path)
    locked.locked_pip_packages = ["Adafruit-ADS1x15"]
    assert compute_config_hash(plain) != compute_config_hash(locked)


def test_compute_config_hash_changes_when_setup_script_content_changes(tmp_path: Path):
    script = tmp_path / "setup.sh"
    script.write_text("#!/bin/bash\necho v1\n")
    config = make_config(tmp_path, "setup_script: setup.sh\n")
    hash_v1 = compute_config_hash(config)

    script.write_text("#!/bin/bash\necho v2\n")
    hash_v2 = compute_config_hash(config)

    assert hash_v1 != hash_v2


def test_compute_config_hash_stable_for_missing_setup_script(tmp_path: Path):
    # A dangling setup_script path shouldn't crash hash computation --
    # ensure_image is what raises the real error, at build time.
    config = make_config(tmp_path, "setup_script: does_not_exist.sh\n")
    assert compute_config_hash(config)


def test_render_dockerfile_uses_fixed_image_identity(tmp_path: Path):
    # Regression test: the image bakes in a FIXED uid/gid, never the
    # builder's own host uid/gid -- required for a `registry_image` tag to
    # mean the same thing regardless of which teammate built it.
    config = make_config(tmp_path)
    dockerfile = render_dockerfile(config)
    assert f"--uid {IMAGE_UID} --gid {IMAGE_GID}" in dockerfile


def test_render_dockerfile_sudoers_allows_any_user(tmp_path: Path):
    config = make_config(tmp_path)
    dockerfile = render_dockerfile(config)
    assert "ALL ALL=(ALL) NOPASSWD:ALL" in dockerfile


def test_render_dockerfile_includes_entrypoint_and_world_writable_paths(tmp_path: Path):
    config = make_config(tmp_path)
    dockerfile = render_dockerfile(config)
    assert 'ENTRYPOINT ["/usr/local/bin/rosman-entrypoint.sh"]' in dockerfile
    assert "chmod 0777 /home/rosman" in dockerfile
    assert "chmod 0666 /etc/passwd" in dockerfile
    assert "chmod -R 0777 /workspace" in dockerfile


def test_render_dockerfile_ensures_device_groups_exist(tmp_path: Path):
    # Regression test: `--device` only grants cgroup-level access -- the
    # device node's own Unix permissions (usually group-owned, e.g.
    # dialout/video/disk at mode 0660) still block the container's non-root
    # user without these groups actually existing in the image, since
    # create_container's group_add=DEVICE_GROUPS has nothing to resolve
    # against otherwise. Some minimal base_image overrides may not define
    # all of them by default (unlike the stock ros:<distro> image), so the
    # build must create whichever are missing rather than assume they exist.
    config = make_config(tmp_path)
    dockerfile = render_dockerfile(config)
    match = re.search(r"for grp in (.*?); do", dockerfile)
    assert match is not None, dockerfile
    assert match.group(1).split() == DEVICE_GROUPS


def make_manager(tmp_path: Path, client=None) -> ContainerManager:
    client = client or MagicMock()
    state = RosmanState.load(tmp_path / "state.json")
    return ContainerManager(client, state)


@pytest.mark.parametrize("network_mode", ["host", "bridge"])
def test_ensure_running_refreshes_cyclonedds_for_existing_container(
    tmp_path: Path, monkeypatch, network_mode: str
):
    config = make_config(tmp_path, f"network_mode: {network_mode}\n")
    manager = make_manager(tmp_path)
    container = MagicMock(status="running")
    monkeypatch.setattr(manager, "find_container", lambda config: container)
    refresh_host = MagicMock()
    refresh_bridge = MagicMock()
    monkeypatch.setattr("rosman.lifecycle.refresh_peers_host_mode", refresh_host)
    monkeypatch.setattr("rosman.lifecycle.refresh_peers", refresh_bridge)

    assert manager.ensure_running(config) == (container, False)

    if network_mode == "host":
        refresh_host.assert_called_once_with(config.remote_peers)
        refresh_bridge.assert_not_called()
    else:
        refresh_bridge.assert_called_once_with(manager.client, config.network, config.remote_peers)
        refresh_host.assert_not_called()


def test_ensure_image_returns_cached_when_local_image_exists(tmp_path: Path):
    client = MagicMock()
    client.images.get.return_value = MagicMock()
    manager = make_manager(tmp_path, client)
    config = make_config(tmp_path)

    result = manager.ensure_image(config, "abc123")

    assert result.source == "cached"
    client.api.build.assert_not_called()


def test_ensure_image_pulls_when_registry_image_available(tmp_path: Path):
    client = MagicMock()
    client.images.get.side_effect = ImageNotFound("not found locally")
    client.api.pull.return_value = iter([{"status": "Pull complete", "id": "layer1"}])
    manager = make_manager(tmp_path, client)
    config = make_config(tmp_path, "registry_image: ghcr.io/team/proj\n")

    result = manager.ensure_image(config, "abc123")

    assert result.source == "pulled"
    assert result.tag == "ghcr.io/team/proj:abc123"
    client.api.build.assert_not_called()


def test_ensure_image_falls_back_to_build_when_pull_fails(tmp_path: Path):
    client = MagicMock()
    client.images.get.side_effect = ImageNotFound("not found locally")
    client.api.pull.side_effect = ImageNotFound("not found in registry")
    client.api.build.return_value = iter([{"stream": "Successfully built abc123\n"}])
    manager = make_manager(tmp_path, client)
    config = make_config(tmp_path, "registry_image: ghcr.io/team/proj\n")

    result = manager.ensure_image(config, "abc123")

    assert result.source == "built"
    client.api.build.assert_called_once()


def test_ensure_image_builds_locally_without_registry_image(tmp_path: Path):
    client = MagicMock()
    client.images.get.side_effect = ImageNotFound("not found locally")
    client.api.build.return_value = iter([{"stream": "Successfully built abc123\n"}])
    manager = make_manager(tmp_path, client)
    config = make_config(tmp_path)

    result = manager.ensure_image(config, "abc123")

    assert result.source == "built"
    client.api.pull.assert_not_called()


def test_ensure_image_failure_shows_full_setup_script_output(tmp_path: Path):
    (tmp_path / "setup.sh").write_text("#!/bin/sh\nexit 42\n")
    client = MagicMock()
    client.images.get.side_effect = ImageNotFound("not found locally")
    diagnostic = "setup.sh: permission denied [" + "x" * 150 + "]"
    client.api.build.return_value = iter(
        [
            {"stream": f"{diagnostic}\n"},
            {"error": "The command returned a non-zero code: 42"},
        ]
    )
    manager = make_manager(tmp_path, client)
    config = make_config(tmp_path, "setup_script: setup.sh\n")

    with pytest.raises(ContainerError) as error:
        manager.ensure_image(config, "abc123")

    assert diagnostic in str(error.value)
    assert "non-zero code: 42" in str(error.value)
    assert "Docker build output:" in str(error.value)


def test_push_image_requires_registry_image_configured(tmp_path: Path):
    manager = make_manager(tmp_path)
    config = make_config(tmp_path)
    with pytest.raises(RosmanError, match="registry_image"):
        manager.push_image(config)


def test_push_image_pushes_and_returns_tag(tmp_path: Path):
    client = MagicMock()
    client.images.get.return_value = MagicMock()
    client.images.push.return_value = iter([{"status": "pushed layer"}])
    manager = make_manager(tmp_path, client)
    config = make_config(tmp_path, "registry_image: ghcr.io/team/proj\n")

    tag = manager.push_image(config)

    expected_hash = compute_config_hash(config)
    assert tag == f"ghcr.io/team/proj:{expected_hash}"
    client.images.push.assert_called_once()


def test_push_image_raises_on_push_error_entry(tmp_path: Path):
    client = MagicMock()
    client.images.get.return_value = MagicMock()
    client.images.push.return_value = iter([{"error": "denied: permission_denied"}])
    manager = make_manager(tmp_path, client)
    config = make_config(tmp_path, "registry_image: ghcr.io/team/proj\n")

    with pytest.raises(ContainerError, match="permission_denied"):
        manager.push_image(config)


def test_create_container_grants_device_groups_when_devices_configured(
    tmp_path: Path, monkeypatch
):
    # Regression test: `--device` alone only grants cgroup-level access to
    # the node -- its own Unix permissions (typically group-owned, mode
    # 0660) still blocked the container's non-root user from actually
    # opening it without `group_add`. Caught live: a usbipd-attached USB
    # device was visible via `rosman doctor`/`lsblk` inside the container
    # but reading it raised "Permission denied" for the default user.
    monkeypatch.setattr("rosman.lifecycle.state_dir", lambda: tmp_path)
    monkeypatch.setattr("rosman.networking.state_dir", lambda: tmp_path)
    client = MagicMock()
    manager = make_manager(tmp_path, client)
    monkeypatch.setattr(
        manager,
        "ensure_image",
        lambda config, config_hash, reporter=None: ImageResult("tag", "cached"),
    )
    config = make_config(tmp_path, 'devices: ["/dev/ttyUSB0"]\n')

    manager.create_container(config)

    _, kwargs = client.containers.create.call_args
    assert kwargs["group_add"] == DEVICE_GROUPS


def test_create_container_no_group_add_without_devices(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("rosman.lifecycle.state_dir", lambda: tmp_path)
    monkeypatch.setattr("rosman.networking.state_dir", lambda: tmp_path)
    client = MagicMock()
    manager = make_manager(tmp_path, client)
    monkeypatch.setattr(
        manager,
        "ensure_image",
        lambda config, config_hash, reporter=None: ImageResult("tag", "cached"),
    )
    config = make_config(tmp_path)

    manager.create_container(config)

    _, kwargs = client.containers.create.call_args
    assert kwargs["group_add"] is None


def test_create_container_publishes_dds_ports_when_remote_peers_configured(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setattr("rosman.lifecycle.state_dir", lambda: tmp_path)
    monkeypatch.setattr("rosman.networking.state_dir", lambda: tmp_path)
    client = MagicMock()
    manager = make_manager(tmp_path, client)
    monkeypatch.setattr(
        manager,
        "ensure_image",
        lambda config, config_hash, reporter=None: ImageResult("tag", "cached"),
    )
    # network_mode: bridge forced explicitly -- port publishing is a
    # bridge-only concept (host mode needs none at all, see
    # test_create_container_host_mode_needs_no_port_publishing), and CI
    # runs on Linux where `auto` would otherwise resolve to host mode.
    config = make_config(
        tmp_path, 'domain_id: 5\nnetwork_mode: bridge\nremote_peers: ["192.168.1.51"]\n'
    )

    manager.create_container(config)

    _, kwargs = client.containers.create.call_args
    expected = {f"{p}/udp": p for p in dds_port_range(5)}
    assert kwargs["ports"] == expected


def test_create_container_no_ports_without_remote_peers(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("rosman.lifecycle.state_dir", lambda: tmp_path)
    monkeypatch.setattr("rosman.networking.state_dir", lambda: tmp_path)
    client = MagicMock()
    manager = make_manager(tmp_path, client)
    monkeypatch.setattr(
        manager,
        "ensure_image",
        lambda config, config_hash, reporter=None: ImageResult("tag", "cached"),
    )
    config = make_config(tmp_path)

    manager.create_container(config)

    _, kwargs = client.containers.create.call_args
    assert kwargs["ports"] is None


def test_resolve_network_mode_auto_is_platform_dependent(monkeypatch):
    import platform as platform_mod

    from rosman.config import parse_config as _parse

    config = _parse("ros_distro: humble\n", Path("/tmp/rosman.yml"))
    monkeypatch.setattr(platform_mod, "system", lambda: "Linux")
    assert resolve_network_mode(config) == "host"
    monkeypatch.setattr(platform_mod, "system", lambda: "Windows")
    assert resolve_network_mode(config) == "bridge"


def test_resolve_network_mode_explicit_overrides_platform(tmp_path: Path, monkeypatch):
    import platform as platform_mod

    monkeypatch.setattr(platform_mod, "system", lambda: "Linux")
    bridge_config = make_config(tmp_path, "network_mode: bridge\n")
    assert resolve_network_mode(bridge_config) == "bridge"

    monkeypatch.setattr(platform_mod, "system", lambda: "Windows")
    host_config = make_config(tmp_path, "network_mode: host\n")
    assert resolve_network_mode(host_config) == "host"


def test_resolve_ports_merges_declared_ports_and_dds_range(tmp_path: Path):
    config = make_config(
        tmp_path, 'ports: ["10000:10000", "8080"]\nremote_peers: ["192.168.1.51"]\ndomain_id: 5\n'
    )
    ports = resolve_ports(config, 5)
    assert ports["10000/tcp"] == 10000
    assert ports["8080/tcp"] == 8080
    for p in dds_port_range(5):
        assert ports[f"{p}/udp"] == p


def test_resolve_ports_none_when_nothing_declared(tmp_path: Path):
    config = make_config(tmp_path)
    assert resolve_ports(config, 0) is None


def test_create_container_host_mode_needs_no_port_publishing_or_bridge_network(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setattr("rosman.lifecycle.state_dir", lambda: tmp_path)
    monkeypatch.setattr("rosman.networking.state_dir", lambda: tmp_path)
    client = MagicMock()
    manager = make_manager(tmp_path, client)
    monkeypatch.setattr(
        manager,
        "ensure_image",
        lambda config, config_hash, reporter=None: ImageResult("tag", "cached"),
    )
    # remote_peers set to prove host mode skips DDS port publishing too,
    # not just the plain "no ports declared" case.
    config = make_config(
        tmp_path, 'network_mode: host\ndomain_id: 5\nremote_peers: ["192.168.1.51"]\n'
    )

    manager.create_container(config)

    _, kwargs = client.containers.create.call_args
    assert kwargs["ports"] is None
    assert kwargs["network_mode"] == "host"
    assert kwargs["labels"]["rosman.remote_peers"] == '["192.168.1.51"]'
    client.networks.get.assert_not_called()
    client.networks.create.assert_not_called()


def test_create_container_bridge_mode_still_joins_network(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("rosman.lifecycle.state_dir", lambda: tmp_path)
    monkeypatch.setattr("rosman.networking.state_dir", lambda: tmp_path)
    client = MagicMock()
    manager = make_manager(tmp_path, client)
    monkeypatch.setattr(
        manager,
        "ensure_image",
        lambda config, config_hash, reporter=None: ImageResult("tag", "cached"),
    )
    config = make_config(tmp_path, "network_mode: bridge\n")

    manager.create_container(config)

    _, kwargs = client.containers.create.call_args
    assert kwargs["network_mode"] is None
    client.networks.get.return_value.connect.assert_called_once()


def test_push_image_wraps_api_error(tmp_path: Path):
    client = MagicMock()
    client.images.get.return_value = MagicMock()
    client.images.push.side_effect = APIError("connection refused")
    manager = make_manager(tmp_path, client)
    config = make_config(tmp_path, "registry_image: ghcr.io/team/proj\n")

    with pytest.raises(ContainerError, match="docker login"):
        manager.push_image(config)


def _images_list_side_effect(labeled, unfiltered):
    def _side_effect(*args, **kwargs):
        return labeled if kwargs.get("filters") else unfiltered

    return _side_effect


def test_list_managed_images_finds_labeled_images():
    client = MagicMock()
    labeled_image = MagicMock(id="sha256:aaa", tags=["rosman/humble-abcd1234:def012345678"])
    client.images.list.side_effect = _images_list_side_effect([labeled_image], [labeled_image])

    assert list_managed_images(client) == [labeled_image]


def test_list_managed_images_finds_legacy_tagged_images_without_label():
    # Regression: images built before MANAGED_LABEL existed must still be
    # visible to `rosman prune`, identified by tag pattern instead.
    legacy_image = MagicMock(id="sha256:bbb", tags=["rosman/humble-12345678:abcdef123456"])
    unrelated_image = MagicMock(id="sha256:ccc", tags=["ubuntu:22.04"])
    client = MagicMock()
    client.images.list.side_effect = _images_list_side_effect(
        [], [legacy_image, unrelated_image]
    )

    assert list_managed_images(client) == [legacy_image]


def test_list_managed_images_dedupes_labeled_and_legacy():
    # A labeled image also shows up in the unfiltered `images.list()` --
    # must not be counted/returned twice just because it happens to also
    # match the legacy tag pattern.
    image = MagicMock(id="sha256:aaa", tags=["rosman/humble-12345678:abcdef123456"])
    client = MagicMock()
    client.images.list.side_effect = _images_list_side_effect([image], [image])

    result = list_managed_images(client)

    assert result == [image]


def _prune_image(image_id, tags, created, workspace=None):
    image = MagicMock(id=image_id, tags=tags)
    labels = {WORKSPACE_LABEL: str(workspace)} if workspace else {}
    image.attrs = {"Created": created, "Config": {"Labels": labels}}
    return image


def test_list_prunable_images_keeps_latest_without_container(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repo = f"rosman/humble-{workspace_hash(workspace)}"
    old = _prune_image("sha256:old", [f"{repo}:000000000001"], "2026-09-20T00:00:00Z", workspace)
    latest = _prune_image(
        "sha256:latest", [f"{repo}:000000000002"], "2026-09-21T00:00:00Z", workspace
    )
    monkeypatch.setattr("rosman.lifecycle.list_managed_images", lambda client: [old, latest])
    client = MagicMock()
    client.containers.list.return_value = []

    assert list_prunable_images(client) == [old]


def test_list_prunable_images_removes_deleted_workspace_images(tmp_path, monkeypatch):
    deleted_workspace = tmp_path / "deleted"
    repo = f"rosman/humble-{workspace_hash(deleted_workspace)}"
    image = _prune_image(
        "sha256:deleted",
        [f"{repo}:000000000001"],
        "2026-09-21T00:00:00Z",
        deleted_workspace,
    )
    monkeypatch.setattr("rosman.lifecycle.list_managed_images", lambda client: [image])
    client = MagicMock()
    client.containers.list.return_value = []

    assert list_prunable_images(client) == [image]


def test_list_prunable_images_compares_labeled_and_legacy_for_same_workspace(
    tmp_path, monkeypatch
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repo = f"rosman/humble-{workspace_hash(workspace)}"
    old_legacy = _prune_image(
        "sha256:old", [f"{repo}:000000000001"], "2026-09-20T00:00:00Z"
    )
    latest = _prune_image(
        "sha256:latest", [f"{repo}:000000000002"], "2026-09-21T00:00:00Z", workspace
    )
    monkeypatch.setattr("rosman.lifecycle.list_managed_images", lambda client: [old_legacy, latest])
    client = MagicMock()
    client.containers.list.return_value = []

    assert list_prunable_images(client) == [old_legacy]


def test_list_prunable_images_keeps_latest_registry_image(monkeypatch):
    repo = "ghcr.io/team/project"
    old = _prune_image("sha256:old", [f"{repo}:000000000001"], "2026-09-20T00:00:00Z")
    latest = _prune_image("sha256:latest", [f"{repo}:000000000002"], "2026-09-21T00:00:00Z")
    monkeypatch.setattr("rosman.lifecycle.list_managed_images", lambda client: [old, latest])
    client = MagicMock()
    client.containers.list.return_value = []

    assert list_prunable_images(client) == [old]


def test_list_prunable_images_keeps_latest_legacy_image(monkeypatch):
    repo = "rosman/humble-12345678"
    old = _prune_image("sha256:old", [f"{repo}:000000000001"], "2026-09-20T00:00:00Z")
    latest = _prune_image("sha256:latest", [f"{repo}:000000000002"], "2026-09-21T00:00:00Z")
    monkeypatch.setattr("rosman.lifecycle.list_managed_images", lambda client: [old, latest])
    client = MagicMock()
    client.containers.list.return_value = []

    assert list_prunable_images(client) == [old]


def test_list_prunable_images_keeps_shared_image_used_by_other_workspace(tmp_path, monkeypatch):
    workspace_a = tmp_path / "a"
    workspace_b = tmp_path / "b"
    workspace_a.mkdir()
    workspace_b.mkdir()
    repo_a = f"rosman/humble-{workspace_hash(workspace_a)}"
    repo_b = f"rosman/humble-{workspace_hash(workspace_b)}"
    shared = _prune_image(
        "sha256:shared",
        [f"{repo_a}:000000000001", f"{repo_b}:000000000001"],
        "2026-09-20T00:00:00Z",
        workspace_a,
    )
    newer_a = _prune_image(
        "sha256:newer",
        [f"{repo_a}:000000000002"],
        "2026-09-21T00:00:00Z",
        workspace_a,
    )
    monkeypatch.setattr("rosman.lifecycle.list_managed_images", lambda client: [shared, newer_a])
    client = MagicMock()
    client.containers.list.return_value = []

    assert list_prunable_images(client) == []


def test_list_prunable_images_excludes_referenced(monkeypatch):
    referenced = MagicMock(id="sha256:referenced")
    unreferenced = MagicMock(id="sha256:orphaned")
    monkeypatch.setattr(
        "rosman.lifecycle.list_managed_images", lambda client: [referenced, unreferenced]
    )
    client = MagicMock()
    container = MagicMock()
    container.attrs = {"Image": "sha256:referenced"}
    client.containers.list.return_value = [container]

    assert list_prunable_images(client) == [unreferenced]


def test_list_prunable_images_all_orphaned_when_no_containers(monkeypatch):
    image = MagicMock(id="sha256:aaa")
    monkeypatch.setattr("rosman.lifecycle.list_managed_images", lambda client: [image])
    client = MagicMock()
    client.containers.list.return_value = []

    assert list_prunable_images(client) == [image]


def test_remove_images_removes_each_tag_individually():
    # Regression: `images.remove(image_id, force=False)` refuses outright
    # when multiple tags point at one image ID ("referenced in multiple
    # repositories") -- confirmed against a real Docker daemon. Must
    # untag each reference instead, like `docker rmi tag1 tag2 ...` does.
    client = MagicMock()
    image = MagicMock(id="sha256:aaa", tags=["repo1:tag", "repo2:tag"])
    image.attrs = {"Size": 1000}

    count, reclaimed = remove_images(client, [image])

    assert count == 1
    assert reclaimed == 1000
    client.images.remove.assert_any_call("repo1:tag", force=False)
    client.images.remove.assert_any_call("repo2:tag", force=False)
    assert client.images.remove.call_count == 2


def test_remove_images_falls_back_to_id_when_no_tags():
    client = MagicMock()
    image = MagicMock(id="sha256:aaa", tags=[])
    image.attrs = {"Size": 500}

    count, reclaimed = remove_images(client, [image])

    client.images.remove.assert_called_once_with("sha256:aaa", force=False)
    assert count == 1
    assert reclaimed == 500


def test_remove_images_skips_failures_without_raising():
    client = MagicMock()
    ok_image = MagicMock(id="sha256:aaa", tags=["repo:ok"])
    ok_image.attrs = {"Size": 100}
    bad_image = MagicMock(id="sha256:bbb", tags=["repo:bad"])
    bad_image.attrs = {"Size": 200}

    def remove_side_effect(ref, force):
        if ref == "repo:bad":
            raise APIError("conflict")

    client.images.remove.side_effect = remove_side_effect

    count, reclaimed = remove_images(client, [ok_image, bad_image])

    assert count == 1
    assert reclaimed == 100


def test_remove_images_treats_not_found_as_a_skip_too():
    client = MagicMock()
    image = MagicMock(id="sha256:aaa", tags=["repo:gone"])
    image.attrs = {"Size": 100}
    client.images.remove.side_effect = NotFound("no such image")

    count, reclaimed = remove_images(client, [image])

    assert count == 0
    assert reclaimed == 0


def test_remove_images_sums_across_multiple_images():
    client = MagicMock()
    first = MagicMock(id="sha256:aaa", tags=["repo:a"])
    first.attrs = {"Size": 100}
    second = MagicMock(id="sha256:bbb", tags=["repo:b"])
    second.attrs = {"Size": 250}

    count, reclaimed = remove_images(client, [first, second])

    assert count == 2
    assert reclaimed == 350
