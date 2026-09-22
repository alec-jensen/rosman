#!/bin/bash
# Builds a PyInstaller --onedir bundle for apt/dnf/pacman packages and the
# standalone GitHub release archive. Avoids the onefile build's extraction
# cost on every ROS command and completion request.
#
# Confirmed the resulting bundle still resolves its sibling `_internal/`
# directory correctly when invoked through a symlink from a different
# directory (exactly how nfpm.yaml installs it: the bundle lives under
# /usr/lib/rosman/, with /usr/bin/rosman symlinked to the executable
# inside it) -- PyInstaller's bootloader resolves argv[0] to its real
# path before looking for _internal, so the symlink doesn't break it.
#
# Usage: packaging/build-onedir.sh <output-dir>
set -euo pipefail

OUT_DIR="${1:?usage: build-onedir.sh <output-dir>}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_PARENT="$(cd "$(dirname "$OUT_DIR")" && pwd)"
OUT_NAME="$(basename "$OUT_DIR")"
if [[ -z "$OUT_NAME" || "$OUT_NAME" == "." || "$OUT_NAME" == ".." || "$OUT_NAME" == "/" ]]; then
    echo "Refusing unsafe output directory: $OUT_DIR" >&2
    exit 2
fi
OUT_DIR="$OUT_PARENT/$OUT_NAME"
OUT_UID="$(id -u)"
OUT_GID="$(id -g)"

docker run --rm \
    -v "$REPO_ROOT:/src:ro" \
    -v "$OUT_PARENT:/out-parent" \
    -e OUT_NAME="$OUT_NAME" -e OUT_UID="$OUT_UID" -e OUT_GID="$OUT_GID" \
    ubuntu:22.04 bash -c '
set -euo pipefail
apt-get update -qq
apt-get install -y -qq --no-install-recommends \
    python3.10 python3.10-venv python3.10-dev python3-pip binutils > /dev/null
python3.10 -m venv /venv
/venv/bin/pip install -q --upgrade pip
/venv/bin/pip install -q /src pyinstaller
cp -r /src/src /tmp/rosman-src
cd /tmp
/venv/bin/pyinstaller --onedir --name rosman \
    --collect-all rich --collect-all docker \
    /tmp/rosman-src/rosman/__main__.py
rm -rf -- "/out-parent/$OUT_NAME"
cp -r /tmp/dist/rosman "/out-parent/$OUT_NAME"
chown -R "$OUT_UID:$OUT_GID" "/out-parent/$OUT_NAME"
'

echo "Built onedir bundle in $OUT_DIR"
