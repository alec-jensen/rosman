# Configuration

## `rosman.yml`

Checked into your repo, next to the code it describes. Only `ros_distro`
is required — everything else has a default.

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
registry_image: null          # optional -- share one built image across a team
remote_peers: []              # optional -- LAN IPs of other machines' rosman containers
ports: []                     # optional -- e.g. ["10000:10000"]; bridge mode only
```

| Field | Default | Notes |
|---|---|---|
| `ros_distro` | *(required)* | Any distro with an official `ros:<tag>` Docker image (`humble`, `jazzy`, `rolling`, ...). ROS 1 (`noetic`) is rejected with a clear error. |
| `rmw_implementation` | `cyclonedds` | Only supported value today — rosman's networking model is built around Cyclone DDS's unicast peers. |
| `domain_id` | `"auto"` | `"auto"` assigns and persists one per project on this machine. Must be an explicit integer (0-232) if `remote_peers` is set — see [Multi-host](guides/multi-host.md). |
| `network` | `"default"` | Bridge-mode network group name. Other projects using the same `network:` value can discover each other over DDS. Ignored under host networking. |
| `network_mode` | `"auto"` | `"auto"` resolves to host networking on Linux, bridge on Windows. `"host"`/`"bridge"` force one regardless of platform. See [Networking](guides/networking.md). |
| `gpu` | `false` | Requests `nvidia-container-toolkit` passthrough (`--gpus all`-equivalent). |
| `devices` | `[]` | Host device paths passed through, e.g. `["/dev/ttyUSB0"]`. See [GPU/GUI/devices](guides/gpu-gui-devices.md). |
| `workspace_dir` | `"."` | Path, relative to `rosman.yml`, mounted as the container's workspace root. |
| `extra_apt_packages` | `[]` | Installed into the image on first build — the simple case for adding a package. |
| `restart_policy` | `"no"` | Docker restart policy. Containers do not restart with Docker after a reboot by default; the next `rosman` command starts the workspace container on demand. |
| `base_image` | `null` | Override the default `ros:<distro>` base image — see [Custom base images](guides/custom-images.md). |
| `setup_script` | `null` | A shell script run during the image build, for anything `apt` can't express — see [Custom base images](guides/custom-images.md). |
| `registry_image` | `null` | Share one built image across a team via `rosman push` — see [Team-shared images](guides/team-images.md). |
| `remote_peers` | `[]` | LAN IPs of other machines' rosman containers for this project — see [Multi-host](guides/multi-host.md). |
| `ports` | `[]` | Bridge-mode-only TCP port publishing, Docker Compose-style (`"host:container"` or bare `"port"`). Ignored under host networking, where every container port already is the host's. See [Networking](guides/networking.md). |

## Per-machine overrides: `rosman.local.yml`

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
entirely — a list like `devices` is swapped wholesale, not merged. Fields
not mentioned come from `rosman.yml` as usual. The file is optional.

## Auto-generated: `rosman.lock`

Unlike `rosman.local.yml`, this one **is** meant to be checked into git —
it's produced by [`rosman rosdep install`](guides/rosdep-lockfile.md) and
records the apt *and* pip packages resolved from your workspace `src/`
packages' declared dependencies, so a `colcon build`-from-source workflow
stays reproducible and shared with your team instead of a one-off change
to a single running container. Never hand-edit it; it's regenerated in
full each time you run the command that produces it.
