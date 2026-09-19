"""Config resolver: finds and parses rosman.yml, and resolves the effective
per-workspace configuration (including `domain_id: auto` assignment).

Discovery walks up from the current working directory the same way `.git`
discovery does, so rosman commands work from any subdirectory of a project.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath
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

# Docker restart policies rosman will accept. Spec §6 explicitly warns against
# defaulting to "unless-stopped" (WSL2/Docker Desktop can restart sessions
# independently of the user), so the default stays "no" -- an explicit
# `rosman up` is always required after a host reboot unless a project opts
# into something else here.
RESTART_POLICIES = {"no", "unless-stopped", "always", "on-failure"}

REQUIRED_FIELDS = ("ros_distro",)

_DEFAULTS: dict[str, Any] = {
    "rmw_implementation": "cyclonedds",
    "domain_id": "auto",
    "network": "default",
    "gpu": False,
    "devices": [],
    "workspace_dir": ".",
    "extra_apt_packages": [],
    "restart_policy": "no",
    "base_image": None,
    "setup_script": None,
    "registry_image": None,
    "remote_peers": [],
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
    restart_policy: str = "no"
    base_image: str | None = None
    setup_script: str | None = None
    registry_image: str | None = None
    remote_peers: list[str] = field(default_factory=list)

    # Not part of the YAML schema — filled in by the loader from
    # rosman.lock (see lock_path/read_lock), never written in rosman.yml
    # itself.
    locked_apt_packages: list[str] = field(default_factory=list)

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


def _validate_base_image(value: Any, path: Path) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ConfigError(
            f"{path}: 'base_image' must be a non-empty image reference string "
            "(e.g. \"nvidia/cuda:12.4.1-devel-ubuntu22.04\"), or omitted entirely."
        )
    return value


def _validate_registry_image(value: Any, path: Path) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ConfigError(
            f"{path}: 'registry_image' must be a non-empty repository reference "
            "(e.g. \"ghcr.io/my-team/my-project\", with no tag -- rosman appends "
            "its own config-hash tag), or omitted entirely."
        )
    if ":" in value.rsplit("/", 1)[-1]:
        raise ConfigError(
            f"{path}: 'registry_image' must not include a tag ({value!r}) -- rosman "
            "appends its own config-hash tag so pulls/pushes stay in sync with rosman.yml."
        )
    return value


def _validate_remote_peers(value: Any, path: Path) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise ConfigError(
            f"{path}: 'remote_peers' must be a list of non-empty host/IP strings "
            "on your LAN, e.g. [\"192.168.1.51\"]. Run `rosman doctor` on the other "
            "machine to see the address to put here."
        )
    return value


def _validate_setup_script(value: Any, path: Path) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{path}: 'setup_script' must be a non-empty relative path string.")
    # rosman.yml is a portable config file that may be checked into a repo
    # shared across Linux and Windows machines, so this must reject both
    # path styles regardless of which OS is doing the validating -- the host
    # `Path` alone won't catch a POSIX-style absolute path like "/etc/passwd"
    # when rosman runs on Windows (WindowsPath treats it as drive-relative,
    # not absolute).
    posix, windows = PurePosixPath(value), PureWindowsPath(value)
    if (
        posix.is_absolute()
        or windows.is_absolute()
        or ".." in posix.parts
        or ".." in windows.parts
    ):
        raise ConfigError(
            f"{path}: 'setup_script' must be a path relative to this file, inside the "
            f"project (got {value!r})."
        )
    return value


def _load_yaml_mapping(text: str, path: Path) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path} is not valid YAML: {exc}") from exc
    return _require_mapping(raw, path)


def parse_config(text: str, path: Path) -> RosmanConfig:
    """Parse and validate already-read YAML text into a RosmanConfig.

    Raises ConfigError with a human-readable message (never a raw YAML/Python
    traceback) on any problem.
    """
    data = _load_yaml_mapping(text, path)
    return _build_config(data, path)


def _build_config(data: dict[str, Any], path: Path) -> RosmanConfig:
    """Validate an already-loaded (and, for a local override, already-merged)
    mapping and build the effective RosmanConfig. `path` is used only for
    error messages and as the dataclass's `config_path` (which
    `workspace_root`/`project_root` resolve relative to) -- when a local
    override was merged in, this is still the *main* rosman.yml's path, not
    the override's, so those stay anchored to the checked-in project root."""
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

    restart_policy = data.get("restart_policy", _DEFAULTS["restart_policy"])
    if restart_policy not in RESTART_POLICIES:
        raise ConfigError(
            f"{path}: 'restart_policy' must be one of: {', '.join(sorted(RESTART_POLICIES))} "
            f"(got {restart_policy!r})."
        )

    domain_id = _validate_domain_id(data.get("domain_id", _DEFAULTS["domain_id"]), path)
    remote_peers = _validate_remote_peers(
        data.get("remote_peers", list(_DEFAULTS["remote_peers"])), path
    )
    if remote_peers and domain_id == "auto":
        raise ConfigError(
            f"{path}: 'domain_id' must be an explicit integer (not \"auto\") when "
            "'remote_peers' is set. Auto-assigned domain ids are chosen "
            "independently on each machine and won't match across hosts, which "
            "would silently break discovery instead of just failing loudly here."
        )

    return RosmanConfig(
        ros_distro=ros_distro,
        rmw_implementation=rmw,
        domain_id=domain_id,
        network=network,
        gpu=gpu,
        devices=_validate_devices(data.get("devices", list(_DEFAULTS["devices"])), path),
        workspace_dir=workspace_dir,
        extra_apt_packages=_validate_apt_packages(
            data.get("extra_apt_packages", list(_DEFAULTS["extra_apt_packages"])), path
        ),
        restart_policy=restart_policy,
        base_image=_validate_base_image(data.get("base_image", _DEFAULTS["base_image"]), path),
        setup_script=_validate_setup_script(
            data.get("setup_script", _DEFAULTS["setup_script"]), path
        ),
        registry_image=_validate_registry_image(
            data.get("registry_image", _DEFAULTS["registry_image"]), path
        ),
        remote_peers=remote_peers,
        config_path=path,
    )


def local_override_path(config_path: Path) -> Path:
    """The machine-local override file for a given rosman.yml -- e.g.
    `rosman.yml` -> `rosman.local.yml`, `.rosman.yaml` -> `.rosman.local.yaml`.
    Meant to be gitignored: for values that are inherently per-machine, most
    notably `devices:` (a USB serial adapter or camera is very unlikely to
    land at the same /dev path, or COM port, on every teammate's machine)."""
    return config_path.with_name(f"{config_path.stem}.local{config_path.suffix}")


LOCK_FILENAME = "rosman.lock"


def lock_path(config_path: Path) -> Path:
    """The auto-generated dependency lockfile for a workspace -- always
    named `rosman.lock` regardless of the main config's own filename,
    matching the fixed-name convention other ecosystems' lockfiles use
    (package-lock.json, Cargo.lock, uv.lock). Produced by `rosman rosdep
    install`; meant to be checked into git and never hand-edited."""
    return config_path.parent / LOCK_FILENAME


def write_lock(config_path: Path, ros_distro: str, apt_packages: list[str]) -> Path:
    path = lock_path(config_path)
    payload = {"ros_distro": ros_distro, "apt_packages": sorted(set(apt_packages))}
    header = (
        "# rosman.lock -- generated by `rosman rosdep install`. Do not edit "
        "by hand; it's regenerated in full each time.\n"
    )
    path.write_text(header + yaml.safe_dump(payload, sort_keys=False))
    return path


def _read_lock(config_path: Path, ros_distro: str) -> list[str]:
    path = lock_path(config_path)
    if not path.is_file():
        return []
    try:
        text = path.read_text()
    except OSError as exc:
        raise ConfigError(f"Could not read {path}: {exc}") from exc
    data = _load_yaml_mapping(text, path)
    locked_distro = data.get("ros_distro")
    if locked_distro != ros_distro:
        raise ConfigError(
            f"{path} was generated for ros_distro {locked_distro!r}, but "
            f"{config_path} now says ros_distro: {ros_distro!r}. Regenerate "
            f"it with `rosman rosdep install`, or delete it if it's stale."
        )
    packages = data.get("apt_packages", [])
    if not isinstance(packages, list) or not all(isinstance(p, str) and p for p in packages):
        raise ConfigError(f"{path}: 'apt_packages' must be a list of non-empty strings.")
    return packages


def load_config(path: Path) -> RosmanConfig:
    try:
        text = path.read_text()
    except OSError as exc:
        raise ConfigError(f"Could not read {path}: {exc}") from exc
    data = _load_yaml_mapping(text, path)

    override_path = local_override_path(path)
    if override_path.is_file():
        try:
            override_text = override_path.read_text()
        except OSError as exc:
            raise ConfigError(f"Could not read {override_path}: {exc}") from exc
        override_data = _load_yaml_mapping(override_text, override_path)
        # Shallow, key-level override: any field present locally replaces
        # the checked-in value wholesale (a list like `devices` is swapped
        # entirely, not merged element-by-element) -- simple and predictable,
        # and matches the actual use case of "this one field is different on
        # my machine," not partial list editing.
        data = {**data, **override_data}

    config = _build_config(data, path)
    config.locked_apt_packages = _read_lock(path, config.ros_distro)
    return config


def resolve_config(start: Path | None = None) -> RosmanConfig:
    """Find and load the rosman config for the workspace containing `start`."""
    path = find_config_file(start)
    if path is None:
        raise ConfigError(
            "No rosman.yml found in this directory or any parent directory.\n"
            "Run `rosman init` to create one."
        )
    return load_config(path)
