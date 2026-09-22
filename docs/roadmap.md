# Roadmap

Tracking status against the phased build order in [`spec.md`](spec.md) §10.

## Windows hardware live verification: WSLg GUI + usbipd (2026-09-19)

Closed the two remaining unverified Windows items from the prior round, both
against real hardware on the same Windows 11 machine (WSL2 Ubuntu 24.04 +
Docker Desktop, WSL integration enabled for that distro).

**WSLg GUI passthrough — confirmed working, no bugs found.** Built a test
workspace with `x11-apps` via `extra_apt_packages`, ran `rosman up` from
*inside* WSL2 (required for `is_wsl2()` to detect it), and confirmed via
`docker inspect` that rosman correctly took the WSL2 branch: only
`/tmp/.X11-unix` and `/mnt/wslg` were bind-mounted (no XAuth cookie
generated — the Linux X11 logic was correctly skipped). Launched `xeyes`
inside the container and verified with a small Xlib script querying the
real WSLg X server directly that a genuine, mapped/viewable top-level
window appeared at the moment `xeyes` started and disappeared the moment it
was killed — proof of an actual window, not just a process that didn't
error.

**usbipd-win device attach — confirmed working, but found and fixed a real
bug in `devices:` passthrough (not Windows-specific).** Installed
usbipd-win, bound and attached a real USB mass storage device to WSL2,
declared it in `rosman.yml`'s `devices:`, and confirmed `rosman doctor`
correctly reports it visible once attached (and gives the documented
BUSID/bind/attach hint beforehand). `rosman up` correctly passed the
device through to the container (`lsblk` showed it) — but reading it
failed with "Permission denied" for the container's default non-root user.
Root cause: `--device` only grants cgroup-level access to the node; the
node's own Unix permissions (owner `root`, group `disk`/`dialout`/etc.,
mode `0660` — standard for block/serial/video devices on Linux) still
blocked a user with no matching group membership, and nothing in
`create_container` ever granted one. This wasn't caught by any prior round
(including the earlier live-Docker verification with a real GPU) because
those never exercised `devices:` against an actual device with restrictive
permissions. Fixed in `rosman/lifecycle.py`:
- Added `DEVICE_GROUPS` (`dialout`, `video`, `audio`, `plugdev`, `disk`,
  `tty`, `uucp` — the standard Debian/Ubuntu groups that own hardware
  device nodes) and pass it to `containers.create(group_add=...)` whenever
  `devices:` is non-empty. This is a runtime property, not baked into the
  image, so it isn't affected by `--user`/host-UID matching.
- `render_dockerfile` now defensively creates whichever of those groups
  don't already exist, so this works even against a minimal `base_image`
  override that might be missing one (e.g. `plugdev`), not just the stock
  `ros:<distro>` image. `DOCKERFILE_TEMPLATE_VERSION` bumped to 6 so
  existing cached images get rebuilt and pick this up.
- **Live-verified the fix**: rebuilt the image, reattached the same USB
  device, and confirmed the container's default user now has
  `disk`/`dialout`/etc. as supplementary groups and can read the device
  directly with no `sudo` needed.

Also encountered, unrelated to rosman itself: Docker Desktop's WSL
integration socket flapped a few times mid-session for reasons outside
rosman's control (once from a `wsl --terminate` during debugging, once
from what looked like a Docker Desktop self-update) — a good reminder that
`rosman doctor`'s existing "Docker Desktop running with WSL2 backend
enabled" hint is about as specific as it can usefully be; the actual
underlying cause of a dead socket varies and isn't something rosman can
diagnose further. Separately, `doctor.py::_usbipd_hint`'s
`shutil.which("usbipd.exe")` lookup can fail to find a genuinely-installed
usbipd if WSL2's Windows-PATH interop is stale for whatever reason (seen
in this session); not fixed here since it self-resolves with a normal
fresh terminal in the common case, but worth revisiting if it turns out to
bite real users, e.g. by also checking the winget default install path
(`/mnt/c/Program Files/usbipd-win/usbipd.exe`) as a fallback.

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

WSL2 GUI passthrough and `usbipd` device attach against real hardware were
live-verified in a follow-up round — see "Windows hardware live
verification" above (the latter surfaced a real `devices:` permissions bug,
now fixed). Still not live-verified: `rosman doctor --network-check` from
native Windows (only exercised from Linux so far).

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
  instructions if `usbipd` isn't found on PATH. **Live-verified 2026-09-19**
  — see "Windows hardware live verification" above: a real `xeyes` window
  through WSLg, and a real USB device attached via usbipd, visible in the
  container and (after fixing a real permissions bug) actually readable by
  its default user.
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

## Phase 9 — automated releases + update notifications — done, live-verified
Two related pieces, both explicitly requested:

- **Release pipeline** (`.github/workflows/release.yml`, `packaging/`):
  a `v*` git tag triggers a build (self-contained PyInstaller binary,
  built inside `ubuntu:22.04` for glibc compatibility), packaging into
  `.deb`/`.rpm`/pacman via `nfpm`, GPG signing, and publishing real signed
  apt/dnf/pacman repositories to GitHub Pages
  (`https://alec-jensen.github.io/rosman/`) plus a GitHub Release. Full
  design reasoning (why not "bring your own image"-style shortcuts, the
  chosen channels, the manual-tag-triggers-everything-else model) is in
  `spec.md`'s addenda.
- **Update notifications** (`rosman/update_check.py`): checks GitHub
  Releases' latest tag at most once every 24h (cached in the same local
  state file as everything else), and shows a one-line notice at most
  once every 24h when the cached latest version differs from what's
  running -- printed *before* dispatch (not after), since passthrough/
  shell commands replace the process via `os.execvp` on POSIX and never
  return to Python for an "after" notice to run. Any network failure is
  swallowed silently; the check must never make a command feel slow or
  broken. Gated on `stderr` being a real terminal, so scripted/CI usage
  never sees it.

Both **live-verified 2026-09-19** against real infrastructure, not just
locally: pushed four real test tags through the actual release workflow
(cleaned up afterward), catching three real bugs invisible to a single
local pass -- `$VERSION` not reaching signing containers (a quoting bug),
`repo-add --sign` failing *silently* against a passphrase-protected key,
and `gpg` refusing to overwrite fixed-name signature files on a *second*
release specifically (a bug class only a real re-release can surface).
Final confirmation: real `apt install`/`dnf install`/`pacman -S` against
the live signed repos, with full signature verification enabled, correctly
installing and self-reporting the tagged version. The update checker was
verified against the live (currently-empty, since all test releases were
cleaned up) GitHub Releases API, confirming graceful no-op behavior when no
release exists yet, plus a full notice-rendering pass with a mocked cached
state.

Not done: Windows package manager support and PyPI publishing (out of
scope for this pass, per explicit direction).

## Versioning
Package is currently unreleased (`0.0.0` in both `pyproject.toml` and
`rosman.__version__`). `0.0.1` gets tagged once Alec confirms there's a
stable working base — that milestone call is his to make, not something
to infer from test/CI status alone.

## Explicitly deferred (see spec §9)
- macOS.
- Full `ros2`/`colcon` CLI reimplementation (rosman is a thin passthrough
  by design).
- Multi-host discovery over the internet via a VPN mesh (e.g. Husarnet) —
  declined outright, not just deferred; see the 2026-09-19 multi-host
  section below and spec.md's third addendum. LAN-only multi-host
  (`remote_peers`) is implemented.
- ROS 1.

## Shell tab-completion (2026-09-19)

"how could we make tab completions possible?" Verified feasibility
empirically against a real running container before building anything:
`ros2` and `colcon` are both already `argcomplete`-instrumented (their own
Python entry points call `argcomplete.autocomplete()` unconditionally at
startup — confirmed by finding `ros2-argcomplete.bash`/
`colcon-argcomplete.bash` in a real image and manually driving the
protocol with `_ARGCOMPLETE=1`/`COMP_LINE`/`COMP_POINT` env vars against
`ros2 to` -> `topic`, `ros2 topic echo ` -> real live topic names
(`/parameter_events`, `/rosout`), and `colcon bui` -> `build`). So rosman
doesn't reimplement any completion logic, just relays that same protocol
through the `docker exec` boundary (`rosman/completion.py`):

- `rosman completion bash`/`zsh` prints an install script
  (`eval "$(rosman completion bash)"` in `.bashrc`); zsh reuses the bash
  function via `bashcompinit`, the same pattern colcon/ros2's own `.zsh`
  hooks use.
- The installed shell function calls a hidden `rosman __complete
  <words...>` for the actual work, which reconstructs the equivalent
  `ros2 ...`/`colcon ...` line (mirroring `dispatch_passthrough`'s own
  rule) and runs it inside the container with the argcomplete env vars,
  relaying the IFS-separated candidates back to stdout.
- Deliberately uses `find_container` (never `ensure_running`) and a 3s
  subprocess timeout — pressing Tab must never auto-start or block on a
  container build, and any failure (no config, no container, container
  not running, docker error, timeout) silently yields zero candidates.
- The one hidden command (`__complete`) is excluded from
  `_maybe_show_update_notice()` too — that check firing on every keystroke
  would be bizarre.
- Live-verified end-to-end, not just unit-tested: a real bash session
  sourcing the printed completion script, driving `_rosman_complete`
  directly against a real running container, confirmed `rosman do` ->
  `doctor`/`down` (deduped against `ros2`'s own real `doctor` subcommand,
  which collides with rosman's reserved `doctor` by coincidence),
  `rosman topic ec` -> `echo`, and `rosman colcon bui` -> `build`.

## Auto-start UX + progress rendering (2026-09-19)

Two related fixes/features from the same ask ("it should automatically
start the containers when needed, without explicitly needing a `rosman
up`", plus "progress spinners/bars... whenever something will take more
than a few seconds"):

- **Real bug found and fixed**: `cmd_passthrough`/`cmd_shell` already
  called `ContainerManager.ensure_running`, which *did* auto-create a
  container on first use — but the "Starting rosman container..." status
  message was printed *after* `ensure_running()` returned, i.e. after the
  (potentially slow, first-time) build had already finished. During that
  build, the command produced zero output, indistinguishable from a hang.
  Fixed with `cli._ensure_running_with_notice`, which checks
  `find_container()` itself and prints the notice *before* calling
  `create_container()`.
- **Progress rendering** (`rosman/progress.py`): image build/pull/push all
  switched from docker-py's high-level, fully-blocking
  `images.build()`/`images.pull()` (which don't return until the whole
  operation finishes, so no live progress is possible through them at
  all — confirmed by reading docker-py's own source) to the low-level
  `client.api.build()`/`client.api.pull()` streaming generators, decoded
  and rendered live via `rich.progress`: a spinner showing the current
  build-log line for `build` (no byte-level progress exists for build
  steps), and real per-layer byte progress bars for `pull`/`push` (Docker
  reports `progressDetail.current`/`total` per layer `id` for those).
  `push` already streamed under the high-level API (`ImageCollection.push`
  is a thin passthrough) so only its rendering needed adding, not its
  transport. A `NullReporter` (drains the same stream silently, still
  raising on `{"error": ...}` entries) keeps `lifecycle.py` renderer-
  agnostic and the existing mocked tests working unchanged in shape.
  Live-verified: a forced-terminal smoke test rendering both a fake
  layered pull and a fake build stream, and a real fresh-workspace
  `rosman topic list` run confirming the notice now prints before the
  build starts.

## Multi-host discovery, LAN only (2026-09-19)

"we need multihost support" — clarified via a follow-up question that a
VPN mesh (the original spec's suggestion, e.g. Husarnet) was explicitly
unwanted: "dont want to have to use vpns. it should just work over lan."
Full design in spec.md's third addendum; summary:

- New `remote_peers: [ip, ...]` config field, added to the existing
  CycloneDDS peers XML alongside local same-host container names (same
  mechanism, not a new one).
- Requires an explicit (non-`"auto"`) `domain_id` — enforced as a config
  error, not a runtime footgun, since two machines' independently
  auto-assigned domain ids would otherwise silently never match.
- `lifecycle.create_container` publishes a UDP port window 1:1
  (`networking.dds_port_range`, mirrors Cyclone DDS's own default port
  formula so every machine derives the identical window from `domain_id`
  alone, no coordination needed) whenever `remote_peers` is set; no
  behavior or port exposure change for projects that don't set it.
- `rosman doctor` gained a check (`networking.detect_lan_ip`) that prints
  this machine's LAN address and the exact port range a teammate's
  firewall needs to allow.
- Live-verified: a real workspace with `remote_peers` set correctly showed
  this machine's actual LAN IP and the computed port range via `rosman
  doctor`, and a `remote_peers` config with `domain_id: auto` was
  correctly rejected at config-parse time with a clear error.

## MkDocs documentation site (2026-09-19)

"create mkdocs wiki/documentation/examples" — a proper docs site alongside
the README, rather than the README growing indefinitely.

- Source lives in `documentation/` (deliberately not `docs/`, which
  already holds the internal spec/roadmap you're reading), built with
  `mkdocs` + `mkdocs-material` (new `docs` dependency group in
  `pyproject.toml`, kept separate from `dev` since it's not needed for
  day-to-day development).
- Structure: a home page, a getting-started walkthrough, a full config
  reference, one guide per major feature (custom images, team-shared
  images, multi-host, tab-completion, GPU/GUI/devices), a set of complete
  example `rosman.yml` files, and a troubleshooting page written from the
  real issues this project's own live-verification work actually hit
  (the `tzdata` interactive-prompt hang, config drift, WSL2 USB devices,
  etc.) reframed as user-facing fixes rather than a development diary.
  `uv run mkdocs build --strict` catches broken nav entries/internal links
  as part of CI.
- **Publishing target required care, not just `mkdocs gh-deploy`**: the
  `gh-pages` branch already hosts the real signed apt/dnf/pacman package
  repos (published by `release.yml`) at `apt/`, `dnf/`, `pacman/`,
  `rosman.gpg*` — and `mkdocs gh-deploy` (via `ghp-import`) replaces an
  entire branch's content by default, which would have destroyed them.
  `.github/workflows/docs.yml` instead reuses release.yml's own safe
  worktree-checkout pattern and `rsync`s the built site in with explicit
  excludes for those paths (no `--delete`), so a docs-only publish can
  never take down the package repos, and vice versa. The docs site
  becomes the actual `https://alec-jensen.github.io/rosman/` landing page
  (that root had no `index.html` before this), while the package repos
  keep their existing subpaths untouched.
- Two-job split (`build` runs on every PR touching docs as a check;
  `publish` only runs on push to `main`) — lower-stakes than the
  package-release pipeline's deliberate manual-tag-only trigger, so
  auto-publish on merge is fine here, matching normal docs-site practice.

## Fix: zsh tab-completion syntax error (2026-09-19, v0.1.1)

Real user report, first real-world use of tab-completion after v0.1.0:
installing via the real dnf repo on Fedora, adding
`eval "$(rosman completion zsh)"` to `~/.zshrc` (oh-my-zsh), every
completion attempt printed `_rosman_complete:18: unrecognized modifier
`C'` instead of completing.

Root cause: `local words=("${COMP_WORDS[@]:1:COMP_CWORD}")` — bash's
offset:length array slice, with the *length* given as a bare variable
name (`COMP_CWORD`) rather than a numeric literal. bash evaluates that
position arithmetically; zsh does not, and instead falls back to trying to
parse `:COMP_CWORD` as a history-style modifier chain, taking just its
first letter (`C`) and erroring since that's not a recognized modifier.
This happens *even with `bashcompinit` loaded* — bashcompinit provides
bash-compatible `complete`/`compgen` builtins and registers the function
with zsh's completion system, but the function body itself is still
parsed by zsh's own native parser, not a bash emulation layer. Reproduced
locally with a real zsh 5.9 before touching anything, to confirm the fix
actually works rather than guessing.

Fixed by dropping the length field entirely
(`"${COMP_WORDS[@]:1}"`) — correct because completion is already
documented as end-of-line only, so `COMP_CWORD` is always the index of the
last word anyway; an offset-only slice needs no length and doesn't hit
this parsing path. Verified against a real running container in both a
real `bash` and a real `zsh` process, not just the two unit tests that
exercise `build_inner_command`/`complete` directly (those never touched
the actual shell-script text, which is exactly how this shipped broken in
v0.1.0 despite tests passing).

## Tab-completion latency (2026-09-19, v0.1.2)

Alec: "is there any way to make the tab completion faster?" Measured
before guessing, using real `time` output against a real running
container, layer by layer:

- Bare `docker exec ... true` (the container boundary itself): ~52ms.
- Sourcing `/opt/ros/<distro>/setup.bash`: ~170ms total (~120ms of actual
  work).
- A full `ros2` invocation (argcomplete dispatch or plain `--help`, same
  cost either way): ~440-500ms -- this is ros2cli's own well-known slow
  startup (it discovers every installed verb/command extension via
  Python entry points on *every* invocation), identical to what native
  `ros2 <TAB>` costs with no Docker involved at all. Not something rosman
  introduces or can fix.
- rosman's own Python-side overhead (`import docker_client` -> pulls in
  the full `docker` package and its own dependency tree of
  requests/urllib3/etc.): ~150ms, of which importing `docker` alone was
  ~101ms. This *is* rosman's own cost, and unlike the ros2cli cost, it was
  pure waste for the completion path -- `find_container`'s job there is
  just "does this one named container exist and is it running," nothing
  that actually needs docker-py's object model.

Fixed by making `completion.py` avoid docker-py entirely: a plain `docker
inspect -f '{{.State.Running}}' <name>` subprocess call replaces
`ContainerManager.find_container` (the container name comes from
`naming.container_name`, a pure function of the workspace path -- the
exact same name `find_container` would have resolved to). Also
restructured `cli.main` so the `__complete` dispatch path is fully
separate from the rest of command dispatch, bypassing `argparse`'s
`args.func(args)` wrapper and its `from docker.errors import
DockerException` import (which alone re-triggers the same ~101ms cost) --
`cmd_complete` already catches everything itself, so none of that
generic machinery was doing anything useful for this path anyway. Every
other `cmd_*` that legitimately needs docker-py now imports it locally
inside its own function body instead of at module top level, so this
doesn't cost those commands anything either.

Net result, measured the same way: ~640ms -> ~550-570ms total per
completion. The remaining time is essentially all `ros2`'s own CLI
startup, correctly outside rosman's control -- set this expectation with
Alec directly rather than overpromising a "fast" result.

## rosdep + rosman.lock (2026-09-19, v0.1.3)

Real user report, first real from-source build after v0.1.0: `rosman
colcon build` succeeded for a cloned `demo_nodes_py`, `rosman run
demo_nodes_py talker` then failed with `ModuleNotFoundError:
example_interfaces` -- a real declared dependency not part of `ros-base`.
Gave the immediate two fixes (a one-off `sudo apt install` and an
`extra_apt_packages` entry), then Alec asked the bigger question: "should
we have some sort of lockfile that makes it easy for rosman to keep track
of environment changes?" Full design in spec.md's fifth addendum;
summary:

- Default image now installs `python3-rosdep` + runs `rosdep init`/`rosdep
  update` at build time.
- `rosman rosdep install` resolves apt deps from workspace `src/`
  packages, installs them into the running container *immediately*
  (usable right away, no rebuild wait), and writes `rosman.lock` -- a new
  auto-generated, git-checked-in file recording `ros_distro` + the
  resolved package list, folded into the image build and config-hash
  exactly like `extra_apt_packages`. Every other `rosdep` subcommand is
  plain passthrough like `colcon`.
- Real bug caught only by testing against an actual container: the real
  (non-simulate) `rosdep install` failed with "Unable to locate package"
  because the image strips `/var/lib/apt/lists/*` after its own build,
  and rosdep doesn't run `apt-get update` itself first. `--simulate`
  never touches apt, so this was invisible until the *real* install was
  tested specifically -- fixed by prefixing `sudo apt-get update &&`.
- Verified rosdep's actual `--simulate` output format empirically (one
  `sudo -H apt-get install -y <pkg>` line per package) against a real
  container with a hand-crafted test `package.xml`, rather than assuming
  it from memory, before writing the parser.
- Live-verified full loop end to end: `rosman rosdep install` resolved
  and installed `ros-humble-example-interfaces` + `ros-humble-turtlesim`
  into a real running container, wrote a correct `rosman.lock`, the
  packages were immediately importable without a rebuild, and `rosman
  rebuild` correctly baked them into the rebuilt image (verified by
  importing both packages again in the freshly rebuilt container). Also
  verified the `rosman.yml`/`rosman.lock` distro-mismatch config error
  fires correctly.

## rosdep completion + error-handling fixes (2026-09-19, v0.2.1)

Real user reports from actually using v0.2.0: "the problem with rosdep tab
complete is it doesnt show as one of the tab completion options." Two real
bugs, both in `completion.py`, both from forgetting to mirror a fix I'd
already made in `dispatch.py`:

- `build_inner_command` still fell through to the `ros2`-prefix branch for
  `rosdep` (`["ros2", "rosdep", ...]`, not a real ros2 subcommand) instead
  of forwarding it directly like `colcon` -- `dispatch.py`'s
  `dispatch_passthrough` got this fix when rosdep passthrough was added,
  `completion.py`'s own copy of the same rule didn't.
- Neither `colcon` nor `rosdep` was ever offered as a first-word
  completion candidate at all (the static list only had rosman's actual
  reserved commands; the dynamic path only asks `ros2 <prefix>`, and
  neither is a real `ros2` subcommand) -- `rosman <TAB>` could never
  suggest either, not just rosdep.

Fixed with a new `PASSTHROUGH_TOOL_NAMES = ("colcon", "rosdep")` constant,
used both in `build_inner_command` and merged into the static first-word
candidate list. Also confirmed and documented a real, permanent
limitation while investigating: `rosdep` itself isn't
`argcomplete`-instrumented (checked directly against a real container --
it doesn't respond to the `_ARGCOMPLETE=1` protocol at all, exit code 2,
no fd-8 output), unlike `ros2`/`colcon`. So `rosdep` completes as a first
word now, but its own subcommands/flags never will -- not fixable without
reimplementing part of rosdep itself, which isn't in scope.

Also investigated a separate report ("rosdep install didnt find anything
until after a rebuild, so i had to rebuild, rosdep install, rebuild").
Tried to reproduce directly against a real container multiple times;
never got a genuine false negative out of rosdep's own dpkg-backed
"is this satisfied" check -- it was reliably correct every time, including
correctly reporting "satisfied" for a package I'd manually installed into
that same container earlier in testing. Most likely explanation: some
earlier action left the package genuinely present in that specific
container (not necessarily the one-off fix I'd suggested earlier -- Alec
wasn't sure either), and the subsequent `rosman rebuild` gave a
genuinely fresh container without it, which is exactly what surfaced the
real need. Documented this interaction in troubleshooting.md rather than
claim a fix for something I couldn't reproduce.

Did fix a real, adjacent bug found *while* trying to reproduce the above:
`resolve_packages` never checked the `--simulate` step's own exit code.
Confirmed against a real container that an unresolvable rosdep key makes
`--simulate` exit 1 with a clear `ERROR:` message and (correctly) no
"apt-get install" lines -- which `cmd_rosdep_install` was silently
treating identically to "nothing needed," misreporting a real failure as
full success. Fixed by threading the exit code through and surfacing the
actual rosdep error when it's non-zero.

Also diagnosed (not a rosman bug, but worth documenting): a separate
`AttributeError: __enter__` deep in a user's cloned `demo_nodes_py` was
traced to cloning the repo's default branch (`rolling`) instead of the
distro-matching `humble` branch -- confirmed directly by diffing
`talker.py` between both branches on the real `ros2/demos` repo (`with
rclpy.init(args=args):`, a newer API, on `rolling`; plain
`rclpy.init(args=args)` on `humble`). Added to troubleshooting.md as a
general "you probably cloned the wrong branch" pattern, since it's a
common, non-rosman-specific ROS 2 gotcha.

## Host networking by default on Linux (2026-09-19, v0.3.0)

Real architecture revision, not an incremental feature. Alec wanted
`ros_tcp_endpoint` (Unity TCP bridge) reachable from Unity running
natively on the same Linux machine. Full story and reasoning in
spec.md's sixth addendum -- the short version: proposed a `ports:` config
field first (built and tested, still used for bridge mode), Alec then
asked for host networking outright, "good on windows nowadays" per his
own research. Checked that claim against current (2026) web sources
before accepting it -- turned out still false for Windows specifically
(an open `microsoft/WSL` issue, a documented WSL2-mirrored-mode/Docker
Desktop port-proxying conflict "as of mid-2026"). Landed on: host
networking default on Linux only (including WSL2, a real Linux kernel),
bridge stays default on Windows, `network_mode: host`/`bridge` overrides
either way.

- `lifecycle.resolve_network_mode`: `"auto"` -> `platform.system() ==
  "Linux"` ? `"host"` : `"bridge"`.
- Host mode: no bridge network, no port publishing, CycloneDDS same-host
  discovery collapses to a bare `127.0.0.1` peer
  (`networking.refresh_peers_host_mode`). `remote_peers` needs no
  publishing either, live-verified: a plain `python3 -m http.server`
  bound inside a host-mode container was reachable from the host via
  `curl` with zero `-p` flags -- exactly the `ros_tcp_endpoint` scenario
  that started this.
- Bridge mode unchanged, plus the new `ports:` field (Compose-style)
  live-verified too: explicit `["18766:18765"]` correctly published and
  reachable.
- Rejected an unconditional "publish every port, zero config" alternative
  after measuring it directly: publishing ~2100 ports made
  `container.start()` hang past Docker's 60s API timeout. Bounded,
  explicit `ports:` avoided this.
- `rosman doctor --network-check`'s round trip now resolves and tests
  whichever mode the real workspace actually uses, instead of always
  bridge -- otherwise the check wasn't representative.
- `network_mode` added to `detect_drift` (new `NETWORK_MODE_LABEL`) but
  deliberately not `compute_config_hash` -- a container-creation setting,
  not an image-build one, matching `devices`/`gpu` precedent.

This is the first genuine reversal of an original "non-negotiable" spec
decision in the project's history, and the first addendum whose trigger
was a live web-search fact-check rather than a design conversation or
container testing alone -- worth remembering the pattern: check a
platform-reliability claim against current, dated sources before
accepting or rejecting it, especially when the original reasoning was
itself platform-specific and time-sensitive.

## Drift prompts on every command, container-stop investigation, pip rosdep fix (2026-09-22, v0.3.1)

Alec, after using rosman for real for a few days: "can we detect when any
commands are run if the build parameters have changed and prompt y/n for
a rebuild? and, make sure everything exits cleanly if the container is
stopped while stuff is running."

**Drift-on-every-command**: previously, config drift was only checked by
`rosman up` (hard refuse unless `--force`) and reported by `rosman
doctor`. Passthrough/`rosman shell` had *no* drift signal at all --
`_ensure_running_with_notice` just found-or-created the container, so
editing `rosman.yml` (or running `rosman rosdep install`, which writes
`rosman.lock`) and then running an ordinary passthrough command silently
kept using the stale container/image with no indication anything had
changed. Fixed with `cli._offer_rebuild`: checks drift on every call to
`_ensure_running_with_notice`, prompts `[y/N]` interactively, and in a
non-interactive session (`sys.stdin.isatty()` false) just warns and
proceeds with the existing container rather than blocking a script/CI
invocation on an unanswerable prompt. Deliberately left `rosman up`'s own
existing (stricter, hard-refuse-unless---force) behavior untouched --
that's a different, already-considered UX choice for an explicit
lifecycle command, not a gap.

**"Exits cleanly if the container is stopped mid-command"**: investigated
directly against real containers rather than assumed broken or written
defensively. Three scenarios tested:
- A long-running passthrough command (`docker exec`, process-replaced via
  `os.execvp`) with the container `docker stop`'d mid-run: exits with 137
  within ~2s. Already `docker`'s own process, not rosman's -- nothing to
  fix.
- `container.exec_run()` (used by `rosdep.py`/`doctor.py`) killed
  mid-call: returns cleanly with exit code 137, no exception, no hang.
- `container.exec_run()` called against an already-stopped container:
  raises a clean `docker.errors.APIError` (409 Conflict) immediately,
  already caught by `main()`'s existing `except DockerException` handler
  (a one-line red message, no raw traceback).

No bug found in any of these -- docker-py's own semantics plus rosman's
existing exception handling already covered this. Reported the finding
honestly rather than add unneeded defensive code for a problem that
didn't reproduce.

**Real bug found and fixed while investigating** (not what was reported,
but the same failure class): `rosman rosdep install` claimed "nothing to
install" for a workspace whose only unmet dependency resolved via `pip`
instead of `apt` -- confirmed live with a real rosdep key
(`adafruit-ads1x15-pip`) declared in a test `package.xml`. Root cause,
found by direct testing rather than reading the parser and guessing:
`pip` wasn't even installed in the default image (a pip-type rosdep
install failed outright with "pip is not installed"), and separately,
`parse_simulate_output` only recognized `apt-get install` lines, so a
resolved pip package was silently invisible even once pip was manually
installed -- `cmd_rosdep_install` then took its "nothing to do" branch
and never called the real install at all. Fixed:
- Default image now installs `python3-pip` alongside `python3-rosdep`.
- `parse_simulate_output` recognizes both installer types (confirmed the
  real pip command shape against a live container:
  `sudo -H --preserve-env=PIP_BREAK_SYSTEM_PACKAGES pip3 install -U
  <pkg>`, distinct enough from the apt shape that both parse
  unambiguously), returning `(apt_packages, pip_packages)`.
- `rosman.lock` gained a `pip_packages:` key alongside `apt_packages:`
  (old lockfiles without the key still load fine, defaulting to empty).
- `render_dockerfile` bakes locked pip packages in via `pip3 install
  --no-cache-dir` with `PIP_BREAK_SYSTEM_PACKAGES=1` (mirrors rosdep's own
  runtime install exactly, needed on newer Ubuntu/Debian's PEP 668
  protection, harmlessly ignored on older ones), installed system-wide as
  root rather than `--user`, sidestepping the arbitrary-UID/HOME-matching
  machinery entirely.
- Live-verified the full loop: a pip-only dependency was correctly
  resolved, installed immediately, written to `rosman.lock`, and baked
  into a `rosman rebuild`d image, confirmed importable in the fresh
  container.

## `rosman config`, `rosman prune`, `doctor --fix` (2026-09-22, v0.4.0)

Alec, asked as an open "what's missing or would make this easier to use"
prompt: "is there any missing features or anything we could do to make it
easier to use/more feature rich." Presented three concrete, verified
candidates rather than a generic brainstorm list; he picked all three.

**`rosman prune`** (`cmd_prune` in cli.py; `list_managed_images`,
`list_prunable_images`, `remove_images` in lifecycle.py) was the one
backed by a real, measured problem, not a guess: checked this actual dev
machine before proposing anything and found 17+ rosman-built images,
~20GB, many for workspaces already deleted from disk or superseded by a
newer config-hash tag — nothing had ever cleaned these up, since every
`rosman.yml`/`rosman.lock` change (or a `DOCKERFILE_TEMPLATE_VERSION`
bump) produces a new content-hash-tagged image and the old one just sits
there. Global (not workspace-scoped) by design, since a deleted
workspace's orphaned images are exactly as real a problem as a rebuilt
one's, and there's no reliable way to enumerate "only this workspace's
old images" that would also catch the deleted-workspace case anyway.
Identifies rosman-built images via a new build-time `MANAGED_LABEL`
(`ensure_image`'s `client.api.build(..., labels=...)`), plus a regex
fallback (`_LEGACY_IMAGE_TAG_RE`) for images built before this label
existed, so upgrading to a labeled version doesn't leave the whole
existing pile invisible to it. Only ever removes an image with zero
container references (running or stopped) — the same safety bar
`docker image prune` uses for dangling images, just scoped to
rosman-tagged ones specifically (which is exactly why this finds real
space `docker system prune` doesn't: a rosman image always has a real
tag, so it's never "dangling" in Docker's own sense).

Real bug caught live while first testing `rosman prune` for real (not a
synthetic test) against this machine's actual 17-image pile:
`images.remove(image_id, force=False)` refuses outright with "image is
referenced in multiple repositories" whenever more than one tag points at
the same image ID — common here, since several small same-config
workspaces produce byte-identical images under different repo names. The
first prune run silently skipped every multi-tagged image as a result
(caught by the `except (APIError, NotFound): continue` that's also there
on purpose, to skip images something else grabbed a reference to in the
meantime — so this failure mode was invisible without checking the
before/after image list directly). Fixed by removing each tag
individually (`docker rmi tag1 tag2 ...` does the same thing) rather than
by bare image ID. Live-verified end to end: found and correctly removed
7.2GB of real orphaned images on this machine, across both the
single-tag and multi-tag cases, while correctly preserving the one image
still referenced by a stopped container.

**`rosman config`** (`cmd_config`) prints the fully *resolved* effective
config — what `domain_id: auto`/`network_mode: auto` actually resolved
to, the current image tag, container status — distinct from `rosman
doctor`, which bundles this into a much broader Docker-touching health
check. Deliberately reads `domain_id`'s auto-assigned value from
persisted state rather than calling `resolve_domain_id` directly, since
that assigns-and-persists on first call and a read-only introspection
command must not have that side effect. Docker reachability is optional
(wrapped in `try/except RosmanError`) — the config-resolution half of the
output should work even offline.

**`rosman doctor --fix`** (`_fix_config_drift`) deliberately narrow in
scope: only auto-rebuilds on config drift, run before the normal checks
so the subsequent report reflects the post-fix state. Everything else
`doctor` reports stays informational-only — in particular, a missing
usbipd-attached device is *not* auto-fixed, since `usbipd bind` needs
Windows-side admin elevation rosman has no reliable way to trigger
non-interactively; attempting it and silently failing would be worse than
not attempting it and saying so.
