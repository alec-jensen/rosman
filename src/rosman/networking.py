"""Networking layer: shared bridge networks + CycloneDDS unicast peer config.

Design (see spec §5): rosman never uses `--network host`, since host
networking on Windows Docker Desktop is unreliable for multicast/port
forwarding. Instead:

  - Every container in the same `network:` group (from rosman.yml) joins one
    rosman-managed Docker bridge network, named `rosman-net-<group>`.
    Docker's embedded DNS makes containers resolvable by name on it, so peer
    IPs never need to be hardcoded.
  - A single CycloneDDS XML file is generated per network group, listing
    every rosman-managed container currently on that network as a unicast
    discovery peer, and is bind-mounted read-only into every container in
    the group at the same host path. Because it's one shared file rather
    than one copy per container, updating it once (via `refresh_peers`)
    is instantly visible to every container without touching each one
    individually or restarting anything — CycloneDDS re-reads
    `CYCLONEDDS_URI` at process startup, and rosman only ever starts new
    ROS 2 processes via fresh `docker exec` calls, so the very next
    `rosman ...` invocation in any container picks up the current peer set.
"""

from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import quoteattr

import docker
from docker.errors import NotFound

from rosman.docker_client import MANAGED_LABEL, NETWORK_GROUP_LABEL
from rosman.naming import network_name
from rosman.state import state_dir

CYCLONEDDS_CONTAINER_PATH = "/etc/rosman/cyclonedds.xml"
RMW_IMPLEMENTATION_ENV = "rmw_cyclonedds_cpp"


def cyclonedds_host_path(group: str) -> Path:
    path = state_dir() / "net" / group
    path.mkdir(parents=True, exist_ok=True)
    return path / "cyclonedds.xml"


def ensure_network(client: docker.DockerClient, group: str) -> docker.models.networks.Network:
    """Create (or fetch) the rosman-managed bridge network for this group."""
    name = network_name(group)
    try:
        return client.networks.get(name)
    except NotFound:
        return client.networks.create(
            name,
            driver="bridge",
            labels={MANAGED_LABEL: "true", NETWORK_GROUP_LABEL: group},
        )


def peer_container_names(
    client: docker.DockerClient, group: str, exclude: str | None = None
) -> list[str]:
    """Names of other rosman-managed containers currently attached to this
    network group's bridge network (running or not — a stopped peer's name
    still resolves once it's started again, so listing it is harmless)."""
    net = ensure_network(client, group)
    net.reload()
    names = [
        attrs["Name"]
        for attrs in (net.attrs.get("Containers") or {}).values()
        if attrs.get("Name") and attrs["Name"] != exclude
    ]
    return sorted(names)


def render_cyclonedds_xml(peers: list[str]) -> str:
    peer_elements = "\n".join(f'        <Peer address={quoteattr(name)} />' for name in peers)
    peers_block = f"      <Peers>\n{peer_elements}\n      </Peers>" if peers else "      <Peers />"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<CycloneDDS xmlns="https://cdds.io/config">
  <Domain id="any">
    <General>
      <Interfaces>
        <NetworkInterface autodetermine="true" />
      </Interfaces>
      <AllowMulticast>false</AllowMulticast>
    </General>
    <Discovery>
{peers_block}
      <ParticipantIndex>auto</ParticipantIndex>
    </Discovery>
  </Domain>
</CycloneDDS>
"""


def refresh_peers(client: docker.DockerClient, group: str) -> Path:
    """Regenerate the shared CycloneDDS peers file for a network group.

    Returns the host path that's bind-mounted into every container in the
    group; callers don't need to touch any container to make the update
    take effect (see module docstring).
    """
    peers = peer_container_names(client, group)
    host_path = cyclonedds_host_path(group)
    host_path.write_text(render_cyclonedds_xml(peers))
    return host_path
