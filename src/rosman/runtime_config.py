"""Pure, Docker-free calculations shared by lifecycle and warm dispatch."""

from __future__ import annotations

import hashlib
import platform

from rosman.config import RosmanConfig

# Bump whenever the generated Dockerfile changes in a way that affects the
# image. Kept here (rather than lifecycle.py) so warm command dispatch can
# validate the container label without importing docker-py.
DOCKERFILE_TEMPLATE_VERSION = 10


def resolve_network_mode(config: RosmanConfig) -> str:
    if config.network_mode == "auto":
        return "host" if platform.system() == "Linux" else "bridge"
    return config.network_mode


def _setup_script_digest(config: RosmanConfig) -> str:
    if not config.setup_script:
        return ""
    script_path = (config.project_root / config.setup_script).resolve()
    try:
        return hashlib.sha256(script_path.read_bytes()).hexdigest()
    except OSError:
        return f"MISSING:{config.setup_script}"


def compute_config_hash(config: RosmanConfig) -> str:
    """Hash every input that affects the workspace image."""
    payload = "|".join(
        [
            str(DOCKERFILE_TEMPLATE_VERSION),
            config.ros_distro,
            config.rmw_implementation,
            ",".join(sorted(config.extra_apt_packages)),
            ",".join(sorted(config.locked_apt_packages)),
            ",".join(sorted(config.locked_pip_packages)),
            config.base_image or "",
            config.setup_script or "",
            _setup_script_digest(config),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
