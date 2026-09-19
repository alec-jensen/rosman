"""Local, per-machine rosman state: domain ID assignments and anything else
that must persist across runs but must *not* live in rosman.yml (which is
typically checked into version control and shared across machines/users).

Stored as a single JSON file under the XDG state directory, keyed by the
absolute path of each project's workspace root. A simple "lowest free ID in
range, tracked in one file" strategy is the whole collision-avoidance
strategy for `domain_id: auto` — see spec §11, which flags this as the
approach to try first before anything fancier.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MIN_DOMAIN_ID = 0
MAX_DOMAIN_ID = 232


def state_dir() -> Path:
    xdg_state_home = os.environ.get("XDG_STATE_HOME")
    if xdg_state_home:
        return Path(xdg_state_home) / "rosman"
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "rosman"
    return Path.home() / ".local" / "state" / "rosman"


def default_state_path() -> Path:
    return state_dir() / "state.json"


@dataclass
class ProjectState:
    domain_id: int | None = None
    container_name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"domain_id": self.domain_id, "container_name": self.container_name}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProjectState:
        return cls(domain_id=data.get("domain_id"), container_name=data.get("container_name"))


@dataclass
class RosmanState:
    path: Path = field(default_factory=default_state_path)
    projects: dict[str, ProjectState] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | None = None) -> RosmanState:
        path = path or default_state_path()
        if not path.is_file():
            return cls(path=path)
        try:
            raw = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            # Corrupt state file: don't crash rosman over it, start fresh.
            return cls(path=path)
        projects = {
            key: ProjectState.from_dict(value) for key, value in raw.get("projects", {}).items()
        }
        return cls(path=path, projects=projects)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"projects": {key: p.to_dict() for key, p in self.projects.items()}}
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        tmp.replace(self.path)

    def _key(self, workspace_root: Path) -> str:
        return str(workspace_root.resolve())

    def get_project(self, workspace_root: Path) -> ProjectState:
        return self.projects.setdefault(self._key(workspace_root), ProjectState())

    def used_domain_ids(self, exclude: Path | None = None) -> set[int]:
        exclude_key = self._key(exclude) if exclude else None
        return {
            p.domain_id
            for key, p in self.projects.items()
            if p.domain_id is not None and key != exclude_key
        }

    def assign_domain_id(self, workspace_root: Path) -> int:
        """Return the persisted domain ID for this workspace, assigning the
        lowest unused ID in [MIN_DOMAIN_ID, MAX_DOMAIN_ID] on first call."""
        project = self.get_project(workspace_root)
        if project.domain_id is not None:
            return project.domain_id

        used = self.used_domain_ids(exclude=workspace_root)
        for candidate in range(MIN_DOMAIN_ID, MAX_DOMAIN_ID + 1):
            if candidate not in used:
                project.domain_id = candidate
                self.save()
                return candidate
        raise RuntimeError(
            f"No free ROS_DOMAIN_ID left in [{MIN_DOMAIN_ID}, {MAX_DOMAIN_ID}] — "
            f"{len(used)} projects already have one assigned. Set domain_id explicitly "
            "in rosman.yml instead of using 'auto'."
        )

    def set_container_name(self, workspace_root: Path, name: str) -> None:
        self.get_project(workspace_root).container_name = name
        self.save()
