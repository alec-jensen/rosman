# Configuration

## `rosman.yml`

Checked into your repo, next to the code it describes. Only `ros_distro`
is required — everything else has a default.

```yaml
ros_distro: humble          # required -- any distro with an official ros:<tag> image
rmw_implementation: cyclonedds  # default; cyclonedds is the only supported path today
domain_id: auto               # "auto" = rosman assigns and persists one per project; or an explicit int
network: default              # named Docker network group -- projects sharing a network can discover each other
gpu: false                    # true enables nvidia-container-toolkit passthrough
devices: []                   # e.g. ["/dev/ttyUSB0"]
workspace_dir: .              # path (relative to this file) mounted as the container's workspace root
extra_apt_packages: []        # optional list, installed into the image on first build
restart_policy: "no"          # docker restart policy: "no" (default), "unless-stopped", "always", "on-failure"
base_image: null              # optional -- override the default `ros:<distro>` base image
setup_script: null            # optional -- path to a shell script rosman runs during the image build
registry_image: null          # optional -- share one built image across a team
remote_peers: []              # optional -- LAN IPs of other machines' rosman containers
```

| Field | Default | Notes |
|---|---|---|
| `ros_distro` | *(required)* | Any distro with an official `ros:<tag>` Docker image (`humble`, `jazzy`, `rolling`, ...). ROS 1 (`noetic`) is rejected with a clear error. |
| `rmw_implementation` | `cyclonedds` | Only supported value today — rosman's networking model is built around Cyclone DDS's unicast peers. |
| `domain_id` | `"auto"` | `"auto"` assigns and persists one per project on this machine. Must be an explicit integer (0-232) if `remote_peers` is set — see [Multi-host](guides/multi-host.md). |
| `network` | `"default"` | Docker network group name. Other projects using the same `network:` value can discover each other over DDS. |
| `gpu` | `false` | Requests `nvidia-container-toolkit` passthrough (`--gpus all`-equivalent). |
| `devices` | `[]` | Host device paths passed through, e.g. `["/dev/ttyUSB0"]`. See [GPU/GUI/devices](guides/gpu-gui-devices.md). |
| `workspace_dir` | `"."` | Path, relative to `rosman.yml`, mounted as the container's workspace root. |
| `extra_apt_packages` | `[]` | Installed into the image on first build — the simple case for adding a package. |
| `restart_policy` | `"no"` | Docker restart policy. Deliberately not `"unless-stopped"` by default — WSL2/Docker Desktop can restart sessions independently of you, and an unexpected already-running container is more confusing than requiring an explicit `rosman up` after a reboot. |
| `base_image` | `null` | Override the default `ros:<distro>` base image — see [Custom base images](guides/custom-images.md). |
| `setup_script` | `null` | A shell script run during the image build, for anything `apt` can't express — see [Custom base images](guides/custom-images.md). |
| `registry_image` | `null` | Share one built image across a team via `rosman push` — see [Team-shared images](guides/team-images.md). |
| `remote_peers` | `[]` | LAN IPs of other machines' rosman containers for this project — see [Multi-host](guides/multi-host.md). |

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
