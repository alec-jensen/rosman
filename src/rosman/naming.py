"""Deterministic naming for the Docker objects rosman creates.

Names are derived from the absolute workspace path so the same workspace
always maps to the same container/image/volume names across invocations,
without needing a separate name registry.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(text: str) -> str:
    slug = _SLUG_RE.sub("-", text.lower()).strip("-")
    return slug or "workspace"


def workspace_hash(workspace_root: Path, length: int = 8) -> str:
    digest = hashlib.sha256(str(workspace_root.resolve()).encode("utf-8")).hexdigest()
    return digest[:length]


def container_name(workspace_root: Path) -> str:
    project = _slug(workspace_root.name)
    return f"rosman-{project}-{workspace_hash(workspace_root)}"


def image_name(workspace_root: Path, ros_distro: str, config_hash: str) -> str:
    return f"rosman/{ros_distro}-{workspace_hash(workspace_root)}:{config_hash}"


def network_name(group: str) -> str:
    return f"rosman-net-{_slug(group)}"


def volume_name(workspace_root: Path, ros_distro: str, kind: str) -> str:
    """kind is one of 'build', 'install', 'log'."""
    return f"rosman-vol-{workspace_hash(workspace_root)}-{ros_distro}-{kind}"
