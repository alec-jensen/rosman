"""Config resolver: finds and parses rosman.yml, and resolves the effective
per-workspace configuration (including `domain_id: auto` assignment).

Discovery walks up from the current working directory the same way `.git`
discovery does, so rosman commands work from any subdirectory of a project.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from rosman.errors import ConfigError

CONFIG_FILENAMES = ("rosman.yml", "rosman.yaml", ".rosman.yml", ".rosman.yaml")

# Official OSRF `ros:<tag>` distros that still receive images. `rolling`
# always tracks the current development distro. Kept as a soft allow-list —
# unknown distros produce a warning-level error message, not a crash, since
# new distros ship periodically and we shouldn't have to be updated in lockstep.
KNOWN_ROS_DISTROS = {
    "noetic",  # ROS 1, kept only so the error message can say "ROS 1 isn't supported"
    "foxy",
    "galactic",
    "humble",
    "iron",
    "jazzy",
    "kilted",
    "rolling",
}

SUPPORTED_RMW_IMPLEMENTATIONS = {"cyclonedds"}

REQUIRED_FIELDS = ("ros_distro",)

_DEFAULTS: dict[str, Any] = {
    "rmw_implementation": "cyclonedds",
    "domain_id": "auto",
    "network": "default",
    "gpu": False,
    "devices": [],
    "workspace_dir": ".",
    "extra_apt_packages": [],
}


@dataclass
class RosmanConfig:
    """The effective configuration for a single rosman-managed workspace."""

    ros_distro: str
    rmw_implementation: str = "cyclonedds"
    domain_id: int | str = "auto"
    network: str = "default"
    gpu: bool = False
    devices: list[str] = field(default_factory=list)
    workspace_dir: str = "."
    extra_apt_packages: list[str] = field(default_factory=list)

    # Not part of the YAML schema — filled in by the loader.
    config_path: Path = field(default=None, repr=False)  # type: ignore[assignment]

    @property
    def project_root(self) -> Path:
        """Directory containing the config file."""
        return self.config_path.parent

    @property
    def workspace_root(self) -> Path:
        """Absolute path to the directory that gets mounted into the container."""
        return (self.project_root / self.workspace_dir).resolve()

    @property
    def project_name(self) -> str:
        return self.project_root.name


def find_config_file(start: Path | None = None) -> Path | None:
    """Walk upward from `start` (default: cwd) looking for a rosman config file.

    Mirrors how `git` discovers the repo root: check the current directory,
    then each parent, until one is found or the filesystem root is reached.
    """
    current = (start or Path.cwd()).resolve()
    for directory in (current, *current.parents):
        for name in CONFIG_FILENAMES:
            candidate = directory / name
            if candidate.is_file():
                return candidate
    return None


def _require_mapping(data: Any, path: Path) -> dict[str, Any]:
    if data is None:
        raise ConfigError(f"{path} is empty. Expected a YAML mapping with at least 'ros_distro'.")
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain a YAML mapping (key: value pairs) at top level.")
    return data


def _validate_devices(value: Any, path: Path) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ConfigError(f"{path}: 'devices' must be a list of strings, e.g. [\"/dev/ttyUSB0\"].")
    return value


def _validate_apt_packages(value: Any, path: Path) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ConfigError(f"{path}: 'extra_apt_packages' must be a list of package name strings.")
    return value


def _validate_domain_id(value: Any, path: Path) -> int | str:
    if value == "auto":
        return "auto"
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{path}: 'domain_id' must be the string \"auto\" or an integer.")
    if not (0 <= value <= 232):
        raise ConfigError(f"{path}: 'domain_id' must be between 0 and 232 (got {value}).")
    return value


def parse_config(text: str, path: Path) -> RosmanConfig:
    """Parse and validate already-read YAML text into a RosmanConfig.

    Raises ConfigError with a human-readable message (never a raw YAML/Python
    traceback) on any problem.
    """
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path} is not valid YAML: {exc}") from exc

    data = _require_mapping(raw, path)

    missing = [f for f in REQUIRED_FIELDS if f not in data]
    if missing:
        raise ConfigError(
            f"{path} is missing required field(s): {', '.join(missing)}."
        )

    unknown = set(data) - set(_DEFAULTS) - set(REQUIRED_FIELDS)
    if unknown:
        raise ConfigError(
            f"{path} has unrecognized field(s): {', '.join(sorted(unknown))}. "
            f"Known fields: ros_distro, {', '.join(_DEFAULTS)}."
        )

    ros_distro = data["ros_distro"]
    if not isinstance(ros_distro, str) or not ros_distro:
        raise ConfigError(f"{path}: 'ros_distro' must be a non-empty string.")
    if ros_distro == "noetic":
        raise ConfigError(
            f"{path}: 'ros_distro: noetic' is ROS 1, which rosman does not support (ROS 2 only)."
        )
    if ros_distro not in KNOWN_ROS_DISTROS:
        raise ConfigError(
            f"{path}: unknown ros_distro '{ros_distro}'. Expected one of: "
            f"{', '.join(sorted(KNOWN_ROS_DISTROS - {'noetic'}))} "
            "(any distro with an official `ros:<tag>` Docker image)."
        )

    rmw = data.get("rmw_implementation", _DEFAULTS["rmw_implementation"])
    if rmw not in SUPPORTED_RMW_IMPLEMENTATIONS:
        raise ConfigError(
            f"{path}: rmw_implementation '{rmw}' is not supported yet. "
            f"rosman's networking model currently only supports: "
            f"{', '.join(sorted(SUPPORTED_RMW_IMPLEMENTATIONS))}."
        )

    workspace_dir = data.get("workspace_dir", _DEFAULTS["workspace_dir"])
    if not isinstance(workspace_dir, str) or not workspace_dir:
        raise ConfigError(f"{path}: 'workspace_dir' must be a non-empty string path.")

    network = data.get("network", _DEFAULTS["network"])
    if not isinstance(network, str) or not network:
        raise ConfigError(f"{path}: 'network' must be a non-empty string.")

    gpu = data.get("gpu", _DEFAULTS["gpu"])
    if not isinstance(gpu, bool):
        raise ConfigError(f"{path}: 'gpu' must be true or false.")

    return RosmanConfig(
        ros_distro=ros_distro,
        rmw_implementation=rmw,
        domain_id=_validate_domain_id(data.get("domain_id", _DEFAULTS["domain_id"]), path),
        network=network,
        gpu=gpu,
        devices=_validate_devices(data.get("devices", list(_DEFAULTS["devices"])), path),
        workspace_dir=workspace_dir,
        extra_apt_packages=_validate_apt_packages(
            data.get("extra_apt_packages", list(_DEFAULTS["extra_apt_packages"])), path
        ),
        config_path=path,
    )


def load_config(path: Path) -> RosmanConfig:
    try:
        text = path.read_text()
    except OSError as exc:
        raise ConfigError(f"Could not read {path}: {exc}") from exc
    return parse_config(text, path)


def resolve_config(start: Path | None = None) -> RosmanConfig:
    """Find and load the rosman config for the workspace containing `start`."""
    path = find_config_file(start)
    if path is None:
        raise ConfigError(
            "No rosman.yml found in this directory or any parent directory.\n"
            "Run `rosman init` to create one."
        )
    return load_config(path)
