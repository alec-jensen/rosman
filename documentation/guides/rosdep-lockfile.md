# Building packages from source: rosdep + rosman.lock

Cloning real ROS 2 source packages into your workspace's `src/` often
pulls in apt dependencies that aren't part of the default image —
`colcon build` can succeed while the package still fails at runtime with
a `ModuleNotFoundError` for a message/service package it depends on.

```sh
rosman rosdep install
```

resolves those dependencies from your `src/` packages' `package.xml`
files, installs them into the running container *immediately* (no rebuild
wait — you can use them right away), and writes the result to
`rosman.lock`:

```yaml
# rosman.lock -- generated, check it into git, don't hand-edit
ros_distro: humble
apt_packages:
  - ros-humble-example-interfaces
pip_packages:
  - some-vendor-sdk
```

A dependency can resolve either way depending on whether Ubuntu packages
it for apt — `rosman rosdep install` handles both automatically and locks
whichever one rosdep actually resolved to (the default image includes
`python3-pip` for exactly this).

Run `rosman rebuild` afterward to bake those packages into the image
itself — `rosman.lock` is folded into the build the same way
`extra_apt_packages` is, so it survives rebuilds and is shared with your
team once you check it into git.

Any other `rosdep` subcommand (`update`, `check`, `keys`, ...) is plain
passthrough, exactly like `colcon`:

```sh
rosman rosdep update
rosman rosdep check --from-paths src --ignore-src
```

## Why a lockfile, not just running `rosdep install` yourself

You could just `rosman shell` and run `rosdep install` directly — but that
change only exists in your one running container. It's invisible to
rosman's config-drift detection, and gets silently lost the next time
anyone (including you, later) runs `rosman rebuild` or a teammate builds
the image fresh. `rosman.lock` makes the resolved dependency set part of
the same config-as-code model `extra_apt_packages`/`base_image`/
`setup_script` already use — reproducible and shared via git, not a
one-off manual step.

## `ros_distro` must match

`rosman.lock` records the `ros_distro` it was resolved for (apt package
names are distro-suffixed, e.g. `ros-humble-example-interfaces`), and
`rosman` refuses to load a config where that doesn't match the current
`rosman.yml` — regenerate it with `rosman rosdep install` after changing
`ros_distro`, or delete it if it's stale.

## Limitations

- Locks the resolved apt **package set**, not exact versions — apt
  doesn't support the same exact-version pinning language-level lockfiles
  (`uv.lock`, `Cargo.lock`) do. Reproducible across your team on the same
  distro, not bit-for-bit deterministic.
- `rosman.lock` is regenerated in full each time you run `rosman rosdep
  install` — it reflects whatever's currently under `src/`, not an
  accumulating history.
