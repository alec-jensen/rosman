"""`rosman doctor` — environment and config sanity checks.

Checks Docker reachability, config validity, container drift, GPU/device
declarations, and (opt-in, via `--network-check`, since it's slow and pulls
images) the networking round trip described in spec §5: spin up two
ephemeral rosman containers on the project's network group and confirm a
publisher in one is actually observed by a subscriber in the other over the
generated CycloneDDS unicast peers — proof the bridge-network-plus-unicast-
peers design works end to end, not just that the config file was rendered.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from dataclasses import dataclass

import docker
from docker.errors import APIError, NotFound

from rosman.config import RosmanConfig
from rosman.docker_client import DOMAIN_ID_LABEL, MANAGED_LABEL, NETWORK_GROUP_LABEL, get_client
from rosman.errors import RosmanError
from rosman.lifecycle import ContainerManager, compute_config_hash
from rosman.networking import (
    CYCLONEDDS_CONTAINER_PATH,
    RMW_IMPLEMENTATION_ENV,
    ensure_network,
    refresh_peers,
)
from rosman.platform_support import is_wsl2
from rosman.state import RosmanState

ROUNDTRIP_TOPIC = "/rosman_doctor_chatter"


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


def _usbipd_hint(device: str) -> str:
    usbipd = shutil.which("usbipd.exe") or shutil.which("usbipd")
    if not usbipd:
        return (
            f"'{device}' not visible in WSL2, and usbipd wasn't found on PATH. "
            "Install it on Windows with `winget install usbipd`, then from Windows: "
            "`usbipd list` to find the device's BUSID, `usbipd bind --busid <id>`, "
            "then `usbipd attach --wsl --busid <id>`."
        )
    try:
        listing = subprocess.run(
            [usbipd, "list"], capture_output=True, text=True, timeout=5, check=False
        ).stdout
    except (OSError, subprocess.SubprocessError):
        listing = ""
    hint = (
        f"'{device}' not visible in WSL2. On Windows: find its BUSID below, then "
        f"`usbipd bind --busid <id>` and `usbipd attach --wsl --busid <id>`."
    )
    if listing.strip():
        hint += f"\n{listing.strip()}"
    return hint


def run_checks(config: RosmanConfig, network_check: bool = False) -> list[Check]:
    checks: list[Check] = []

    try:
        client = get_client()
        checks.append(Check("docker daemon", True, "reachable"))
    except RosmanError as exc:
        checks.append(Check("docker daemon", False, str(exc)))
        return checks

    checks.append(Check("config", True, f"{config.config_path} parsed OK"))

    config_hash = compute_config_hash(config)
    checks.append(Check("config hash", True, config_hash))

    state = RosmanState.load()
    manager = ContainerManager(client, state)
    container = manager.find_container(config)
    if container is None:
        checks.append(Check("container", True, "not created yet (run `rosman up`)"))
    else:
        container.reload()
        checks.append(Check("container", True, f"{container.name} ({container.status})"))
        drift = manager.detect_drift(container, config)
        if drift.drifted:
            checks.append(
                Check("config drift", False, "; ".join(drift.reasons) + " — run `rosman rebuild`")
            )
        else:
            checks.append(Check("config drift", True, "container matches rosman.yml"))

    if config.gpu:
        checks.append(
            Check(
                "gpu",
                True,
                "gpu: true — rosman will request nvidia-container-toolkit passthrough; "
                "not independently verified here",
            )
        )

    if config.devices:
        on_wsl2 = platform.system() == "Linux" and is_wsl2()
        for device in config.devices:
            if os.path.exists(device):
                checks.append(Check(f"device {device}", True, "visible"))
            elif on_wsl2:
                checks.append(Check(f"device {device}", False, _usbipd_hint(device)))
            else:
                checks.append(Check(f"device {device}", False, "not visible on this host"))

    if network_check:
        checks.append(run_network_roundtrip(client, manager, config))
    else:
        checks.append(
            Check(
                "networking round-trip",
                True,
                "skipped (pass --network-check to spin up two containers and verify "
                "pub/sub over the generated CycloneDDS peers)",
            )
        )

    return checks


def run_network_roundtrip(
    client: docker.DockerClient, manager: ContainerManager, config: RosmanConfig
) -> Check:
    """Spin up two throwaway containers on the workspace's network group and
    confirm a topic published in one is received in the other, proving the
    bridge network + CycloneDDS unicast peers actually deliver discovery —
    the validation step spec §5 calls out as the highest-priority thing to
    get right before calling the networking design "done".
    """
    domain_id = manager.resolve_domain_id(config)
    group = config.network
    a_name = f"rosman-doctor-{group}-a"
    b_name = f"rosman-doctor-{group}-b"

    # Reuse the workspace's own rosman-built image rather than the bare
    # `ros:<distro>` upstream one: only rosman's build installs the
    # rmw_cyclonedds_cpp package, and RMW_IMPLEMENTATION would fail to
    # load against a plain base image that doesn't have it.
    config_hash = compute_config_hash(config)
    try:
        image = manager.ensure_image(config, config_hash).tag
    except RosmanError as exc:
        return Check("networking round-trip", False, f"could not build/find image: {exc}")

    ensure_network(client, group)
    for name in (a_name, b_name):
        try:
            client.containers.get(name).remove(force=True)
        except NotFound:
            pass

    cyclonedds_path = refresh_peers(client, group)
    environment = {
        "RMW_IMPLEMENTATION": RMW_IMPLEMENTATION_ENV,
        "ROS_DOMAIN_ID": str(domain_id),
        "CYCLONEDDS_URI": f"file://{CYCLONEDDS_CONTAINER_PATH}",
    }
    volumes = {str(cyclonedds_path): {"bind": CYCLONEDDS_CONTAINER_PATH, "mode": "ro"}}
    labels = {MANAGED_LABEL: "true", NETWORK_GROUP_LABEL: group, DOMAIN_ID_LABEL: str(domain_id)}

    containers = []
    try:
        for name in (a_name, b_name):
            c = client.containers.create(
                image,
                name=name,
                command=["sleep", "60"],
                detach=True,
                environment=environment,
                volumes=volumes,
                labels=labels,
                network=ensure_network(client, group).name,
            )
            containers.append(c)
            c.start()

        refresh_peers(client, group)
        talker, listener = containers

        pub_cmd = (
            "ros2 topic pub " + ROUNDTRIP_TOPIC + " std_msgs/String "
            '\'{data: "rosman doctor"}\' -r 5'
        )
        talker.exec_run(["bash", "-lc", pub_cmd], detach=True)

        echo_cmd = f"timeout 15 ros2 topic echo {ROUNDTRIP_TOPIC} --once"
        exit_code, output = listener.exec_run(["bash", "-lc", echo_cmd])
        received = exit_code == 0 and b"rosman doctor" in output

        if received:
            return Check(
                "networking round-trip",
                True,
                f"talker/listener round trip succeeded over network group '{group}'",
            )
        return Check(
            "networking round-trip",
            False,
            f"listener never received the test message (exit {exit_code}): "
            f"{output.decode(errors='replace').strip()[:300]}",
        )
    except (APIError, OSError) as exc:
        return Check("networking round-trip", False, f"error running round trip: {exc}")
    finally:
        for c in containers:
            try:
                c.remove(force=True)
            except NotFound:
                pass
        refresh_peers(client, group)
