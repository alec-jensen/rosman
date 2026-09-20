# rosman

Run ROS 2 — any distro, on Linux or Windows — without installing it
natively. `rosman` transparently forwards every `ros2`/`colcon` call into a
Docker container running the right ROS 2 distro for your project, so it
feels like native `ros2` usage even though Docker does all the work.

A per-repo `rosman.yml` (like `.nvmrc` for Node, or `rust-toolchain.toml`
for Rust) declares which ROS 2 distro a project needs. `rosman` reads it,
ensures a container running that distro exists for the current workspace,
and execs every command into it.

Target platforms: Linux (Docker Engine) and Windows (Docker Desktop). The
core workflow runs natively on Windows without WSL2; WSL2 is only needed
for GUI passthrough (WSLg) and USB device attachment (usbipd-win). macOS
is not supported.

Full documentation, guides, and examples:
**[alec-jensen.github.io/rosman](https://alec-jensen.github.io/rosman/)**

## Installation

Prebuilt packages are published to `https://alec-jensen.github.io/rosman/`
and signed with rosman's release GPG key.

**apt (Debian/Ubuntu):**
```sh
curl -fsSL https://alec-jensen.github.io/rosman/rosman.gpg | sudo tee /usr/share/keyrings/rosman-archive-keyring.gpg > /dev/null
echo "deb [signed-by=/usr/share/keyrings/rosman-archive-keyring.gpg] https://alec-jensen.github.io/rosman/apt stable main" | sudo tee /etc/apt/sources.list.d/rosman.list
sudo apt update && sudo apt install rosman
```

**dnf/yum (Fedora/RHEL):**
```sh
sudo tee /etc/yum.repos.d/rosman.repo > /dev/null << 'EOF'
[rosman]
name=rosman
baseurl=https://alec-jensen.github.io/rosman/dnf
enabled=1
gpgcheck=1
repo_gpgcheck=1
gpgkey=https://alec-jensen.github.io/rosman/rosman.gpg.asc
EOF
sudo dnf install rosman
```

**pacman (Arch Linux):**
```sh
sudo pacman-key --add <(curl -fsSL https://alec-jensen.github.io/rosman/rosman.gpg.asc)
sudo pacman-key --lsign-key <fingerprint printed above>
echo -e "\n[rosman]\nSigLevel = Required\nServer = https://alec-jensen.github.io/rosman/pacman" | sudo tee -a /etc/pacman.conf
sudo pacman -Sy rosman
```

`apt upgrade`/`dnf upgrade`/`pacman -Syu` pick up new releases
automatically. Windows package manager support isn't available yet — on
Windows, or not on apt/dnf/pacman, install from source below, or grab a
raw binary/wheel from a [GitHub release](https://github.com/alec-jensen/rosman/releases).

`rosman` also checks for updates in the background (at most once a day)
and prints a one-line notice when one's available — it never blocks,
fails, or interrupts scripted/CI usage.

### From source

```sh
uv sync --dev
uv run rosman --help
```

## Quick start

```sh
cd my-ros2-project
rosman init --distro humble    # writes rosman.yml
rosman up                      # builds the image and starts the workspace container
rosman topic list               # forwarded to `ros2 topic list` inside the container
rosman colcon build              # forwarded to `colcon build` inside the container
rosman rosdep install            # resolve + install src/ packages' apt deps, and lock them
rosman shell                    # interactive shell in the container
rosman status                   # list rosman-managed containers
rosman doctor                   # environment/config sanity checks
rosman doctor --network-check   # + a two-container pub/sub round trip over the network group
rosman push                     # build (if needed) and push the image to registry_image, for your team
rosman down                     # stop the container (rosman up starts it again)
```

Anything that isn't one of rosman's own subcommands (`init`, `up`, `down`,
`status`, `rebuild`, `doctor`, `shell`, `push`, `completion`, `help`) is
forwarded verbatim as `ros2 <args>` (or `colcon <args>`/`rosdep <args>` if
the first word is `colcon`/`rosdep`) inside the workspace container —
rosman does not reimplement the `ros2` CLI. `rosdep install` specifically
is the one exception: see [Building packages from
source](#building-packages-from-source-rosdep--rosmanlock) below.

## Config: `rosman.yml`

```yaml
ros_distro: humble          # required -- any distro with an official ros:<tag> image
rmw_implementation: cyclonedds  # default; cyclonedds is the only supported path today
domain_id: auto               # "auto" = rosman assigns and persists one per project; or an explicit int
network: default              # bridge-mode network group name -- ignored under host networking
network_mode: auto            # "auto" (host on Linux, bridge on Windows), "host", or "bridge"
gpu: false                    # true enables nvidia-container-toolkit passthrough
devices: []                   # e.g. ["/dev/ttyUSB0"]
workspace_dir: .              # path (relative to this file) mounted as the container's workspace root
extra_apt_packages: []        # optional list, installed into the image on first build
restart_policy: "no"          # docker restart policy: "no" (default), "unless-stopped", "always", "on-failure"
base_image: null              # optional -- override the default `ros:<distro>` base image
setup_script: null            # optional -- path to a shell script rosman runs during the image build
registry_image: null          # optional -- share one built image across a team; see below
remote_peers: []              # optional -- LAN IPs of other machines' rosman containers; see below
ports: []                     # optional -- e.g. ["10000:10000"]; bridge mode only, see below
```

### Per-machine overrides: `rosman.local.yml`

Some fields are inherently machine-specific — a USB serial adapter or
camera rarely lands at the same `/dev` path (or COM port) on every
teammate's machine. Put those in a `rosman.local.yml` next to
`rosman.yml`; `rosman init` adds it to an existing `.gitignore`
automatically.

```yaml
# rosman.local.yml -- not checked in
devices: ["/dev/ttyUSB3"]
```

Any field set here replaces the corresponding value from `rosman.yml`
entirely (a list like `devices` is swapped wholesale, not merged). Fields
not mentioned come from `rosman.yml` as usual. The file is optional.

### Custom base images and setup scripts

For projects with heavier requirements — CUDA, a vendor SDK, anything
beyond a plain `apt install`:

```yaml
ros_distro: humble
base_image: nvidia/cuda:12.4.1-devel-ubuntu22.04   # rosman installs ROS 2 onto this itself
gpu: true                                            # nvidia-container-toolkit passthrough
devices: ["/dev/video0"]                             # the camera
setup_script: docker/install_zed_sdk.sh              # anything apt can't express: repos, .run installers, etc.
```

- `base_image`: overrides the default `ros:<distro>` image. rosman adds
  the official ROS 2 apt repo and installs `ros-<distro>-ros-base` onto
  it — useful when you need a base other than the stock ROS image, e.g.
  `nvidia/cuda` for exact GPU library versions.
- `setup_script`: a path (relative to `rosman.yml`, must stay inside the
  project) to a shell script rosman copies into the build context and
  runs as the rosman user. Editing the script's contents is picked up as
  config drift and triggers a rebuild.

### Building packages from source: `rosdep` + `rosman.lock`

Cloning real ROS 2 source packages into your workspace's `src/` often
pulls in apt dependencies that aren't part of the default image —
`colcon build` can succeed while the package still fails at runtime with a
`ModuleNotFoundError` for a message/service package it depends on.

```sh
rosman rosdep install
```

resolves those dependencies from your `src/` packages' `package.xml`
files, installs them into the running container immediately, and writes
the result to `rosman.lock`:

```yaml
# rosman.lock -- generated, check it into git, don't hand-edit
ros_distro: humble
apt_packages:
  - ros-humble-example-interfaces
```

`rosman.lock` is folded into the image build the same way
`extra_apt_packages` is, so it survives `rosman rebuild` and is shared
with your team via git — run `rosman rebuild` after `rosman rosdep
install` to bake it in. Any other `rosdep` subcommand (`update`, `check`,
...) is plain passthrough like `colcon`.

### Team-shared images

rosman still builds the image itself — `registry_image` just controls
where the *result* is cached, so a team shares one build instead of
everyone rebuilding locally:

```yaml
registry_image: ghcr.io/my-team/my-project   # no tag -- rosman appends its own
```

```sh
rosman push   # builds (if needed) and pushes -- once, after `docker login ghcr.io`
rosman up     # teammates: pulls the pushed image instead of building locally
```

`rosman up` tries a pull first whenever `registry_image` is set, falling
back to a local build if nothing's been pushed yet or the registry isn't
reachable. Registry auth is your own `docker login`. The image bakes in a
fixed internal identity, not whoever built it — each machine's actual
host UID/GID is applied at container runtime, so bind-mounted files still
come out correctly owned regardless of who built the shared image.

### Networking: `network_mode` & `ports`

```yaml
network_mode: auto   # "auto" (default), "host", or "bridge"
ports: []             # bridge mode only, e.g. ["10000:10000"]
```

`auto` resolves to **host networking on Linux** (including WSL2 — real,
fully reliable Linux host networking, no VM boundary involved) and
**bridge networking on Windows** (Docker Desktop's host networking there
still has documented reliability issues as of 2026). Override either
direction with an explicit `host`/`bridge` value.

Under host networking, every container port already *is* the host's port
— nothing to publish, nothing to configure, and a TCP service inside the
container (a rosbridge/`ros_tcp_endpoint`-style bridge for Unity/web
clients, for example) is reachable at `localhost:<port>` from the host
with zero extra config, as long as it binds `0.0.0.0` rather than
`127.0.0.1` inside the container. The one tradeoff, inherent to host
networking generally: two *different* rosman workspaces on the same
machine can't both bind the same fixed port at once, exactly like two
native processes competing for a port.

Under bridge networking (Windows by default, or anywhere via
`network_mode: bridge`), use `ports:` for the same TCP-service case —
Docker Compose-style, `["host_port:container_port"]` or a bare
`["port"]` for the same port on both sides.

### Multi-host (LAN)

For talking to a real robot on the same network — no VPN, LAN only:

```yaml
domain_id: 5                         # required -- must be an explicit int, not "auto"
remote_peers: ["192.168.1.51"]       # LAN IP(s) of the other machine(s)
```

Run `rosman doctor` on each machine to see the address to put in the
others' `remote_peers`, and (bridge mode only) the UDP port range that
needs to be reachable between them (rosman publishes it automatically; a
firewall in between is the usual reason it doesn't work). Under host
networking, there's nothing to publish — the port is already the host's.
`domain_id` must be an explicit, matching integer on every machine —
`"auto"` is assigned independently per machine and won't line up across
hosts, so rosman rejects it outright when `remote_peers` is set rather
than failing silently at discovery time.

## Shell tab-completion

```sh
echo 'eval "$(rosman completion bash)"' >> ~/.bashrc   # or: completion zsh >> ~/.zshrc
```

Completes `ros2`/`colcon` subcommands, flags, and dynamic values (topic
names, node names, etc.) exactly as they would inside the container —
`ros2`/`colcon` are `argcomplete`-instrumented already, so rosman just
relays the completion request through `docker exec` rather than
reimplementing any of it. Only works while the workspace container is
already running (`rosman up`); pressing Tab never starts one.

## Design decisions

- **Host networking on Linux by default, bridge on Windows.** Real
  `--network host` is fully reliable on Linux (including WSL2) and needs
  no port publishing at all; Docker Desktop's host networking on Windows
  still has documented reliability issues as of 2026, so Windows defaults
  to a per-workspace bridge network with explicit CycloneDDS unicast peers
  instead. Override either direction with `network_mode:`. See
  [`src/rosman/networking.py`](src/rosman/networking.py) and
  [Networking](#networking-network_mode--ports) below.
- **One persistent container per workspace.** `rosman up` starts it once;
  every other `rosman <command>` is a `docker exec` into that same
  container.
- **Thin passthrough for the ROS 2 CLI.** rosman's own logic is scoped to
  config/version resolution, container lifecycle, and networking — it
  doesn't reimplement `ros2`.

See [`docs/spec.md`](docs/spec.md) for the full design spec, and
[`docs/roadmap.md`](docs/roadmap.md) for build/verification status.

## Known limitations

- macOS is not supported and won't be.
- Tab-completion only works while the container is already running, and
  only completes at the end of the line (no mid-line editing).
- Multi-host discovery is LAN-only (`remote_peers`); there's no VPN mesh
  integration for reaching a machine that isn't on the same network.
- Under host networking (the Linux default), two different rosman
  workspaces on the same machine can't both bind the same fixed TCP port
  at once — the same tradeoff two native processes on that machine would
  have. Not something rosman tries to prevent.
- ROS 2 only, no ROS 1.
- rosman wires up GPU/X11 *plumbing* but doesn't install GUI packages
  (rviz2, rqt, Gazebo) — add them via `extra_apt_packages`.

## Development

```sh
uv sync --dev
uv run pytest
uv run ruff check .
uv run mypy src
```
