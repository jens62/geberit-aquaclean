#!/usr/bin/env bash
# update.sh — upgrade aquaclean-bridge while preserving config.ini
#
# Usage:
#   bash operation_support/update.sh [version]
#
# Or without cloning the repo:
#   curl -fsSL https://raw.githubusercontent.com/jens62/geberit-aquaclean/main/operation_support/update.sh | bash
#   curl -fsSL https://raw.githubusercontent.com/jens62/geberit-aquaclean/main/operation_support/update.sh | bash -s -- v2.4.63
#
# Omitting the version installs the latest stable release.
#
# What this script does:
#   1. Stops the aquaclean-bridge systemd service (if running)
#   2. Backs up config.ini from inside the venv package
#   3. Upgrades the package via pip
#   4. Restores config.ini from the backup
#   5. Restarts the service (if it was running before)

set -euo pipefail

VENV="${HOME}/venv"
VERSION="${1:-latest}"

if [ "$VERSION" = "latest" ]; then
    echo "==> Resolving latest release..."
    VERSION=$(curl -fsSL "https://api.github.com/repos/jens62/geberit-aquaclean/releases/latest" \
              | grep '"tag_name"' | head -1 | cut -d'"' -f4)
    echo "==> Latest release: ${VERSION}"
fi

if [ ! -f "${VENV}/bin/aquaclean-bridge" ]; then
    echo "ERROR: aquaclean-bridge not found at ${VENV}/bin/aquaclean-bridge"
    echo "       Run install.sh first:"
    echo "         curl -fsSL https://raw.githubusercontent.com/jens62/geberit-aquaclean/main/operation_support/install.sh | bash -s -- latest"
    exit 1
fi

# Locate config.ini inside the installed package
CONFIG=$("${VENV}/bin/python3" -c \
    "import os, aquaclean_console_app; print(os.path.join(os.path.dirname(aquaclean_console_app.__file__), 'config.ini'))")

BACKUP=$(mktemp /tmp/aquaclean-config.ini.XXXXXX)

echo "==> Backing up config.ini..."
echo "    Source:  ${CONFIG}"
echo "    Backup:  ${BACKUP}"
cp "$CONFIG" "$BACKUP"

# Stop service if active (ignore errors if not installed/running)
SERVICE_WAS_ACTIVE=0
if systemctl is-active --quiet aquaclean-bridge 2>/dev/null; then
    echo "==> Stopping aquaclean-bridge service..."
    sudo systemctl stop aquaclean-bridge
    SERVICE_WAS_ACTIVE=1
fi

echo "==> Upgrading aquaclean-bridge to ${VERSION}..."
"${VENV}/bin/pip" install --quiet --force-reinstall \
    "git+https://github.com/jens62/geberit-aquaclean.git@${VERSION}"

echo "==> Restoring config.ini..."
cp "$BACKUP" "$CONFIG"
rm -f "$BACKUP"

echo ""
echo "==> Installed version:"
"${VENV}/bin/aquaclean-bridge" --version

if [ "$SERVICE_WAS_ACTIVE" = "1" ]; then
    echo "==> Restarting aquaclean-bridge service..."
    sudo systemctl start aquaclean-bridge
    echo "==> Service status:"
    systemctl status aquaclean-bridge --no-pager -l | head -10
fi

echo ""
echo "==> Update complete. config.ini preserved."
