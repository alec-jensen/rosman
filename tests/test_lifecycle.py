import re
import subprocess
from pathlib import Path

import pytest

from rosman.config import parse_config
from rosman.errors import ContainerError
from rosman.lifecycle import compute_config_hash, render_dockerfile


def make_config(tmp_path: Path, extra: str = ""):
    path = tmp_path / "rosman.yml"
    path.write_text(f"ros_distro: humble\n{extra}")
    return parse_config(path.read_text(), path)


def test_render_dockerfile_default_uses_ros_image(tmp_path: Path):
    config = make_config(tmp_path)
    dockerfile = render_dockerfile(config, uid=1000, gid=1000)
    assert dockerfile.startswith("FROM ros:humble\n")
    assert "ros-humble-rmw-cyclonedds-cpp" in dockerfile
    assert "python3-colcon-common-extensions" in dockerfile
    assert "COPY" not in dockerfile


def test_render_dockerfile_with_base_image_installs_ros(tmp_path: Path):
    config = make_config(tmp_path, "base_image: nvidia/cuda:12.4.1-devel-ubuntu22.04\n")
    dockerfile = render_dockerfile(config, uid=1000, gid=1000)
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
    dockerfile = render_dockerfile(config, uid=1000, gid=1000)

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
        render_dockerfile(config, uid=1000, gid=1000)


def test_render_dockerfile_with_setup_script_copies_and_runs_it(tmp_path: Path):
    config = make_config(tmp_path, "setup_script: docker/setup.sh\n")
    dockerfile = render_dockerfile(config, uid=1000, gid=1000)
    assert "COPY rosman-setup.sh /tmp/rosman-setup.sh" in dockerfile
    assert "RUN /tmp/rosman-setup.sh" in dockerfile
    assert "rm /tmp/rosman-setup.sh" in dockerfile


def test_compute_config_hash_changes_with_base_image(tmp_path: Path):
    plain = make_config(tmp_path)
    with_base = make_config(tmp_path, "base_image: nvidia/cuda:12.4.1-devel-ubuntu22.04\n")
    assert compute_config_hash(plain, 1000, 1000) != compute_config_hash(with_base, 1000, 1000)


def test_compute_config_hash_changes_when_setup_script_content_changes(tmp_path: Path):
    script = tmp_path / "setup.sh"
    script.write_text("#!/bin/bash\necho v1\n")
    config = make_config(tmp_path, "setup_script: setup.sh\n")
    hash_v1 = compute_config_hash(config, 1000, 1000)

    script.write_text("#!/bin/bash\necho v2\n")
    hash_v2 = compute_config_hash(config, 1000, 1000)

    assert hash_v1 != hash_v2


def test_compute_config_hash_stable_for_missing_setup_script(tmp_path: Path):
    # A dangling setup_script path shouldn't crash hash computation --
    # ensure_image is what raises the real error, at build time.
    config = make_config(tmp_path, "setup_script: does_not_exist.sh\n")
    assert compute_config_hash(config, 1000, 1000)
