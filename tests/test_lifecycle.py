import re
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from docker.errors import APIError, ImageNotFound

from rosman.config import parse_config
from rosman.errors import ContainerError, RosmanError
from rosman.lifecycle import (
    IMAGE_GID,
    IMAGE_UID,
    ContainerManager,
    compute_config_hash,
    render_dockerfile,
)
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


def make_manager(tmp_path: Path, client=None) -> ContainerManager:
    client = client or MagicMock()
    state = RosmanState.load(tmp_path / "state.json")
    return ContainerManager(client, state)


def test_ensure_image_returns_cached_when_local_image_exists(tmp_path: Path):
    client = MagicMock()
    client.images.get.return_value = MagicMock()
    manager = make_manager(tmp_path, client)
    config = make_config(tmp_path)

    result = manager.ensure_image(config, "abc123")

    assert result.source == "cached"
    client.images.build.assert_not_called()


def test_ensure_image_pulls_when_registry_image_available(tmp_path: Path):
    client = MagicMock()
    client.images.get.side_effect = ImageNotFound("not found locally")
    manager = make_manager(tmp_path, client)
    config = make_config(tmp_path, "registry_image: ghcr.io/team/proj\n")

    result = manager.ensure_image(config, "abc123")

    assert result.source == "pulled"
    assert result.tag == "ghcr.io/team/proj:abc123"
    client.images.build.assert_not_called()


def test_ensure_image_falls_back_to_build_when_pull_fails(tmp_path: Path):
    client = MagicMock()
    client.images.get.side_effect = ImageNotFound("not found locally")
    client.images.pull.side_effect = ImageNotFound("not found in registry")
    manager = make_manager(tmp_path, client)
    config = make_config(tmp_path, "registry_image: ghcr.io/team/proj\n")

    result = manager.ensure_image(config, "abc123")

    assert result.source == "built"
    client.images.build.assert_called_once()


def test_ensure_image_builds_locally_without_registry_image(tmp_path: Path):
    client = MagicMock()
    client.images.get.side_effect = ImageNotFound("not found locally")
    manager = make_manager(tmp_path, client)
    config = make_config(tmp_path)

    result = manager.ensure_image(config, "abc123")

    assert result.source == "built"
    client.images.pull.assert_not_called()


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


def test_push_image_wraps_api_error(tmp_path: Path):
    client = MagicMock()
    client.images.get.return_value = MagicMock()
    client.images.push.side_effect = APIError("connection refused")
    manager = make_manager(tmp_path, client)
    config = make_config(tmp_path, "registry_image: ghcr.io/team/proj\n")

    with pytest.raises(ContainerError, match="docker login"):
        manager.push_image(config)
