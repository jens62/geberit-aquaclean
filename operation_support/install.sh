#!/usr/bin/env bash
# install.sh — set up or upgrade aquaclean-bridge in ~/venv
#
# Usage:
#   ./scripts/install.sh <version>
#
# Examples:
#   ./scripts/install.sh v2.4.3        # install a specific release tag
#   ./scripts/install.sh main          # install latest from main branch
#
# If ~/venv already exists, the apt/venv creation steps are skipped.
# Re-running the script with a new version upgrades the package in-place.

set -euo pipefail

VENV="${HOME}/venv"
VERSION="${1:-}"

if [ -z "$VERSION" ]; then
    echo "Usage: $0 <version>"
    echo "  Example: $0 v2.4.3"
    echo "  Example: $0 main"
    exit 1
fi

if [ ! -d "$VENV" ]; then
    echo "==> Installing system dependencies..."
    sudo apt update
    sudo apt install -y python3-venv python3-pip

    echo "==> Creating virtual environment at ${VENV}..."
    python3 -m venv "$VENV"
else
    echo "==> Virtual environment already exists at ${VENV}, skipping apt and venv creation."
fi

echo "==> Upgrading pip, setuptools, wheel..."
"${VENV}/bin/pip" install --upgrade pip setuptools wheel

echo "==> Installing aquaclean-bridge @ ${VERSION}..."
"${VENV}/bin/pip" install --force-reinstall \
    "git+https://github.com/jens62/geberit-aquaclean.git@${VERSION}"

echo "==> Installed version:"
"${VENV}/bin/aquaclean-bridge" --version
