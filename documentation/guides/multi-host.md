# Multi-host (LAN)

For talking to a real robot on the same network as your laptop — no VPN,
LAN only.

```yaml
ros_distro: humble
domain_id: 5                         # required -- must be an explicit int, not "auto"
remote_peers: ["192.168.1.51"]       # LAN IP(s) of the other machine(s)
```

Set this identically (same `domain_id`, and each machine's own IP in the
*other* machines' `remote_peers`) on every machine involved, and run
`rosman up` on each.

## Finding the right address and ports

```sh
rosman doctor
```

prints this machine's detected LAN address and the exact UDP port range
that needs to be reachable between the machines — put the address in your
teammate's (or the robot's) `remote_peers`, and check nothing in between
(a firewall, usually) is blocking that port range.

## Why `domain_id` must be explicit

`domain_id: auto` is assigned independently *per machine*, keyed off that
machine's own absolute workspace path. Two machines running the identical
checked-in `rosman.yml` could silently land on different DDS domains and
simply never discover each other — a failure mode that's painful to debug
because nothing errors, things just don't show up. rosman rejects
`remote_peers` + `domain_id: auto` as a config error up front instead, so
this surfaces immediately rather than as an hour of "why can't my laptop
see the robot's topics."

## How it works

This extends the same mechanism rosman already uses for same-host
multi-container discovery (a generated CycloneDDS peers file), rather than
adding a separate transport: `remote_peers`' addresses are added to that
file alongside the local peer entries.

Under **bridge networking** (see [Networking](networking.md) — the
default on Windows), rosman also publishes the exact Cyclone DDS port
window your `domain_id` maps to (deterministically, the same way on every
machine — no coordination needed) so it survives Docker's port publishing
intact. Under **host networking** (the Linux default), there's nothing to
publish at all — the port Cyclone DDS binds is already the host's port,
reachable from the LAN directly once the OS firewall allows it. Either
way, `rosman doctor` reports the same port range, since it comes from
Cyclone DDS's own port-selection formula, independent of which mode
you're using.

## Limitations

- LAN only — there's no VPN mesh integration for reaching a machine that
  isn't on the same network.
- `remote_peers` is a manually maintained list, not automatic discovery
  (no mDNS/broadcast) — matches how rosman already treats local peers as
  an explicit, generated list.
- Reachability isn't verified for you beyond what `rosman doctor` reports;
  a firewall between the two machines is the most common reason two
  correctly configured machines still can't see each other.
