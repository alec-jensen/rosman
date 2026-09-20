"""Networking layer: shared bridge networks (or host networking) +
CycloneDDS unicast peer config.

Design (see spec §5, and its sixth addendum for the network_mode revision):
rosman defaults to a per-workspace bridge network on Windows, since host
networking is still documented as unreliable there as of 2026 (real,
current Docker Desktop/WSL2 issues -- not just historical caution). On
Linux (including WSL2, which reports as Linux), `--network host` is fully
reliable and is the default (`network_mode: auto`, see
`lifecycle.resolve_network_mode`) -- it needs no bridge network, no port
publishing, and no per-container DNS name resolution at all, since every
host-mode container already shares the actual machine's network namespace
directly. `network_mode: host`/`bridge` in rosman.yml force one or the
other regardless of platform.

**Bridge mode** (`refresh_peers`, `ensure_network`, `peer_container_names`):
  - Every container in the same `network:` group (from rosman.yml) joins one
    rosman-managed Docker bridge network, named `rosman-net-<group>`.
    Docker's embedded DNS makes containers resolvable by name on it, so peer
    IPs never need to be hardcoded.
  - A single CycloneDDS XML file is generated per network group, listing
    every rosman-managed container currently on that network as a unicast
    discovery peer, and is bind-mounted read-only into every container in
    the group at the same host path.

**Host mode** (`refresh_peers_host_mode`): every host-mode container on this
machine already shares the real network namespace, so same-host discovery
is just `127.0.0.1` -- no Docker network to introspect for peer container
names, and no port publishing for `remote_peers` either (a container's own
bound port already *is* the host's port, reachable from the LAN directly
once the OS firewall allows it). The one tradeoff, inherent to host
networking and not something rosman tries to paper over: two *different*
rosman workspaces on the same host cannot both bind the same *fixed* TCP
port at once (e.g. two workspaces both trying to run something on
port 10000) -- exactly like two native host processes competing for a port.

Both modes share one file-generation mechanism (`render_cyclonedds_xml`),
just fed a different peer list. Updating it once (via either refresh
function) is instantly visible to every container without touching each
one individually or restarting anything — CycloneDDS re-reads
`CYCLONEDDS_URI` at process startup, and rosman only ever starts new ROS 2
processes via fresh `docker exec` calls, so the very next `rosman ...`
invocation in any container picks up the current peer set.

Multi-host (LAN) discovery extends the peers file with entries for other
machines' `remote_peers` (rosman.yml) in both modes -- see docs/spec.md's
third addendum. Alec's explicit call: no VPN mesh, LAN only. This only
works because `remote_peers` requires an explicit (non-"auto") `domain_id`
(enforced in config.py), which two machines running the same checked-in
rosman.yml therefore always agree on -- that in turn lets both sides
independently compute the *same* CycloneDDS discovery port window from
`dds_port_range()` with no coordination needed (used for bridge-mode port
publishing; host mode doesn't need it, but the port numbers it describes
are still accurate for firewall purposes either way, since they come from
Cyclone DDS's own port-selection formula, unrelated to Docker networking).
"""

from __future__ import annotations

import socket
from pathlib import Path
from xml.sax.saxutils import quoteattr

import docker
from docker.errors import NotFound

from rosman.docker_client import MANAGED_LABEL, NETWORK_GROUP_LABEL
from rosman.naming import network_name
from rosman.state import state_dir

CYCLONEDDS_CONTAINER_PATH = "/etc/rosman/cyclonedds.xml"
RMW_IMPLEMENTATION_ENV = "rmw_cyclonedds_cpp"

# Cyclone DDS's own default port formula is `PB + DG*domain_id +
# PG*participant_index + offset(0..3)`. Mirroring it (rather than overriding
# it via config) means both sides of a LAN link compute the same window with
# no handshake. `_PARTICIPANT_WINDOW` covers participant indices 0-7 (most
# workspaces run far fewer separate ROS 2 processes than that concurrently
# in one container) plus Cyclone's 4 port offsets per participant.
_PORT_BASE = 7400
_DOMAIN_GAIN = 250
_PARTICIPANT_GAIN = 2
_MAX_PARTICIPANTS = 8
_PARTICIPANT_WINDOW = _PARTICIPANT_GAIN * _MAX_PARTICIPANTS + 4


def dds_port_range(domain_id: int) -> range:
    """The UDP port window Cyclone DDS will use for this domain id, that
    must be published host:container 1:1 for LAN peers to reach it."""
    base = _PORT_BASE + _DOMAIN_GAIN * domain_id
    return range(base, base + _PARTICIPANT_WINDOW)


def detect_lan_ip() -> str | None:
    """Best-effort local LAN IP for this machine, to show teammates what to
    put in their `remote_peers`. Doesn't actually send any traffic -- a UDP
    socket's `connect()` only performs local route lookup."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return None


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


def render_cyclonedds_xml(peers: list[str], remote_peers: list[str] | None = None) -> str:
    all_peers = [*peers, *(remote_peers or [])]
    peer_elements = "\n".join(
        f'        <Peer address={quoteattr(name)} />' for name in all_peers
    )
    peers_block = (
        f"      <Peers>\n{peer_elements}\n      </Peers>" if all_peers else "      <Peers />"
    )
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


def refresh_peers(
    client: docker.DockerClient, group: str, remote_peers: list[str] | None = None
) -> Path:
    """Regenerate the shared CycloneDDS peers file for a network group
    (bridge mode only -- see `refresh_peers_host_mode` for host mode).

    Returns the host path that's bind-mounted into every container in the
    group; callers don't need to touch any container to make the update
    take effect (see module docstring).
    """
    peers = peer_container_names(client, group)
    host_path = cyclonedds_host_path(group)
    host_path.write_text(render_cyclonedds_xml(peers, remote_peers))
    return host_path


# Not a real `network:` group name -- just a fixed key so every host-mode
# container across every workspace on this machine shares the one peers
# file (there's no separate bridge network per group to key off in host
# mode; they're all on the same real network namespace regardless of
# their `network:` value, which is ignored under host mode).
HOST_NETWORK_GROUP = "__host__"


def refresh_peers_host_mode(remote_peers: list[str] | None = None) -> Path:
    """Regenerate the shared CycloneDDS peers file for host-mode containers.

    No Docker network to introspect for peer container names -- every
    host-mode container already shares this machine's real loopback, so
    `127.0.0.1` alone covers same-host discovery.
    """
    host_path = cyclonedds_host_path(HOST_NETWORK_GROUP)
    host_path.write_text(render_cyclonedds_xml(["127.0.0.1"], remote_peers))
    return host_path
