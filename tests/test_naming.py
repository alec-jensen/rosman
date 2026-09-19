from pathlib import Path

from rosman.naming import container_name, image_name, network_name, volume_name, workspace_hash


def test_container_name_is_deterministic(tmp_path: Path):
    assert container_name(tmp_path) == container_name(tmp_path)


def test_container_name_differs_per_workspace(tmp_path: Path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    assert container_name(a) != container_name(b)


def test_container_name_is_docker_safe(tmp_path: Path):
    weird = tmp_path / "My Cool Project!!"
    weird.mkdir()
    name = container_name(weird)
    assert all(c.isalnum() or c in "-_." for c in name)


def test_image_name_includes_distro_and_hash(tmp_path: Path):
    name = image_name(tmp_path, "humble", "abc123")
    assert "humble" in name
    assert name.endswith(":abc123")


def test_image_name_with_registry_image_ignores_workspace_path(tmp_path: Path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    # Two different local workspace paths must produce the SAME image name
    # when registry_image is set -- that's the whole point: a team shares
    # one tag regardless of whose machine (and which path) built it.
    name_a = image_name(a, "humble", "abc123", registry_image="ghcr.io/team/proj")
    name_b = image_name(b, "humble", "abc123", registry_image="ghcr.io/team/proj")
    assert name_a == name_b == "ghcr.io/team/proj:abc123"


def test_network_name_slugifies_group():
    assert network_name("My Fleet") == "rosman-net-my-fleet"


def test_volume_name_includes_kind(tmp_path: Path):
    name = volume_name(tmp_path, "humble", "build")
    assert name.endswith("-build")
    assert "humble" in name


def test_workspace_hash_stable_length(tmp_path: Path):
    assert len(workspace_hash(tmp_path)) == 8
    assert len(workspace_hash(tmp_path, length=16)) == 16
