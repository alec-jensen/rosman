#!/bin/bash
# Builds a standalone, self-contained rosman binary for Linux x86_64.
#
# Deliberately built inside an ubuntu:22.04 container -- not on whatever
# glibc the build host happens to have -- because a PyInstaller binary is
# dynamically linked against glibc, and glibc is forward- but not
# backward-compatible: a binary built on a newer glibc won't run on an
# older one. Ubuntu 22.04's glibc (2.35) matches the baseline ROS 2
# Humble/Jazzy already target, so it's the right compatibility floor for
# this specific tool. Confirmed working on Ubuntu 22.04, Debian 12
# (bookworm), Fedora, and Arch Linux (all newer-or-equal glibc).
#
# Usage: packaging/build-binary.sh <output-path>
set -euo pipefail

OUT="${1:?usage: build-binary.sh <output-path>}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="$(cd "$(dirname "$OUT")" && pwd)"

docker run --rm \
    -v "$REPO_ROOT:/src:ro" \
    -v "$OUT_DIR:/out" \
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
/venv/bin/pyinstaller --onefile --name rosman \
    --collect-all rich --collect-all docker \
    /tmp/rosman-src/rosman/__main__.py
cp /tmp/dist/rosman /out/rosman-binary
'

mv "$OUT_DIR/rosman-binary" "$OUT"
echo "Built $OUT"
