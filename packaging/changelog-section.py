#!/usr/bin/env python3
"""Extracts one version's section from CHANGELOG.md, for use as GitHub
release notes -- so a release's notes are the same curated summary
CHANGELOG.md has, not GitHub's `--generate-notes` (which, for a repo with
no PR-based history, just falls back to a bare compare-link)."""

from __future__ import annotations

import re
import sys
from pathlib import Path

CHANGELOG_PATH = Path(__file__).resolve().parent.parent / "CHANGELOG.md"


def extract(version: str) -> str:
    version = version.lstrip("v")
    text = CHANGELOG_PATH.read_text()
    pattern = re.compile(
        rf"^## v{re.escape(version)}\b.*?$\n(.*?)(?=^## |\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(text)
    if not match:
        raise SystemExit(f"No CHANGELOG.md section found for v{version}")
    return match.group(1).strip()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: changelog-section.py <version>")
    print(extract(sys.argv[1]))
