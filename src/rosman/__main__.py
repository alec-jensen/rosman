"""Lightweight process entry point.

Keep the two latency-sensitive paths out of ``rosman.cli``: importing the
full CLI also imports Rich and the normal-command dispatch machinery. A
version query needs none of those, while the hidden completion path only
needs config resolution and its Docker CLI relay.
"""

from __future__ import annotations

import sys


def _complete(words: list[str]) -> int:
    try:
        from rosman.completion import complete
        from rosman.config import resolve_config

        config = resolve_config()
        for candidate in complete(config, words):
            print(candidate)
    except Exception:
        # Completion is best-effort and runs in the user's interactive
        # shell. A missing config/container/Docker daemon means no
        # candidates, never an error message or a broken prompt.
        pass
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv

    if argv == ["--version"]:
        from rosman import __version__

        print(f"rosman {__version__}")
        return 0

    if argv and argv[0] == "__complete":
        return _complete(argv[1:])
    if len(argv) == 2 and argv[0] == "__check_update":
        from pathlib import Path

        from rosman.update_check import run_scheduled_update_check

        run_scheduled_update_check(Path(argv[1]))
        return 0

    if argv and not argv[0].startswith("-") and argv[:2] != ["rosdep", "install"]:
        from rosman.dispatch import RESERVED_COMMANDS

        if argv[0] not in RESERVED_COMMANDS:
            from rosman.fast_dispatch import try_fast_passthrough

            result = try_fast_passthrough(argv)
            if result is not None:
                return result

    from rosman.cli import main as cli_main

    return cli_main(argv)

if __name__ == "__main__":
    raise SystemExit(main())
