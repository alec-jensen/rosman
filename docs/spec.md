# rosman — Project Spec

## 1. What this is

`rosman` is a CLI tool that lets a developer run ROS 2 — any distro, on Linux or
Windows — without installing ROS natively. Every ROS 2 call the user makes
(`rosman run ...`, `rosman launch ...`, `rosman topic echo ...`, etc.) is
transparently forwarded into a Docker container running the correct ROS 2
distro for that project, so it feels like native `ros2` usage even though
Docker is doing all the work underneath.

The core idea: a per-repo config file (like `.nvmrc` for Node or
`rust-toolchain.toml` for Rust) declares which ROS 2 distro a project needs.
`rosman` reads it, ensures a container running that distro exists for the
current workspace, and transparently execs every command into it.

**Target platforms: Linux (Docker Engine, native) and Windows (Docker Desktop,
WSL2 backend). macOS is explicitly out of scope — do not spend design or
implementation effort on it.**

## 2. Design decisions already made (do not re-litigate these)

These three decisions were made deliberately and should shape the
architecture from the start:

1. **Networking: bridge network + auto-generated CycloneDDS unicast peer
   config**, not `--network host`. Host networking is inconsistent on Windows
   Docker Desktop (multicast/port forwarding is unreliable even with the
   "Enable host networking" beta setting), so rosman standardizes on Cyclone
   DDS with explicit unicast peer discovery so behavior is identical on Linux
   and Windows.
2. **One persistent container per workspace.** `rosman` does not spin up a
   fresh container per command. It starts (or reuses) a long-running
   container for the workspace the first time it's needed, and every
   subsequent `rosman <command>` is a `docker exec` into that container. This
   is what makes rosman feel fast and native instead of like a wrapper.
3. **Thin passthrough for the ROS 2 CLI.** `rosman` does not reimplement
   `ros2 topic`, `ros2 launch`, etc. It forwards the command verbatim to
   `ros2` inside the container. `rosman`'s own logic is scoped to
   config/version resolution, container lifecycle, and networking — not to
   reimplementing ROS 2's CLI surface.

## 3. Config file

Each ROS 2 project has a `rosman.yml` (or `.rosman.yml`) at its repo root.
Example:

```yaml
ros_distro: humble          # required — any distro with an official ros:<tag> image
rmw_implementation: cyclonedds  # default; can be overridden but cyclonedds is the supported path
domain_id: auto               # "auto" = rosman assigns and persists one per project; or an explicit int
network: default               # named Docker network group — projects sharing a network can discover each other
gpu: false                     # true enables nvidia-container-toolkit passthrough (Linux and WSL2 GPU paravirt both supported)
devices: []                    # e.g. ["/dev/ttyUSB0"] — passed through on Linux; auto-resolved via usbipd-win on Windows
workspace_dir: .               # path (relative to this file) that becomes the container's mounted workspace root
extra_apt_packages: []         # optional list, installed into the image on first build
```

`rosman` should validate this file and give a clear error (not a Python
traceback) if it's missing required fields or has an unsupported
`ros_distro`.

## 4. Architecture overview

Four responsibilities, and they should be separable modules/components, not
one monolithic script:

1. **Config resolver** — finds and parses `rosman.yml` by walking up from
   cwd (like `.git` discovery), resolves the effective config (including
   `domain_id: auto` assignment, described below).
2. **Container lifecycle manager** — creates/starts/stops/tears down the
   per-workspace container; detects when the container's config has drifted
   from `rosman.yml` (e.g. distro changed) and needs to be recreated;
   handles the workspace bind mount and named volumes for build artifacts.
3. **Networking layer** — creates/manages the shared Docker network(s),
   assigns domain IDs, generates and refreshes the CycloneDDS unicast peers
   XML for each workspace container.
4. **Command dispatcher** — the actual `rosman <args>` entrypoint. Decides
   whether `<args>` is a reserved rosman-namespace command or a pass-through
   `ros2` invocation, ensures the container is running, translates the
   current working directory into the container's path, and `docker exec`s.

## 5. Networking design (the highest-risk part — prototype this first)

- All rosman-managed containers belonging to the same `network:` group (from
  `rosman.yml`) are attached to one **rosman-created Docker bridge network**
  named something like `rosman-net-<group>`. Containers get resolvable names
  on this network via Docker's built-in embedded DNS, so peer IPs don't need
  to be hardcoded.
- On container start (and whenever the peer set on a network changes),
  rosman regenerates a **CycloneDDS XML config** (`CYCLONEDDS_URI` pointed at
  a generated file) listing the other containers on the same network as
  unicast peers, and mounts/updates it inside each affected container without
  requiring a container restart if avoidable.
- `RMW_IMPLEMENTATION=rmw_cyclonedds_cpp` is set by default in every
  container rosman manages.
- **Domain ID assignment**: when `domain_id: auto`, rosman assigns an integer
  (avoiding collisions with other rosman-managed projects on the same host)
  the first time the project is set up, and persists that assignment
  (e.g. in a local rosman state file, not in `rosman.yml` itself, since that
  file is likely checked into version control and shared across machines).
- **Validation step**: build a `rosman doctor` (mirrors `ros2 doctor`) check
  that spins up two rosman containers on the same network and confirms
  `ros2 topic list` / a talker-listener round trip actually works before
  calling networking "done." Do this early — it's the part most likely to
  reveal a design flaw.
- Document the fallback path: if a user's Windows Docker Desktop host
  networking is broken/disabled, unicast-peer CycloneDDS should keep working
  regardless, since it doesn't depend on host networking or multicast at
  all — that's the whole point of this choice.

## 6. Container lifecycle

- **Startup trigger**: `rosman <anything>` should auto-start the workspace
  container if it isn't running, printing a clear one-line status
  (`Starting rosman container for this workspace (humble)...`) so first-run
  latency doesn't look like a hang.
- **Explicit lifecycle commands** (reserved rosman namespace — see §8):
  `rosman up`, `rosman down`, `rosman status`, `rosman rebuild`.
- **Config drift detection**: before exec-ing into an existing container,
  compare the running container's labels (distro, image hash, mounted
  config) against the current `rosman.yml`. If they differ, warn and offer
  (or require, TBD — pick one and document it) to recreate the container.
- **Restart policy**: containers should be started with
  `--restart unless-stopped` is *not* recommended by default, since WSL2/
  Docker Desktop sessions on Windows can restart independently of the user's
  intent; default to requiring an explicit `rosman up` after a host reboot,
  and make this configurable.
- **UID/permission matching**: on container creation, create a user inside
  the container matching the host UID/GID (same approach as `osrf/rocker`)
  so files created in the mounted workspace aren't root-owned on the host.
- **Build artifact caching**: bind-mount only the source directories from
  `workspace_dir`. Keep `build/`, `install/`, and `log/` in **named Docker
  volumes keyed by workspace + ROS distro**, not in the bind mount, so
  switching distros doesn't force stale artifacts and doesn't take a
  performance hit from bind-mount I/O on the build output.

## 7. Command dispatch / CLI shim

- `rosman <args>` where `<args>` doesn't match a reserved rosman command is
  forwarded as `docker exec -it <container> ros2 <args>` (or `colcon
  <args>` — decide whether `colcon` gets the same passthrough treatment;
  recommended: yes, same mechanism, since build commands need the same
  container/workspace resolution).
- **Working directory translation**: `rosman` must map the user's current
  host working directory to the equivalent path inside the container (same
  relative path under the mounted workspace root) and pass that as the exec
  working directory — otherwise relative paths in launch files and package
  references will break.
- **Reserved namespace**: rosman's own subcommands must not collide with
  real `ros2`/`colcon` subcommands. Recommend prefixing/grouping rosman-only
  commands clearly, e.g.:
  - `rosman init` — scaffold a `rosman.yml` in the current repo
  - `rosman up` / `rosman down` — start/stop the workspace container
  - `rosman status` — show running containers, their distro, network group,
    domain ID
  - `rosman rebuild` — force-recreate the container from current config
  - `rosman doctor` — environment + networking sanity checks (see §5)
  - `rosman shell` — drop into an interactive shell in the container (for
    debugging, distinct from a passthrough `ros2`/`colcon` call)
  - Everything else not matching these is assumed to be a ROS 2/colcon
    passthrough call.
- Shell tab-completion for the passthrough case is explicitly **not** MVP
  scope — native `ros2`'s argcomplete-based completion is not required to
  work through rosman initially. Note this as a known limitation rather than
  silently losing it.

## 8. Platform-specific handling

### Linux
- GPU: `nvidia-container-toolkit`, standard `--gpus all` passthrough when
  `gpu: true`.
- GUI (rviz2/rqt/Gazebo): bind-mount `/tmp/.X11-unix`, pass `$DISPLAY`, use
  an XAuth cookie file mounted into the container (same mechanism as
  `osrf/rocker`'s X11 extension — reuse that approach directly rather than
  reinventing it).
- Devices: pass through `devices:` list entries directly via Docker's
  `--device` flag.

### Windows (Docker Desktop, WSL2 backend)
- GPU: WSL2 GPU paravirtualization + nvidia-container-toolkit inside WSL2;
  should work the same way as Linux from Docker's perspective once set up.
- GUI: **WSLg** provides X11/Wayland forwarding automatically inside WSL2 —
  no manual `xhost`/XAuth handling needed. rosman should detect it's running
  under WSL2 and skip the Linux X11 mounting logic, only ensuring `$DISPLAY`
  isn't overwritten if WSLg has already set it correctly.
- Devices (USB/serial): WSL2 does not expose host USB devices by default.
  The supported path is **usbipd-win**
  (`winget install usbipd`, `usbipd bind`/`usbipd attach`) to share a
  Windows-side USB device into the WSL2 instance, after which it appears as
  a normal `/dev/ttyUSBx`-style node that Docker can pass through normally.
  `rosman doctor` should detect devices listed in `rosman.yml` that aren't
  currently visible and print usbipd-specific setup instructions on
  Windows.

## 9. Non-goals for MVP (explicitly out of scope, revisit later)

- macOS support.
- Reimplementing/wrapping individual `ros2` subcommands beyond passthrough.
- Shell tab-completion through the container.
- Multi-host (robot + remote workstation over the internet) DDS discovery —
  MVP targets a single Windows/Linux dev machine with multiple rosman
  containers, not distributed robots. Note this as a clearly separate,
  later problem (would need something like a VPN mesh, e.g. Husarnet,
  layered on top).
- ROS 1 support — ROS 2 only for MVP.

## 10. Suggested implementation approach

- Language: Python is a reasonable default (good Docker SDK support via
  `docker-py`, easy distribution via pip/pipx, matches the ROS ecosystem's
  own tooling conventions like `colcon` and `rosdep`). Note this as a
  recommendation, not a hard requirement — Go is a reasonable alternative if
  a single static binary (especially for the Windows-side CLI) is preferred
  and worth discussing before committing.
- Use the Docker SDK/API directly rather than shelling out to the `docker`
  CLI where practical, for cleaner error handling around container
  state checks.
- Suggested build order (each phase should produce something testable):
  1. Config resolver + `rosman init`/`rosman up` bringing up a single
     container for one workspace, `rosman <ros2 command>` passthrough
     working for a single-container, no-networking case.
  2. Container lifecycle robustness: drift detection, `rosman status`,
     `rosman rebuild`, UID matching, build-artifact volume caching.
  3. Networking: shared Docker network per `network:` group, CycloneDDS
     unicast peer generation, domain ID auto-assignment, `rosman doctor`
     networking check with an actual two-container talker/listener test.
  4. Platform-specific extensions: Linux X11/GPU/device passthrough,
     Windows WSLg detection + usbipd-win device flow + doctor guidance.
  5. Polish: working-directory translation edge cases, error messaging,
     docs.

## 11. Open questions to resolve during implementation (not blocking start)

- Should config drift (distro changed in `rosman.yml`) auto-recreate the
  container, or require explicit `rosman rebuild` confirmation? Leaning
  toward requiring confirmation, since silently recreating could lose
  in-container state a user forgot was there.
- Should `colcon` commands go through the same passthrough path as `ros2`
  commands, or get slightly different handling (e.g. always run in the
  workspace root regardless of cwd)? Leaning toward "same path," but worth
  testing against a real multi-package workspace before locking in.
- Exact domain ID collision-avoidance strategy when `domain_id: auto` is
  used across many unrelated rosman projects on one machine — simplest
  approach is a local state file tracking assigned IDs per project path;
  confirm this is sufficient before building anything fancier.

---

## Addendum (2026-09-19): image customization for heavier per-project needs

Everything above is the original spec, kept verbatim as the historical
record. This addendum captures a real gap surfaced once the project's own
use case came up: some projects need much more than `extra_apt_packages`
can express — GPU-heavy stacks (CUDA pinned to a specific base image) and
vendor SDKs with their own installers (e.g. the ZED SDK), not just plain
`apt install <name>` packages.

Two config fields close this gap, both optional (most projects need
neither):

- **`base_image`** — override the default `ros:<distro>` image. When set,
  rosman adds the official ROS 2 apt repo and apt-installs
  `ros-<distro>-ros-base` onto it, the same way it would if you followed
  ROS's own "install on a bare Ubuntu box" instructions. This is for cases
  like starting from `nvidia/cuda:<tag>-devel-ubuntu22.04` so CUDA/cuDNN
  versions are exactly what the base image pins, rather than whatever ends
  up layered on top of `ros:<distro>` after the fact. Doing this correctly
  requires knowing which Ubuntu codename each ROS 2 distro's apt packages
  are published under (humble→jammy, jazzy→noble, etc.) — a small, mostly
  fixed mapping, except `rolling`, which tracks whatever codename is
  currently current and may need updating over time.
- **`setup_script`** — a path (relative to `rosman.yml`, must resolve
  inside the project) to a shell script rosman copies into the image build
  context and runs, as the rosman user, after its own base setup. This is
  the general escape hatch: adding a vendor apt repo, running a `.run`
  installer, `pip install`, anything `extra_apt_packages`'s flat
  package-name list can't express. The script's *contents* (not just its
  path) feed into the same config-hash that already drives image tagging
  and drift detection, so editing it triggers a rebuild on the next
  `rosman up`, the same as changing `ros_distro` would.

Neither field changes the three load-bearing decisions in §2 — one
persistent container, bridge network + CycloneDDS, thin ros2/colcon
passthrough — they only extend *how the image gets built*, which was
already the part of the config schema explicitly designed to be extended
(`extra_apt_packages` was the same kind of knob, just narrower).

---

## Addendum (2026-09-19, part 2): team-shared images via a registry

A follow-up gap, surfaced once the base_image/setup_script work above was
live-tested: a team wants everyone to run the *same* image without every
member independently rebuilding it. Two designs were considered and one
was explicitly rejected:

- **Rejected: "bring your own pre-built image."** Letting a project point
  rosman at an already-built image and skip rosman's own build entirely
  was considered and explicitly ruled out. Alec's words: "I don't think we
  actually want to support bring your own image. I think that's exactly
  what this project wants to eliminate, is needing to manage your own
  image." rosman always builds the image itself; nothing changes about
  that.
- **Adopted: `registry_image`.** rosman still builds the image exactly as
  before (`ros:<distro>` or `base_image` + `setup_script`); this field only
  says where to cache the *result* for a team. `rosman push` builds (if
  needed) and pushes to that repository, tagged with the same content-hash
  already used for local drift detection. `rosman up` tries `docker pull`
  against that tag before building locally, falling back to a local build
  only if nothing's been pushed yet or the registry isn't reachable.

Making this actually work correctly for a team required fixing a real,
previously-latent bug: the image used to bake in the **builder's own host
UID/GID** (for the file-permission-matching feature), which meant two
teammates with different host UIDs building the identical `rosman.yml`
got *different* image tags — there would have been no shared tag to push
or pull in the first place. Fixed by decoupling the two:

- The image now bakes in a fixed, arbitrary identity (uid/gid 1000, a
  `rosman` user) regardless of who builds it — this is what makes the
  image itself byte-identical (and its content-hash tag reusable) across
  different builders.
- The actual host UID/GID is still applied purely at container *runtime*
  (`docker run --user`, unchanged from before) — this is still what makes
  bind-mounted workspace files come out correctly owned on the host,
  regardless of who built the image.
- Since an arbitrary runtime UID won't have a `/etc/passwd` entry for the
  image's fixed baked-in user, an `ENTRYPOINT` script patches one in on
  every container start (the standard "arbitrary UID" container pattern
  used by e.g. OpenShift-compatible images). Without this, anything that
  calls `getpwuid` — `bash`'s own prompt, `git`, some `colcon`/`rosdep`
  paths, `sudo`'s PAM checks — misbehaves for any teammate whose host UID
  isn't exactly 1000.
- A few paths that used to be `chown`ed to the builder's UID at build time
  (the user's home directory, and the `build`/`install`/`log` named
  volumes) are instead made world-writable, since there's no longer a
  single "correct" UID to chown them to at build time.

This loosens the in-container permission model slightly — any UID can
write to those specific paths, and `sudo` is now UID-agnostic
(`ALL ALL=(ALL) NOPASSWD:ALL` rather than keyed to one username). Judged
acceptable because the threat model here is a personal/team dev container
someone already has `docker exec` access to, not a multi-tenant system —
but it's a deliberate tradeoff, not an oversight, and worth knowing about
if that threat model is ever wrong for a given project.
