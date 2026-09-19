#!/usr/bin/env python3
"""Generate the apt dists/<suite>/Release file (with MD5Sum/SHA1/SHA256
checksums of the Packages indices) for the rosman apt repo. Run from the
repo root (the directory containing `dists/` and `pool/`).
"""

from __future__ import annotations

import hashlib
import sys
from datetime import datetime, timezone
from pathlib import Path

SUITE = "stable"
ARCH = "amd64"


def checksums(path: Path) -> tuple[str, str, str, int]:
    data = path.read_bytes()
    return (
        hashlib.md5(data).hexdigest(),
        hashlib.sha1(data).hexdigest(),
        hashlib.sha256(data).hexdigest(),
        len(data),
    )


def main() -> None:
    repo_root = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    suite_dir = repo_root / "dists" / SUITE
    indices = [
        f"main/binary-{ARCH}/Packages",
        f"main/binary-{ARCH}/Packages.gz",
    ]

    md5_lines, sha1_lines, sha256_lines = [], [], []
    for rel in indices:
        md5, sha1, sha256, size = checksums(suite_dir / rel)
        md5_lines.append(f" {md5} {size} {rel}")
        sha1_lines.append(f" {sha1} {size} {rel}")
        sha256_lines.append(f" {sha256} {size} {rel}")

    date = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S UTC")
    release = f"""Origin: rosman
Label: rosman
Suite: {SUITE}
Codename: {SUITE}
Architectures: {ARCH}
Components: main
Description: rosman package repository (https://github.com/alec-jensen/rosman)
Date: {date}
MD5Sum:
{chr(10).join(md5_lines)}
SHA1:
{chr(10).join(sha1_lines)}
SHA256:
{chr(10).join(sha256_lines)}
"""
    (suite_dir / "Release").write_text(release)
    print(f"Wrote {suite_dir / 'Release'}")


if __name__ == "__main__":
    main()
