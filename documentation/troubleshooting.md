# Troubleshooting

Start here:

```sh
rosman doctor
```

It checks Docker reachability, config validity, container drift, declared
GPU/devices, and (with `--network-check`) actually spins up two ephemeral
containers to verify a pub/sub round trip over the network — the most
reliable way to confirm networking actually works end to end, not just
that the config looks right.

## Common issues

**A `docker.errors.DockerException` / "docker daemon" check fails.**
Docker itself isn't reachable — make sure Docker Engine (Linux) or Docker
Desktop (Windows) is running, and that your user has permission to talk to
it (on Linux, being in the `docker` group; a fresh group membership needs
a new login session to take effect, not just a new terminal).

**A build hangs for a long time with no error, using a custom
`base_image`.** A bare (non-ROS) base image doesn't set
`DEBIAN_FRONTEND=noninteractive` on its own, and an interactive `tzdata`
(or similar) prompt during `apt-get install` will hang the build
indefinitely rather than fail — there's nothing to answer the prompt in a
non-interactive build. See [Custom base images](guides/custom-images.md).

**`rosman doctor` reports config drift.** Something in `rosman.yml`
(distro, network, `base_image`/`setup_script` content, restart policy, or
the resolved domain id) changed since the container was created. Run
`rosman rebuild` to recreate it with the new config — your `build`/
`install`/`log` volumes are preserved.

**Files created in the container come out owned by the wrong user on the
host.** This shouldn't happen under normal use — rosman applies your
actual host UID/GID at container start specifically so bind-mounted files
come out correctly owned. If you're hitting this, check for a stale
container from before a `rosman rebuild`/`rosman down --remove`, or open
an issue.

**A `colcon build` fails only for the very first build after
`registry_image`/`base_image` changed.** Try `rosman rebuild` — the
`build`/`install`/`log` directories are named Docker volumes tied to the
distro, and a fresh one starts out root-owned until the first UID-matched
process touches it; this is handled automatically on a normal `rosman up`,
but a manual `docker` interaction with the same volume outside of rosman
can leave it in a state rosman doesn't expect.

**`colcon build` succeeds for a package cloned into `src/`, but running it
fails with a `ModuleNotFoundError` for another ROS package.** The package
declares a dependency that isn't part of the default image — `colcon
build` doesn't fail just because a pure-Python runtime import will later
be missing. Run `rosman rosdep install` to resolve and install it (and
lock it into `rosman.yml`'s built image via `rosman rebuild`) — see
[Building from source](guides/rosdep-lockfile.md). Run this on a
container you haven't manually poked at with `sudo apt install` by hand
first (or `rosman rebuild` beforehand) — `rosdep install` checks what's
*actually* installed in the current container, so an earlier manual
install can make it correctly (if unhelpfully, for locking purposes)
report "nothing to do" even though the package was never baked into the
image.

**`colcon build` succeeds for a package cloned into `src/`, but running it
fails with `AttributeError: __enter__` (or another API-shaped error) deep
inside the package's own code.** You likely cloned the wrong branch — ROS
2 source repos maintain distro-specific branches (`humble`, `jazzy`, ...)
because the API surface genuinely changes between distros, and the
default branch usually tracks `rolling` (the newest, unstable API). Check
which branch you're on, and re-clone with `git clone -b <your_distro>
...` if it doesn't match your `rosman.yml`'s `ros_distro`.

**`ros2 topic list`/GUI apps don't show anything from another rosman
project.** Confirm both projects use the same `network:` group in their
`rosman.yml` — only projects in the same group can discover each other
(bridge mode only; under host networking, `network:` is ignored and
same-machine discovery always works via `localhost`). Run `rosman doctor
--network-check` to verify the underlying DDS discovery mechanism itself
works (it spins up two throwaway containers and confirms one actually
receives what the other publishes).

**A TCP service inside the container (a `ros_tcp_endpoint`-style bridge
for Unity, a web dashboard, ...) isn't reachable from outside.** Two
separate things to check: (1) it needs to bind `0.0.0.0`, not
`127.0.0.1`, inside the container — `127.0.0.1` only ever means "this
container's own loopback," never the host's, regardless of network mode.
(2) Under bridge networking (Windows by default), the port also needs to
be published via `ports:` in `rosman.yml` — see
[Networking](guides/networking.md). Under host networking (the Linux
default), (1) alone is enough; there's no publishing step.

**A GUI window (rviz2, rqt, ...) doesn't appear.** Make sure the package
providing it is actually installed (`extra_apt_packages`) — rosman wires
up the X11/WSLg plumbing but doesn't install GUI packages itself. See
[GPU, GUIs & devices](guides/gpu-gui-devices.md).

**A device in `devices:` isn't visible inside the container, on
Windows.** WSL2 doesn't expose host USB devices by default. `rosman
doctor` detects this and prints the exact `usbipd bind`/`usbipd attach`
commands (with the device's BUSID from a live `usbipd list`) to fix it.

**Multi-host discovery isn't working.** Almost always either mismatched
`domain_id` (must be identical, explicit, on every machine — see
[Multi-host](guides/multi-host.md)) or a firewall blocking the UDP port
range `rosman doctor` reports. Confirm same-host discovery works first
with `rosman doctor --network-check` before troubleshooting the
cross-machine case.

**Nothing happens for a while on the very first command in a new
workspace, with no output.** This was a real bug, since fixed: the
"Starting rosman container..." message now prints *before* the (possibly
slow) first build, not after, and build/pull/push all show live progress.
If you're seeing this on a current version, please open an issue — it
means something regressed.

## Still stuck?

Open an issue at
[github.com/alec-jensen/rosman/issues](https://github.com/alec-jensen/rosman/issues)
with your `rosman.yml` and the output of `rosman doctor`.
