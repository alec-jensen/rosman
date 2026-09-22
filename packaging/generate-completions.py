#!/usr/bin/env python3
"""Generate package-manager shell completion files from rosman's source.

Keeping these generated means `rosman completion bash/zsh` and the files
installed by apt/dnf/pacman cannot quietly drift apart.
"""

from __future__ import annotations

import sys
from pathlib import Path

from rosman.completion import BASH_SCRIPT, PACKAGED_ZSH_SCRIPT


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: generate-completions.py <output-directory>")
    output = Path(sys.argv[1])
    output.mkdir(parents=True, exist_ok=True)
    (output / "rosman.bash").write_text(BASH_SCRIPT)
    (output / "_rosman").write_text(PACKAGED_ZSH_SCRIPT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
