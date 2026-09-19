# Roadmap

Tracking status against the phased build order in [`spec.md`](spec.md) §10.

## Phase 1 — single container, no networking — done
- Config resolver (`rosman/config.py`): finds/validates `rosman.yml`,
  walking up from cwd like `.git` discovery.
- `rosman init` scaffolds a config.
- Container lifecycle manager (`rosman/lifecycle.py`): per-workspace image
  build with a host-UID/GID-matched user baked in (rocker-style), container
  create/start/stop/remove.
- `ros2`/`colcon` passthrough (`rosman/dispatch.py`) with working-directory
  translation into the container's mounted workspace.

## Phase 2 — lifecycle robustness — done
- Config drift detection (`ContainerManager.detect_drift`): compares
  ros_distro, image config hash, network group, and domain id labels on the
  running container against the current `rosman.yml`.
- `rosman status`, `rosman rebuild` (with confirmation prompt, per spec §11
  leaning towards requiring confirmation rather than silent recreation),
  `rosman down --remove`.
- Named volumes for `build/`, `install/`, `log/`, keyed by workspace +
  distro (`rosman/naming.py::volume_name`), separate from the source bind
  mount.

## Phase 3 — networking — done
- Shared bridge network per `network:` group (`rosman/networking.py`).
- CycloneDDS unicast peer XML generation, refreshed on container
  create/remove, delivered via a single bind-mounted file shared by every
  container in the group (see module docstring for why this avoids needing
  a restart to pick up peer changes).
- Domain ID auto-assignment with local collision-avoidance state
  (`rosman/state.py`).
- `rosman doctor --network-check` (`rosman/doctor.py::run_network_roundtrip`):
  spins up two ephemeral containers on the workspace's network group
  (reusing the workspace's own rosman-built image, since only that image
  has `rmw_cyclonedds_cpp` installed), publishes on one, and confirms the
  other receives it within a timeout — the validation step spec §5 flags as
  highest priority. Off by default (opt-in flag) since it's slower and may
  pull/build an image; unit-tested with a mocked Docker client (this dev
  sandbox has Docker installed but no daemon permission, so the real
  end-to-end path is still unverified against a live daemon — that's the
  next thing to confirm on a machine with working Docker access).

## Phase 4 — platform-specific extensions — done (needs live verification)
- Linux: X11/XAuth mounting for GUI tools (rviz2/rqt/Gazebo)
  (`rosman/platform_support.py::gui_passthrough`), generating a
  rocker-style masked XAuth cookie rather than sharing the whole host
  `~/.Xauthority`, wired into `ContainerManager.create_container`.
  Degrades gracefully (skips the cookie, keeps `$DISPLAY`) if `xauth` isn't
  installed on the host, rather than failing `rosman up`.
- Windows: WSL2/WSLg detection (`rosman/platform_support.py::is_wsl2`) so
  rosman skips the Linux X11/XAuth logic and just bind-mounts WSLg's
  existing `/tmp/.X11-unix` and `/mnt/wslg` sockets through untouched.
  `usbipd-win` guidance in `rosman doctor`: shells out to `usbipd list` (or
  `usbipd.exe list` from WSL2) when available and prints the BUSID listing
  alongside the bind/attach commands; falls back to generic install
  instructions if `usbipd` isn't found on PATH.
- Not yet verified on an actual Windows/WSL2 host or against a real X
  server — the pure-Python logic (WSL2 detection, XAuth cookie generation
  falling back cleanly when `xauth` is missing, env/volume construction) is
  unit-tested, but nobody has yet run `rosman up` with `gpu: true` or a
  populated `devices:` list against real hardware.
- One documented limitation: rosman only wires up the X11/GPU *plumbing*.
  It does not install GUI packages (rviz2, rqt, Gazebo) into the per-
  workspace image — that's what `extra_apt_packages` in `rosman.yml` is
  for, since the base `ros:<distro>` image is minimal.

## Phase 6 — image customization for heavier per-project needs — done (needs live verification)
Not in the original spec; added 2026-09-19 once a real use case (GPU +
vendor SDK) surfaced the gap. Full rationale in `spec.md`'s addendum.

- `base_image` config field (`rosman/lifecycle.py::_render_ros_install_block`):
  overrides the default `ros:<distro>` image; rosman adds the ROS 2 apt repo
  and installs `ros-<distro>-ros-base` onto it, using a small
  distro→Ubuntu-codename map (`UBUNTU_CODENAME_FOR_DISTRO`).
- `setup_script` config field: a repo-relative shell script rosman copies
  into the build context and runs as the rosman user after its own base
  setup — the escape hatch for anything `extra_apt_packages` can't express
  (vendor apt repos, `.run` installers, pip installs). Its *content* hash
  feeds `compute_config_hash`, so editing it triggers a rebuild.
- `ensure_image` now builds from a real temporary build-context directory
  (`path=...`) instead of a bare `fileobj` Dockerfile string, since `COPY`
  needs an actual context to copy the setup script from.
- Regression-tested: a real `bash` subprocess runs the generated apt-source
  `echo` command to confirm the produced `sources.list` entry has no stray
  whitespace (an actual bug caught and fixed during implementation — the
  first version broke a quoted shell string across Dockerfile continuation
  lines, embedding literal indentation into the apt source line).
- Not yet verified against a real Docker build — the Dockerfile-rendering
  and hashing logic is unit-tested (including running the actual generated
  shell fragment through bash), but nobody has run `rosman up` with
  `base_image`/`setup_script` set against a live daemon to confirm the full
  build succeeds end to end (e.g. an actual ZED SDK install script).

## Phase 5 — polish — not started
- Working-directory translation edge cases (symlinked workspaces, devices
  mounted outside `workspace_dir`).
- Friendlier error messages throughout.
- Packaging for distribution (pipx-installable release, versioned tags).
- Versioning: package is currently unreleased (`0.0.0`); `0.0.1` gets
  tagged once there's a stable working base confirmed against a live
  Docker daemon.

## Explicitly deferred (see spec §9)
- macOS.
- Full `ros2`/`colcon` CLI reimplementation (rosman is a thin passthrough
  by design).
- Shell tab-completion through the container.
- Multi-host DDS discovery over the internet (would need a VPN mesh, e.g.
  Husarnet, layered on top — out of scope for a single dev machine).
- ROS 1.
