# rosman

Run ROS 2 — any distro, on Linux or Windows — without installing it
natively. `rosman` transparently forwards every `ros2`/`colcon` call into a
Docker container running the right ROS 2 distro for your project, so it
feels like native `ros2` usage even though Docker does all the work.

A per-repo `rosman.yml` (like `.nvmrc` for Node, or `rust-toolchain.toml`
for Rust) declares which ROS 2 distro a project needs. `rosman` reads it,
ensures a container running that distro exists for the current workspace,
and execs every command into it — including auto-starting that container
the first time you run anything, no separate `rosman up` required.

```sh
cd my-ros2-project
rosman init --distro humble
rosman topic list      # auto-starts the container on first use
rosman colcon build
rosman shell
```

Target platforms: Linux (Docker Engine) and Windows (Docker Desktop). The
core workflow runs natively on Windows without WSL2; WSL2 is only needed
for GUI passthrough (WSLg) and USB device attachment (usbipd-win). macOS
is not supported.

## Where to go next

- **[Getting started](getting-started.md)** — install rosman and run your
  first workspace.
- **[Configuration](configuration.md)** — the full `rosman.yml` reference.
- **Guides** — [networking (network_mode & ports)](guides/networking.md),
  [custom base images](guides/custom-images.md),
  [building from source (rosdep + lock)](guides/rosdep-lockfile.md),
  [team-shared images](guides/team-images.md),
  [multi-host over LAN](guides/multi-host.md),
  [shell tab-completion](guides/tab-completion.md), and
  [GPU/GUI/device passthrough](guides/gpu-gui-devices.md).
- **[Examples](examples.md)** — complete `rosman.yml` files for common
  setups.
- **[Troubleshooting](troubleshooting.md)** — `rosman doctor` and fixes
  for the issues people actually hit.

## Design decisions

- **Host networking on Linux by default, bridge on Windows.** Real
  `--network host` is fully reliable on Linux (including WSL2); Docker
  Desktop's host networking on Windows still has documented reliability
  issues as of 2026, so Windows defaults to a per-workspace bridge network
  with explicit CycloneDDS unicast peer discovery instead. Override either
  direction with `network_mode:` — see [Networking](guides/networking.md).
- **One persistent container per workspace.** The first `rosman <command>`
  starts it; every other `rosman <command>` is a `docker exec` into that
  same container.
- **Thin passthrough for the ROS 2 CLI.** rosman's own logic is scoped to
  config/version resolution, container lifecycle, and networking — it
  doesn't reimplement `ros2`.

See the [project README](https://github.com/alec-jensen/rosman) for the
source, and `docs/spec.md`/`docs/roadmap.md` in the repo for the full
design history if you're curious how any of this was decided.
