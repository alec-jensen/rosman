# Roadmap

Tracking status against the phased build order in [`spec.md`](spec.md) §10.

## Windows live verification (2026-09-19)

Ran `rosman` for real on a Windows 11 machine, both from native Windows
Python (PowerShell/Docker Desktop directly, no WSL2) and confirmed WSL2 is
available on the same box (Ubuntu 24.04) for the fully-featured path.
Answers a real design question: **rosman does not require WSL2** — native
Windows execution against Docker Desktop's named pipe works end-to-end
(`init`, `up`, `doctor`, `status`, `ros2`/`colcon` passthrough including
writing files back to the Windows-side bind mount, `colcon build`, `shell`
with piped non-interactive stdin, `down --remove`). WSL2 remains relevant
only for GUI passthrough (WSLg) and `usbipd-win` USB device attachment,
which are meaningless without it, but the core workflow doesn't need it.

Two real bugs found and fixed, both invisible to the existing unit tests
because they only manifest under real Windows path/process semantics:

1. **`setup_script` path validation used the host `Path` class**
   (`rosman/config.py::_validate_setup_script`), so a POSIX-style absolute
   path like `/etc/passwd` in `rosman.yml` silently passed validation when
   parsed on Windows (`WindowsPath("/etc/passwd").is_absolute()` is `False`
   — no drive letter — even though the path has a root). Since `rosman.yml`
   is a portable config file that may be committed to a repo shared across a
   team's Linux and Windows machines, this was a real path-traversal gap on
   Windows specifically. Fixed by checking both `PurePosixPath` and
   `PureWindowsPath` absoluteness/traversal regardless of host OS.
2. **`ros2`/`colcon` passthrough and `rosman shell` silently produced
   garbled `docker exec` invocations on Windows** (`rosman/dispatch.py`).
   The code picked between `os.execvp` (true process replacement) and a
   `subprocess.call` fallback using `hasattr(os, "execvp")`, wrongly
   assuming that attribute implies POSIX. Windows has `os.execvp` too, but
   it's an emulated spawn-then-exit with unreliable argv-to-command-line
   quoting — it corrupted `docker exec -i ...` badly enough (mangling the
   `docker.exe` path under `C:\Program Files\...`, which contains a space)
   that Docker's CLI parser errored on `-i` as an unrecognized top-level
   flag instead of an `exec` subcommand flag. Fixed by keying the branch on
   `os.name == "posix"` instead, so Windows always uses the working
   `subprocess.call` path (which quotes correctly via
   `subprocess.list2cmdline`).

Also confirmed working as designed: `usbipd` guidance degrades cleanly to
generic install instructions when `usbipd.exe` isn't on `PATH`; Rich's
table/box-drawing output (which looked garbled through this session's
non-UTF-8 shell) renders correctly in a real PowerShell/Windows Terminal
session — not a rosman bug, just a UTF-8 console requirement.

Not yet live-verified: WSL2 GUI passthrough against a real X/WSLg session,
`usbipd` device attach against real hardware, and `rosman doctor
--network-check` from native Windows (only exercised from Linux so far).

## Live verification (2026-09-19)

Docker access on the dev machine was fixed and the core lifecycle got its
first real end-to-end run against an actual daemon (previously everything
below was only unit-tested with mocks). Confirmed working: `rosman up`
(image build + container start), `ros2 topic list`/`pkg create`/`pkg list`
passthrough, `colcon build`, the workspace overlay auto-sourcing on the
very next command with no manual `source` step, `rosman shell` with piped
non-interactive stdin, host-UID-matched file ownership on bind-mounted
files, and — the design's highest-risk piece — `rosman doctor
--network-check`'s two-container CycloneDDS talker/listener round trip,
which passed on the first real attempt.

This first live run also caught three real bugs that mocked tests
structurally could not have caught (all fixed, all regression-tested where
practical): `ros2`/`colcon` missing from `$PATH` on every `docker exec`
(needed `/etc/profile.d` sourcing + routing commands through a login
shell), piped/non-interactive stdin being silently discarded by `rosman
shell` (an `-i` vs `-t` flag mixup), and `build`/`install`/`log` named
volumes coming up root-owned so `colcon build` failed with EACCES (fixed
by pre-creating and chowning those paths in the image before the volumes
ever mount). A `DOCKERFILE_TEMPLATE_VERSION` constant was added to
`compute_config_hash` specifically so future fixes like these actually
invalidate existing users' cached images instead of leaving them stuck on
an old, buggy build.

**Second round (same day):** this machine turned out to actually have an
NVIDIA GPU with `nvidia-container-toolkit` installed, so `gpu: true` and
the Phase 6 `base_image`/`setup_script` fields got live-verified too —
see Phase 6 below for the tzdata-hang bug that surfaced and got fixed in
the process. Also confirmed the standard ROS 2 demo packages
(`demo_nodes_cpp`/`demo_nodes_py`) work correctly through rosman's
passthrough once declared via `extra_apt_packages` (they're not in the
base `ros:<distro>` image by default) — real talker/listener messages
observed via `rosman topic echo`.

**Third round (same day): team-shared images.** Requested support for a
team building one image once and sharing it via a registry (GHCR named
specifically), explicitly *not* "bring your own pre-built image" — see
`spec.md`'s second addendum and Phase 7 below. Implementing this
correctly surfaced and fixed a real, previously-latent bug: the image used
to bake in the *builder's own host UID/GID*, which meant two teammates
with different UIDs building the same `rosman.yml` got different image
tags — there was no shared tag to actually push/pull. Decoupled: the image
now bakes in a fixed uid/gid (1000), and host UID/GID matching happens
purely at container-runtime (`--user`, unchanged); an `ENTRYPOINT` script
patches `/etc/passwd` for whatever arbitrary UID actually runs the
container. **Live-verified 2026-09-19**, including the exact scenario the
whole feature exists for: built an image, pushed it to a real (local,
auth-free) test registry, removed every local copy to simulate a fresh
machine, ran `rosman up` and confirmed it pulled instead of rebuilding,
and directly confirmed the pulled image works correctly under a genuinely
different, arbitrary UID (not 1000) — `sudo`, `$HOME`, file writes to
`build`/`install`/`log`, and `ros2`/`colcon` on `$PATH` all correct.

**Fourth round (same day): Linux X11 GUI passthrough.** This dev machine
turned out to actually have a real X server (`DISPLAY=:0`, a live GNOME
session) and `xauth` installed, contradicting the earlier assumption here
that GUI passthrough couldn't be tested without real hardware.
**Live-verified 2026-09-19**: built a workspace with `x11-apps` via
`extra_apt_packages`, confirmed `rosman up` correctly wired up
`DISPLAY`/`XAUTHORITY` and mounted both `/tmp/.X11-unix` and the generated
XAuth cookie, ran `xeyes` inside the container, and confirmed via
`xlsclients`/`xwininfo` on the host that it registered as a genuine
connected X client and produced a real, window-manager-decorated window on
the actual desktop — not just "the process started without erroring."

The Windows path has separately been live-verified from a real Windows
machine (native, no WSL2 required) — see "Windows live verification"
above. Combined with this round, the only pieces of the design still
without any live verification at all are WSLg GUI passthrough and
`usbipd` device attach against real Windows hardware.

## Phase 1 — single container, no networking — done
- Config resolver (`rosman/config.py`): finds/validates `rosman.yml`,
  walking up from cwd like `.git` discovery.
- `rosman init` scaffolds a config.
- Container lifecycle manager (`rosman/lifecycle.py`): per-workspace image
  build, container create/start/stop/remove. Originally baked the
  builder's own host UID/GID into the image (rocker-style); as of Phase 7,
  the image bakes in a fixed identity instead and UID/GID matching happens
  purely at container-runtime, so the same image is shareable across a
  team regardless of who built it.
- `ros2`/`colcon` passthrough (`rosman/dispatch.py`) with working-directory
  translation into the container's mounted workspace. **Live-verified
  2026-09-19** (see "Live verification" above) — this is also where the
  `$PATH`/login-shell and piped-stdin bugs were found and fixed.

## Phase 2 — lifecycle robustness — done
- Config drift detection (`ContainerManager.detect_drift`): compares
  ros_distro, image config hash, network group, and domain id labels on the
  running container against the current `rosman.yml`.
- `rosman status`, `rosman rebuild` (with confirmation prompt, per spec §11
  leaning towards requiring confirmation rather than silent recreation),
  `rosman down --remove`.
- Named volumes for `build/`, `install/`, `log/`, keyed by workspace +
  distro (`rosman/naming.py::volume_name`), separate from the source bind
  mount. **Live-verified 2026-09-19** — this is also where the volume
  ownership bug (root-owned volumes breaking `colcon build` under the
  UID-matched user) was found and fixed.

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
  pull/build an image. **Live-verified 2026-09-19**: the talker/listener
  round trip passed against a real Docker daemon on the first attempt.

## Phase 4 — platform-specific extensions — done, mostly live-verified
- Linux: X11/XAuth mounting for GUI tools (rviz2/rqt/Gazebo)
  (`rosman/platform_support.py::gui_passthrough`), generating a
  rocker-style masked XAuth cookie rather than sharing the whole host
  `~/.Xauthority`, wired into `ContainerManager.create_container`.
  Degrades gracefully (skips the cookie, keeps `$DISPLAY`) if `xauth` isn't
  installed on the host, rather than failing `rosman up`. **Live-verified
  2026-09-19** (see "Live verification" above): a real `xeyes` window from
  inside a rosman container, rendered on the actual host desktop.
- Windows: WSL2/WSLg detection (`rosman/platform_support.py::is_wsl2`) so
  rosman skips the Linux X11/XAuth logic and just bind-mounts WSLg's
  existing `/tmp/.X11-unix` and `/mnt/wslg` sockets through untouched.
  `usbipd-win` guidance in `rosman doctor`: shells out to `usbipd list` (or
  `usbipd.exe list` from WSL2) when available and prints the BUSID listing
  alongside the bind/attach commands; falls back to generic install
  instructions if `usbipd` isn't found on PATH. The core Windows workflow
  (not GUI/device-specific) is live-verified — see "Windows live
  verification" above — but WSLg GUI passthrough and `usbipd` device
  attach specifically are not yet tested against real Windows hardware.
- One documented limitation: rosman only wires up the X11/GPU *plumbing*.
  It does not install GUI packages (rviz2, rqt, Gazebo) into the per-
  workspace image — that's what `extra_apt_packages` in `rosman.yml` is
  for, since the base `ros:<distro>` image is minimal.

## Phase 6 — image customization for heavier per-project needs — done, live-verified
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
- Two real bugs caught and fixed via actual builds, not just unit tests:
  (1) the apt-source `echo` command originally broke a quoted shell string
  across Dockerfile continuation lines, corrupting the `sources.list`
  entry with stray whitespace; (2) building `base_image` against a bare
  Ubuntu base (e.g. `nvidia/cuda`) hung for 20+ minutes on an interactive
  `tzdata` timezone prompt during `apt-get install ca-certificates`, since
  unlike `ros:<distro>`, a bare base doesn't set
  `DEBIAN_FRONTEND=noninteractive` itself. Both fixed and
  regression-tested.
- **Live-verified 2026-09-19** on a real build combining `base_image:
  nvidia/cuda:12.4.1-devel-ubuntu22.04` + `setup_script` + `gpu: true` —
  the exact shape of the project's actual use case (CUDA + a vendor SDK
  installer + GPU access): ROS 2 correctly apt-installed onto the CUDA
  image and on `$PATH`, the setup script executed during the build
  (confirmed via a marker file it wrote), `nvidia-smi` and
  `ros2 topic list` both working on top of the custom base image. Not
  tested with an actual ZED SDK installer specifically (no camera/license
  here) — the mechanism is proven, a real vendor script is the next thing
  to try against real hardware.

## Phase 7 — team-shared images via a registry — done, live-verified
Not in the original spec; added 2026-09-19 at Alec's explicit request, with
one explicit non-goal: **not** "bring your own pre-built image" — rosman
still always builds the image itself. Full rationale in `spec.md`'s second
addendum.

- `registry_image` config field (`rosman/naming.py::image_name`): when
  set, replaces the local-path-based repository name entirely, so the
  same content-hash tag is portable across machines/builders.
- `rosman push` (`ContainerManager.push_image`): builds locally if needed,
  then pushes to `registry_image`. Relies on the user's own
  `docker login`; rosman doesn't manage registry credentials.
- `ContainerManager.ensure_image` now tries `docker pull` against
  `registry_image` before building locally, falling back to a local build
  if nothing's been pushed yet or the registry isn't reachable. Returns an
  `ImageResult(tag, source)` (`"cached"`/`"pulled"`/`"built"`) so
  `rosman up` can tell the user which one happened.
- **The UID-decoupling fix** (image bakes in a fixed 1000:1000 identity;
  an `ENTRYPOINT` script patches `/etc/passwd` for whatever arbitrary UID
  actually runs the container; `/home/rosman`,
  `build`/`install`/`log`, and `/etc/passwd` are made world-writable
  instead of chowned to a specific UID at build time; `sudo` is granted to
  `ALL` rather than one baked username) — required for `registry_image` to
  mean anything across teammates with different host UIDs. This loosens
  in-container permissions somewhat; judged acceptable for a personal/team
  dev container's threat model, not a multi-tenant one. `DOCKERFILE_TEMPLATE_VERSION`
  bumped to 5.
- **Live-verified 2026-09-19**: built an image, pushed to a real local test
  registry, simulated a fresh machine (removed every local copy), ran
  `rosman up` and confirmed it printed "Pulled shared image..." instead of
  building, and directly ran the shared image under `--user 88888:88888`
  (a UID with no pre-existing passwd entry) to confirm `id`/`whoami`,
  `sudo whoami` → `root`, `$HOME` resolution, and write access to
  `build`/`install`/`log` all work correctly for a UID that never built
  the image.

## Phase 5 — polish — mostly done
- Working-directory translation edge cases: checked both flagged cases.
  Symlinked workspaces already work correctly with no code changes needed
  — `os.getcwd()`/`Path.cwd()` always return the real, symlink-resolved
  path, and `config.workspace_root` is already `.resolve()`d, so
  `translate_cwd` naturally handles a workspace reached via an unrelated
  symlink alias (regression test added). `devices:` entries were never
  actually workspace-relative in the first place — they're host-absolute
  paths mapped 1:1 into the container — so "devices mounted outside
  workspace_dir" wasn't a real gap to begin with.
- Friendlier error messages: added a `DockerException` safety net in
  `cli.py::main` so any docker-py error a specific code path didn't
  already wrap into a clean `RosmanError` (a name conflict, an invalid
  device path, "could not select device driver" for a missing GPU
  runtime, etc.) still prints one line instead of a raw traceback. Also
  fixed `RosmanState.assign_domain_id` raising a bare `RuntimeError`
  (domain ID exhaustion) instead of `RosmanError`, which would have
  slipped past that same safety net.
- Packaging: verified for real, not just assumed — `uv build` produces a
  standard wheel, and `pipx install dist/rosman-*.whl` installs and runs
  correctly as a standalone `rosman` command with no dependency on the
  dev environment.
- Not done: versioned release tags (still `0.0.0`, see below).

## Phase 8 — per-machine config overrides — done
`rosman.local.yml` (`rosman/config.py::local_override_path`): a gitignored,
optional file next to `rosman.yml` whose fields shallow-override the
checked-in config — for values that are inherently per-machine, most
notably `devices:` (a USB serial adapter or camera won't land at the same
path on every teammate's machine). `rosman init` auto-appends it to an
existing `.gitignore`. Validated through the same per-field validators as
the main config (applied to the merged dict), so a bad override value
fails the same way a bad main-config value would.

## Versioning
Package is currently unreleased (`0.0.0` in both `pyproject.toml` and
`rosman.__version__`). `0.0.1` gets tagged once Alec confirms there's a
stable working base — that milestone call is his to make, not something
to infer from test/CI status alone.

## Explicitly deferred (see spec §9)
- macOS.
- Full `ros2`/`colcon` CLI reimplementation (rosman is a thin passthrough
  by design).
- Shell tab-completion through the container.
- Multi-host DDS discovery over the internet (would need a VPN mesh, e.g.
  Husarnet, layered on top — out of scope for a single dev machine).
- ROS 1.
