# Custom base images & setup scripts

For projects with heavier requirements than a plain `apt install` —
CUDA, a vendor SDK, anything with its own installer.

```yaml
ros_distro: humble
base_image: nvidia/cuda:12.4.1-devel-ubuntu22.04   # rosman installs ROS 2 onto this itself
gpu: true                                            # nvidia-container-toolkit passthrough
devices: ["/dev/video0"]                             # a camera
setup_script: docker/install_vendor_sdk.sh           # anything apt can't express: repos, .run installers, etc.
```

## `base_image`

Overrides the default `ros:<distro>` image. rosman adds the official ROS 2
apt repository and installs `ros-<distro>-ros-base` onto whatever image you
point it at — useful when you need a base other than the stock ROS image,
most commonly `nvidia/cuda` for exact GPU library versions a vendor SDK
requires.

!!! note
    A bare Ubuntu (or other non-ROS) base image may not set
    `DEBIAN_FRONTEND=noninteractive` itself. rosman's own build sets it
    before any `apt-get install`, but your `setup_script` runs in the same
    build and should not rely on interactive prompts either — a stray
    `tzdata`-style prompt with nothing to answer it will hang the build
    indefinitely rather than fail cleanly.

## `setup_script`

A path (relative to `rosman.yml`, and must stay inside the project — no
`..`, no absolute paths) to a shell script rosman copies into the build
context and runs as the rosman user during the image build. This is the
escape hatch for anything `extra_apt_packages` can't express: a vendor's
own apt repository, a `.run`/`.deb` installer, cloning and building
something from source.

Editing the script's contents is treated as config drift, the same as
editing `rosman.yml` itself — `rosman doctor` will flag it and `rosman
rebuild` picks up the change.

```sh
#!/usr/bin/env bash
set -euo pipefail

curl -fsSL https://example.com/vendor-repo.gpg | sudo tee /usr/share/keyrings/vendor.gpg > /dev/null
echo "deb [signed-by=/usr/share/keyrings/vendor.gpg] https://example.com/apt stable main" | sudo tee /etc/apt/sources.list.d/vendor.list
sudo apt-get update
sudo apt-get install -y vendor-sdk
```
