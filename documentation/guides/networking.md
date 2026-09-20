# Networking: `network_mode` & `ports`

```yaml
network_mode: auto   # "auto" (default), "host", or "bridge"
ports: []             # bridge mode only, e.g. ["10000:10000"]
```

## The default

`auto` resolves to:

- **Host networking on Linux** (including WSL2 — it's a real Linux
  kernel, so host networking there is exactly as reliable as native
  Linux, no VM boundary involved).
- **Bridge networking on Windows** — Docker Desktop's host networking
  support there still has real, documented reliability issues as of
  2026 (containers not reachable from WSL2/Windows in some
  configurations, conflicts between WSL2's mirrored networking mode and
  Docker Desktop's port proxying). This isn't a permanent judgment; it's
  worth rechecking if you're reading this well after 2026.

Override either direction with an explicit `network_mode: host` or
`network_mode: bridge`, regardless of platform.

## Host networking

Every container port already *is* the host's port — nothing to publish,
nothing to configure. A TCP service running inside the container (a
`ros_tcp_endpoint`-style bridge for Unity, `rosbridge_server`, a web
dashboard, ...) is reachable at `localhost:<port>` from the host directly,
as long as it binds `0.0.0.0` rather than `127.0.0.1` inside the
container — `127.0.0.1` inside any container (host-networked or not) only
ever means "this container's own loopback," never the host's.

```sh
rosman run ros_tcp_endpoint default_server_endpoint \
  --ros-args -p ROS_IP:=0.0.0.0 -p ROS_TCP_PORT:=10000
```

Unity (or anything else on the same machine, or the same LAN once the OS
firewall allows it) connects to `<this-machine's-address>:10000` directly.

**The one tradeoff**, inherent to host networking generally and not
something rosman tries to prevent: two *different* rosman workspaces on
the same machine can't both bind the same fixed port at the same time —
exactly like two native processes competing for a port. If you hit this,
either don't run both workspaces' conflicting services simultaneously, or
use `network_mode: bridge` + `ports:` for one of them to remap around the
conflict.

Multi-host DDS discovery (`remote_peers`) also gets simpler under host
networking: there's no port publishing to compute at all, since the port
Cyclone DDS binds is already the host's port. See
[Multi-host](multi-host.md).

## Bridge networking

The original model, still the default on Windows and available anywhere
via `network_mode: bridge`: a rosman-managed Docker bridge network per
`network:` group, with an explicit generated CycloneDDS peers file for
discovery (see [Multi-host](multi-host.md) for the cross-machine case).

For a TCP service that needs to be reachable from outside the container
under bridge mode, declare it with `ports:` — Docker Compose-style:

```yaml
ports:
  - "10000:10000"    # host_port:container_port
  - "8080"            # same port on both sides
```

## `rosman doctor`

Reports which mode a workspace resolved to (and whether that was
auto-detected or explicit), and `--network-check` runs its two-container
pub/sub round trip using that same resolved mode, so the test is actually
representative of what your real workspace uses.
