#!/bin/bash
# Generates rosman's man page directly from its own build_parser() --
# the exact same parser --help/tab-completion already use -- so the man
# page can never drift from the real CLI surface the way a hand-written
# one inevitably would.
#
# Usage: packaging/generate-manpage.sh <version> <output-path.1>
set -euo pipefail

VERSION="${1:?usage: generate-manpage.sh <version> <output-path.1>}"
OUT="${2:?usage: generate-manpage.sh <version> <output-path.1>}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "$REPO_ROOT"
uv run --with argparse-manpage argparse-manpage \
    --module rosman.cli \
    --function build_parser \
    --project-name rosman \
    --prog rosman \
    --version "$VERSION" \
    --description "Run ROS 2 anywhere without installing it natively" \
    --url https://github.com/alec-jensen/rosman \
    --author "Alec Jensen" \
    --format single-commands-section \
    --output "$OUT"

# `__complete` backs shell tab-completion and is never meant to be run
# directly -- argparse.SUPPRESS already hides its own --help text (so it
# gets no man-page section of its own), but it still shows up in the raw
# SYNOPSIS choices list, which isn't affected by help suppression.
sed -i 's/,__complete//' "$OUT"

echo "Generated $OUT"
