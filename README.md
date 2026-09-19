# rosman

Run ROS 2 — any distro, on Linux or Windows — without installing it
natively. `rosman` transparently forwards every `ros2`/`colcon` call you make
into a Docker container running the right ROS 2 distro for your project, so
it feels like native `ros2` usage even though Docker is doing all the work
underneath.

A per-repo `rosman.yml` (like `.nvmrc` for Node, or `rust-toolchain.toml` for
Rust) declares which ROS 2 distro a project needs. `rosman` reads it, ensures
a container running that distro exists for the current workspace, and execs
every command into it.

**Target platforms: Linux (Docker Engine) and Windows (Docker Desktop,
WSL2 backend). macOS is out of scope.**

## Status

Early scaffolding. Phase 1 (config resolver, container lifecycle,
`ros2`/`colcon` passthrough for a single container) is implemented; the full
multi-container networking validation (`rosman doctor`'s talker/listener
round trip) and the Linux/Windows platform extensions (X11, GPU, USB device
passthrough) are in progress. See [`docs/roadmap.md`](docs/roadmap.md).

## Install (development)

```sh
uv sync --dev
uv run rosman --help
```

## Quick start

```sh
cd my-ros2-project
rosman init --distro humble   # writes rosman.yml
rosman up                     # builds the image and starts the workspace container
rosman topic list              # forwarded to `ros2 topic list` inside the container
rosman colcon build             # forwarded to `colcon build` inside the container
rosman shell                   # interactive shell in the container
rosman status                  # list rosman-managed containers
rosman doctor                  # environment/config sanity checks
rosman down                    # stop the container (rosman up starts it again)
```

Anything that isn't one of rosman's own subcommands (`init`, `up`, `down`,
`status`, `rebuild`, `doctor`, `shell`, `help`) is forwarded verbatim as
`ros2 <args>` (or `colcon <args>` if the first word is `colcon`) inside the
workspace container — rosman does not reimplement the `ros2` CLI.

## Config: `rosman.yml`

```yaml
ros_distro: humble          # required -- any distro with an official ros:<tag> image
rmw_implementation: cyclonedds  # default; cyclonedds is the only supported path today
domain_id: auto               # "auto" = rosman assigns and persists one per project; or an explicit int
network: default              # named Docker network group -- projects sharing a network can discover each other
gpu: false                    # true enables nvidia-container-toolkit passthrough
devices: []                   # e.g. ["/dev/ttyUSB0"]
workspace_dir: .              # path (relative to this file) mounted as the container's workspace root
extra_apt_packages: []        # optional list, installed into the image on first build
```

## Design decisions

- **Bridge network + CycloneDDS unicast peers, not `--network host`.** Host
  networking is unreliable on Windows Docker Desktop, so rosman standardizes
  on Cyclone DDS with explicit unicast peer discovery. See
  [`src/rosman/networking.py`](src/rosman/networking.py).
- **One persistent container per workspace.** `rosman up` starts it once;
  every other `rosman <command>` is a `docker exec` into that same
  container.
- **Thin passthrough for the ROS 2 CLI.** rosman's own logic is scoped to
  config/version resolution, container lifecycle, and networking.

See [`docs/spec.md`](docs/spec.md) for the full design spec this project is
built from.

## Development

```sh
uv sync --dev
uv run pytest
uv run ruff check .
uv run mypy src
```
