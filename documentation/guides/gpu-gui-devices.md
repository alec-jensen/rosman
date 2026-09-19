# GPU, GUIs & devices

## GPU

```yaml
gpu: true
```

Requests `nvidia-container-toolkit` passthrough (the equivalent of `docker
run --gpus all`). Requires the NVIDIA driver and
`nvidia-container-toolkit` already set up on the host — on Windows, that
means WSL2 GPU paravirtualization plus `nvidia-container-toolkit` inside
WSL2, which then works the same way as native Linux from Docker's
perspective. rosman requests the passthrough; it doesn't independently
verify it works (`rosman doctor` notes this).

## GUI apps (rviz2, rqt, Gazebo, ...)

rosman wires up the GUI *plumbing* automatically — X11 socket/XAuth on
Linux, WSLg on Windows — but doesn't install GUI packages itself. Add them
via `extra_apt_packages`:

```yaml
extra_apt_packages: ["ros-humble-rviz2"]
```

**Linux**: rosman bind-mounts `/tmp/.X11-unix`, passes `$DISPLAY`, and uses
an XAuth cookie file — the same mechanism `osrf/rocker`'s X11 extension
uses. Nothing else to configure.

**Windows**: WSLg provides X11/Wayland forwarding automatically inside
WSL2 — rosman detects it and skips the Linux-specific X11 mounting logic
entirely, just making sure `$DISPLAY` isn't clobbered.

## Devices (USB serial adapters, cameras, ...)

```yaml
devices: ["/dev/ttyUSB0"]
```

Passed through via Docker's `--device`, plus group membership
(`dialout`/`video`/`plugdev`/etc.) so the container's non-root user can
actually open the device node — `--device` alone only grants cgroup-level
access, not the node's own Unix group permissions.

Device paths are almost always machine-specific — put them in
[`rosman.local.yml`](../configuration.md#per-machine-overrides-rosmanlocalyml)
rather than the shared `rosman.yml`.

**Windows**: WSL2 doesn't expose host USB devices by default. Use
[usbipd-win](https://github.com/dorssel/usbipd-win)
(`winget install usbipd`) to share a Windows-side USB device into WSL2,
after which it appears as a normal `/dev/ttyUSBx`-style node rosman can
pass through the same as on Linux. `rosman doctor` detects a device listed
in `rosman.yml` that isn't currently visible under WSL2 and prints the
exact `usbipd bind`/`usbipd attach` commands to fix it, including the
device's BUSID from a live `usbipd list`.
