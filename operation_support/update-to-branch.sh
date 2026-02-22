#!/bin/bash
# Update the pip-installed aquaclean-bridge to a specific branch,
# preserving the existing config.ini.
#
# Usage (with venv active):
#   bash update-to-branch.sh
#
# Usage (without activating venv):
#   PYTHON=/home/jens/venv/bin/python3 PIP=/home/jens/venv/bin/pip bash update-to-branch.sh

set -e

PYTHON="${PYTHON:-python3}"
PIP="${PIP:-pip}"
BRANCH="feature/new-ble-commands"
REPO="https://github.com/jens62/geberit-aquaclean.git"
BACKUP="/tmp/aquaclean_config.ini.bak"

# Locate the installed config.ini
CONFIG=$($PYTHON -c "import os, aquaclean_console_app; print(os.path.join(os.path.dirname(aquaclean_console_app.__file__), 'config.ini'))")
echo "Config found at: $CONFIG"

# Back it up
cp "$CONFIG" "$BACKUP"
echo "Config backed up to: $BACKUP"

# Reinstall from branch
echo "Installing from branch: $BRANCH ..."
$PIP install --force-reinstall "git+${REPO}@${BRANCH}"

# Restore config
cp "$BACKUP" "$CONFIG"
echo "Config restored."

echo ""
echo "Done. Restart the service to apply:"
echo "  sudo systemctl restart aquaclean-bridge"
