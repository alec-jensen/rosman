# Examples

Complete `rosman.yml` files for common setups. Pick the one closest to
yours and adjust.

## A plain workspace

Nothing beyond the ROS 2 distro:

```yaml
ros_distro: humble
```

```sh
rosman init --distro humble   # writes exactly this
rosman colcon build
rosman run demo_nodes_cpp talker
```

## A workspace with extra system packages

```yaml
ros_distro: humble
extra_apt_packages:
  - ros-humble-rviz2
  - ros-humble-demo-nodes-cpp
```

## GPU + a vendor SDK (e.g. a stereo camera SDK)

```yaml
ros_distro: humble
base_image: nvidia/cuda:12.4.1-devel-ubuntu22.04
gpu: true
devices: ["/dev/video0"]
setup_script: docker/install_vendor_sdk.sh
```

See [Custom base images & setup scripts](guides/custom-images.md) for what
`install_vendor_sdk.sh` looks like.

## A team sharing one built image

```yaml
ros_distro: humble
base_image: nvidia/cuda:12.4.1-devel-ubuntu22.04
gpu: true
setup_script: docker/install_vendor_sdk.sh
registry_image: ghcr.io/my-team/my-project
```

One person runs `rosman push` after `docker login ghcr.io`; everyone else
just runs `rosman up` and pulls it. See
[Team-shared images](guides/team-images.md).

## A robot + a laptop on the same LAN

On both the robot and the laptop, the identical checked-in `rosman.yml`:

```yaml
ros_distro: humble
domain_id: 5
remote_peers: ["192.168.1.51"]   # the *other* machine's address -- different on each side
```

`remote_peers` is the one field that differs between the two machines (each
one lists the *other's* address). Run `rosman doctor` on each machine to
get the address to put on the other. See [Multi-host](guides/multi-host.md).

## Everything at once

A team project, GPU-accelerated, with a fixed device, sharing a built
image, reachable from a robot on the LAN:

```yaml
ros_distro: humble
domain_id: 5
base_image: nvidia/cuda:12.4.1-devel-ubuntu22.04
gpu: true
setup_script: docker/install_vendor_sdk.sh
registry_image: ghcr.io/my-team/my-project
remote_peers: ["192.168.1.51"]
extra_apt_packages:
  - ros-humble-rviz2
restart_policy: unless-stopped
```

```yaml
# rosman.local.yml -- not checked in, per-machine
devices: ["/dev/ttyUSB0"]
```
