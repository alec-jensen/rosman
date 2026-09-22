# Changelog

All notable changes to rosman are documented here. Dates are UTC.
See [GitHub Releases](https://github.com/alec-jensen/rosman/releases) for
downloadable artifacts, and [docs/roadmap.md](docs/roadmap.md) for the
full build/verification history behind each entry.

## v0.4.6 — 2026-09-22

### Fixed

- Failed image builds now show the complete Docker build output, including
  stdout and stderr from a custom `setup_script`, instead of only the
  command's exit code.

## v0.4.5 — 2026-09-22

### Added

- `rosman shell <command> [args...]` runs a command in the container's
  login shell, using the workspace directory and returning its exit code.
  `rosman shell` still opens an interactive shell.

### Fixed

- `rosman prune` now preserves the newest image for each existing
  workspace, even when no container references it. Images referenced by
  running or stopped containers remain protected.

## v0.4.4 — 2026-09-22

### Fixed

- Restore executable file modes from the preserved release tarball before
  building apt, dnf, and pacman packages. GitHub Actions artifact transfer
  stripped those modes and made the v0.4.3 package-manager installs unusable.
- Check the finished RPM's executable and symlink before signing, so a broken
  package cannot be published again.

## v0.4.3 — 2026-09-22

### Performance

- Package-manager installs now use a self-contained PyInstaller directory
  bundle instead of extracting a one-file bundle on every invocation.
- `rosman --version` and hidden completion requests bypass the full CLI
  import path; version checks no longer import Rich or docker-py.
- Warm passthrough commands validate container status and all drift labels
  with one `docker inspect`, then exec directly without loading docker-py;
  stopped matching containers use the same lightweight auto-start path.
- Tab completion now uses one Docker call per request, without a separate
  container-status inspection.
- Image builds keep ROS tooling and workspace setup in shared Docker layers
  ahead of project apt/pip packages, so dependency changes reuse those layers.
  Each image build prints its elapsed time when it finishes or fails.
- Changed or removed `remote_peers` refresh the CycloneDDS config before
  dispatch instead of leaving the previous peer list in use.
- The daily GitHub update request runs in a detached worker, so the first
  command after the 24-hour interval never waits on network I/O.
- apt/dnf/pacman packages install native bash and zsh completion files;
  opening a shell no longer runs `rosman completion ...`.

### Changed

- The standalone Linux GitHub artifact is now a `.tar.gz` bundle. Keep the
  directory together when installing it; package-manager installs remain
  the recommended path.
- Existing workspace containers require `rosman rebuild` to pick up the
  new image layout; rosman reports this as normal config drift.

## v0.4.2 — 2026-09-22

### Fixed

- The background update check was tied to shell startup for anyone with
  tab-completion set up: `rosman completion bash/zsh` (what
  `eval "$(rosman completion bash)"` in `.bashrc`/`.zshrc` runs once per
  shell) wasn't excluded from the check, only the internal `__complete`
  command was.

## v0.4.1 — 2026-09-22

### Added

- `man rosman` — generated directly from the CLI's own argument parser
  (the same one `--help`/tab-completion use), so it can't drift.
  Installed automatically by the apt/dnf/pacman packages.

## v0.4.0 — 2026-09-22

### Added

- `rosman config` — prints the fully resolved effective config for a
  workspace (what `domain_id`/`network_mode` "auto" actually resolved to,
  current image tag, container status).
- `rosman prune` — removes rosman-built Docker images no longer used by
  any container, reclaiming disk space that accumulates as
  `rosman.yml`/`rosman.lock` change over time.
- `rosman doctor --fix` — automatically rebuilds the container if config
  drift is found, before reporting the rest of the checks.

## v0.3.1 — 2026-09-22

### Added

- Config drift is now detected — and a rebuild offered — on *any*
  command that starts the container, not just `rosman up`.

### Fixed

- `rosman rosdep install` could silently report "nothing to install" for
  a dependency that only resolves via `pip` instead of `apt`; the
  default image now includes `python3-pip` and `rosman.lock` tracks both
  package types.

## v0.3.0 — 2026-09-19

### Changed

- **Networking default on Linux is now host networking** (`--network
  host`), including under WSL2 — no port publishing needed for services
  running inside the container. Windows keeps the previous bridge-network
  default, since Docker Desktop's host networking there still has
  documented reliability issues. Override either way with
  `network_mode: host`/`bridge` in `rosman.yml`.

### Added

- `ports:` config field — Docker Compose-style TCP port publishing for
  workspaces using bridge networking.

## v0.2.1 — 2026-09-19

### Fixed

- Tab-completion for `rosman rosdep`/`rosman colcon` (a passthrough rule
  wasn't mirrored between the dispatcher and the completion predictor).
- `rosman rosdep install` could silently swallow a real dependency
  resolution failure and report success instead.

## v0.2.0 — 2026-09-19

### Added

- `rosman rosdep install` and a `rosman.lock` dependency lockfile —
  resolves and installs apt dependencies declared by source packages
  under a workspace's `src/`, and locks them into the built image so
  they survive rebuilds and are shared with your team via git.

## v0.1.2 — 2026-09-19

### Changed

- Tab-completion latency reduced by removing an unnecessary `docker-py`
  import from that hot path (the remaining latency is `ros2`/`colcon`'s
  own CLI startup cost, outside rosman's control).

## v0.1.1 — 2026-09-19

### Fixed

- Tab-completion under zsh (a bash-specific array-slice syntax broke
  zsh's parser, even with `bashcompinit` loaded).

## v0.1.0 — 2026-09-19

### Added

- Auto-starting a workspace container on the first `rosman <command>`
  now shows a visible "Starting rosman container..." message *before*
  the build, plus live progress for image build/pull/push — previously
  a slow first build looked like a silent hang.
- LAN multi-host DDS discovery (`remote_peers`), for talking to a robot
  on the same network — no VPN required.
- Shell tab-completion (`rosman completion bash/zsh`), relaying
  `ros2`/`colcon`'s own `argcomplete` support through the container
  boundary.
- Full documentation site at
  [alec-jensen.github.io/rosman](https://alec-jensen.github.io/rosman/).

## v0.0.1 — 2026-09-19

Initial release. Core workflow (`init`/`up`/`down`/`status`/`rebuild`/
`doctor`/`shell`, plus transparent `ros2`/`colcon` passthrough) live and
verified on both Linux and Windows, including:

- GPU passthrough, X11/WSLg GUI forwarding, and usbipd USB device
  attachment on Windows.
- Custom `base_image`/`setup_script` for projects needing more than a
  plain `apt install` (e.g. CUDA, vendor SDKs).
- Team-shared images via `registry_image` + `rosman push`.
- Per-machine config overrides via `rosman.local.yml`.
- Signed apt/dnf/pacman packages, published via an automated release
  pipeline, with background update-check notifications.
