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

## Phase 3 — networking — partially done
Done:
- Shared bridge network per `network:` group (`rosman/networking.py`).
- CycloneDDS unicast peer XML generation, refreshed on container
  create/remove, delivered via a single bind-mounted file shared by every
  container in the group (see module docstring for why this avoids needing
  a restart to pick up peer changes).
- Domain ID auto-assignment with local collision-avoidance state
  (`rosman/state.py`).

Not yet done:
- `rosman doctor`'s actual two-container talker/listener round trip test
  (spec §5 flags this as the validation step that should happen "before
  calling networking done"). Currently `rosman doctor` checks Docker
  reachability, config validity, drift, and declared device visibility, but
  does not yet spin up a second container to prove the CycloneDDS peer
  config actually results in working discovery. This is the next thing to
  build and the highest-value thing to test against a real Docker daemon
  (not verifiable in a sandboxed dev environment without Docker access).

## Phase 4 — platform-specific extensions — not started
- Linux: X11/XAuth mounting for GUI tools (rviz2/rqt/Gazebo), reusing
  `osrf/rocker`'s approach rather than reinventing it.
- Windows: WSL2/WSLg detection (`rosman/doctor.py::is_wsl2` exists as a
  building block) so rosman skips X11 mounting when WSLg already handles
  display forwarding; `usbipd-win` detection/guidance for devices declared
  in `rosman.yml` but not visible in WSL2 (partially present in
  `rosman doctor`'s device check, needs the actual `usbipd list` shell-out
  and copy-pasteable command suggestions).

## Phase 5 — polish — not started
- Working-directory translation edge cases (symlinked workspaces, devices
  mounted outside `workspace_dir`).
- Friendlier error messages throughout.
- Packaging for distribution (pipx-installable release, versioned tags).

## Explicitly deferred (see spec §9)
- macOS.
- Full `ros2`/`colcon` CLI reimplementation (rosman is a thin passthrough
  by design).
- Shell tab-completion through the container.
- Multi-host DDS discovery over the internet (would need a VPN mesh, e.g.
  Husarnet, layered on top — out of scope for a single dev machine).
- ROS 1.
