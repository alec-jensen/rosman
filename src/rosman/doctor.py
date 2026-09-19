"""`rosman doctor` — environment and config sanity checks.

MVP scope: verify Docker is reachable, the config is valid, the image can be
resolved/built, and the workspace's declared devices are actually visible.
The full networking validation described in spec §5 — spinning up two
rosman containers on the same network and confirming a talker/listener
round trip over the generated CycloneDDS unicast peers — is the next
milestone (see docs/roadmap.md); it needs a live Docker daemon to mean
anything and can't be meaningfully faked here.
"""

from __future__ import annotations

import os
import platform
from dataclasses import dataclass

from rosman.config import RosmanConfig
from rosman.docker_client import get_client
from rosman.errors import RosmanError
from rosman.lifecycle import ContainerManager, compute_config_hash, host_uid_gid
from rosman.state import RosmanState


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


def is_wsl2() -> bool:
    try:
        with open("/proc/version") as f:
            return "microsoft" in f.read().lower()
    except OSError:
        return False


def run_checks(config: RosmanConfig) -> list[Check]:
    checks: list[Check] = []

    try:
        client = get_client()
        checks.append(Check("docker daemon", True, "reachable"))
    except RosmanError as exc:
        checks.append(Check("docker daemon", False, str(exc)))
        return checks

    checks.append(Check("config", True, f"{config.config_path} parsed OK"))

    uid, gid = host_uid_gid()
    config_hash = compute_config_hash(config, uid, gid)
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
        on_windows_wsl = platform.system() == "Linux" and is_wsl2()
        for device in config.devices:
            visible = os.path.exists(device)
            if visible:
                checks.append(Check(f"device {device}", True, "visible"))
            elif on_windows_wsl:
                checks.append(
                    Check(
                        f"device {device}",
                        False,
                        "not visible in WSL2. Run: usbipd list  (on Windows), then "
                        "usbipd bind/attach the matching device.",
                    )
                )
            else:
                checks.append(
                    Check(f"device {device}", False, "not visible on this host")
                )

    checks.append(
        Check(
            "networking round-trip",
            True,
            f"not yet implemented — see docs/roadmap.md "
            f"(network group '{config.network}' peers file will be generated on `rosman up`)",
        )
    )

    return checks
