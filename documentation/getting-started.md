# Getting started

## Install

Prebuilt packages are published to `https://alec-jensen.github.io/rosman/`
and signed with rosman's release GPG key.

=== "apt (Debian/Ubuntu)"

    ```sh
    curl -fsSL https://alec-jensen.github.io/rosman/rosman.gpg | sudo tee /usr/share/keyrings/rosman-archive-keyring.gpg > /dev/null
    echo "deb [signed-by=/usr/share/keyrings/rosman-archive-keyring.gpg] https://alec-jensen.github.io/rosman/apt stable main" | sudo tee /etc/apt/sources.list.d/rosman.list
    sudo apt update && sudo apt install rosman
    ```

=== "dnf/yum (Fedora/RHEL)"

    ```sh
    sudo tee /etc/yum.repos.d/rosman.repo > /dev/null << 'EOF'
    [rosman]
    name=rosman
    baseurl=https://alec-jensen.github.io/rosman/dnf
    enabled=1
    gpgcheck=1
    repo_gpgcheck=1
    gpgkey=https://alec-jensen.github.io/rosman/rosman.gpg.asc
    EOF
    sudo dnf install rosman
    ```

=== "pacman (Arch Linux)"

    ```sh
    sudo pacman-key --add <(curl -fsSL https://alec-jensen.github.io/rosman/rosman.gpg.asc)
    sudo pacman-key --lsign-key <fingerprint printed above>
    echo -e "\n[rosman]\nSigLevel = Required\nServer = https://alec-jensen.github.io/rosman/pacman" | sudo tee -a /etc/pacman.conf
    sudo pacman -Sy rosman
    ```

=== "From source"

    ```sh
    git clone https://github.com/alec-jensen/rosman
    cd rosman
    uv sync --dev
    uv run rosman --help
    ```

`apt upgrade`/`dnf upgrade`/`pacman -Syu` pick up new releases
automatically. Windows package manager support isn't available yet — on
Windows, or if you're not on apt/dnf/pacman, install from source above, or
grab a raw binary/wheel from a
[GitHub release](https://github.com/alec-jensen/rosman/releases).

You need Docker itself already working (Docker Engine on Linux, Docker
Desktop on Windows) — rosman drives Docker, it doesn't install it.

The apt/dnf/pacman packages also install a man page (`man rosman`),
generated directly from rosman's own CLI parser so it can't drift from
`--help`.

## Your first workspace

```sh
cd my-ros2-project
rosman init --distro humble
```

This writes a `rosman.yml` next to your project — check it into git, it's
meant to be shared with your team, the same way a `.nvmrc` or
`rust-toolchain.toml` is:

```yaml
ros_distro: humble
```

Everything else in `rosman.yml` has a sensible default (see
[Configuration](configuration.md)) — a minimal project needs nothing more
than `ros_distro`.

Now just use it:

```sh
rosman topic list      # builds the image and starts the container on first use
rosman colcon build
rosman run demo_nodes_cpp talker
rosman shell            # interactive shell in the container, if you need one
rosman status            # see rosman-managed containers
rosman config            # show the fully resolved effective config
rosman doctor            # sanity-check your environment
```

You never have to explicitly start anything — the first `rosman <command>`
you run builds the image (showing live progress) and starts the container;
every command after that reuses it. If you do want direct control:

```sh
rosman up      # start (or create) the container explicitly
rosman down    # stop it
rosman rebuild # force-recreate it (e.g. after changing rosman.yml)
rosman prune   # remove old images no longer used by any container
```

Anything that isn't one of rosman's own subcommands (`init`, `up`, `down`,
`status`, `config`, `prune`, `rebuild`, `doctor`, `shell`, `push`,
`completion`, `help`) is forwarded verbatim as `ros2 <args>` (or `colcon
<args>`/`rosdep <args>` if the first word is `colcon`/`rosdep`) inside the
workspace container — rosman does not reimplement the `ros2` CLI. Cloning
source packages into `src/`? See [Building from
source](guides/rosdep-lockfile.md) for `rosman rosdep install` before you
hit a missing-dependency error at runtime.

If `rosman.yml`/`rosman.lock` has changed since the container was built,
any command that would use it asks whether to rebuild first —
interactively; in a non-interactive session (no tty, e.g. a script or CI)
it just warns and keeps using the existing container rather than blocking
on a prompt nothing can answer. `rosman doctor --fix` does the same
rebuild automatically as part of a health check.

Next: turn on [tab-completion](guides/tab-completion.md), skim the
[configuration reference](configuration.md), jump straight to
[examples](examples.md) for a setup close to yours, or see
[cleaning up old images](guides/prune.md) once you've rebuilt a few times.
