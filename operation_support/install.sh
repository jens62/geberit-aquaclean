#!/usr/bin/env bash
# install.sh — set up or upgrade aquaclean-bridge in ~/venv
#
# Usage:
#   bash operation_support/install.sh <version>
#
# Or without cloning the repo first:
#   curl -fsSL https://raw.githubusercontent.com/jens62/geberit-aquaclean/main/operation_support/install.sh | bash -s -- <version>
#
# Examples:
#   bash operation_support/install.sh latest    # install the latest release (recommended)
#   bash operation_support/install.sh v2.4.4    # install a specific release tag
#   bash operation_support/install.sh main      # install latest from main branch
#
# If ~/venv already exists, the apt/venv creation steps are skipped.
# Re-running the script with a new version upgrades the package in-place.

set -euo pipefail

VENV="${HOME}/venv"
VERSION="${1:-}"

if [ -z "$VERSION" ]; then
    echo "Usage: $0 <version>"
    echo "  Example: $0 latest"
    echo "  Example: $0 v2.4.4"
    echo "  Example: $0 main"
    exit 1
fi

if [ "$VERSION" = "latest" ]; then
    echo "==> Resolving latest release..."
    VERSION=$(curl -fsSL "https://api.github.com/repos/jens62/geberit-aquaclean/releases/latest" \
              | grep '"tag_name"' | head -1 | cut -d'"' -f4)
    echo "==> Latest release: ${VERSION}"
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

echo ""
echo "==> Installed version:"
"${VENV}/bin/aquaclean-bridge" --version

CONFIG=$("${VENV}/bin/python3" -c "import os, aquaclean_console_app; print(os.path.join(os.path.dirname(aquaclean_console_app.__file__), 'config.ini'))")

echo ""
echo "==> Next steps:"
echo ""
echo "  1. Edit config.ini — set your BLE device address and MQTT broker:"
echo "       ${CONFIG}"
echo ""
echo "     Minimum required settings:"
echo "       [BLE]  device_id = XX:XX:XX:XX:XX:XX   # BLE MAC of your AquaClean"
echo "       [MQTT] server    = 192.168.x.x          # your MQTT broker IP"
echo ""
echo "  2. Start the bridge:"
echo "       ${VENV}/bin/aquaclean-bridge --mode api"
echo ""
echo "  3. Full usage:"
echo "       ${VENV}/bin/aquaclean-bridge --help"
echo ""
echo "  To call aquaclean-bridge without the full path, add to your shell profile"
echo "  (~/.bashrc or ~/.zshrc):"
echo "       export PATH=\"\${HOME}/venv/bin:\$PATH\""
