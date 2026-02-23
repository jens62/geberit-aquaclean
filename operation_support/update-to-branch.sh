#!/bin/bash
# Update the pip-installed aquaclean-bridge to a specific branch or release tag,
# preserving the existing config.ini.
#
# Usage (with venv active):
#   bash update-to-branch.sh                        # installs main
#   bash update-to-branch.sh feature/my-branch      # installs a specific branch
#   bash update-to-branch.sh v2.4.3                 # installs a specific release tag
#
# Usage (without activating venv):
#   PYTHON=/home/kali/venv/bin/python3 PIP=/home/kali/venv/bin/pip bash update-to-branch.sh v2.4.3

set -e

# Auto-detect a python3 that has aquaclean_console_app installed
if [ -z "$PYTHON" ]; then
    for candidate in \
        "/home/$USER/venv/bin/python3" \
        "/home/kali/venv/bin/python3" \
        "/home/jens/venv/bin/python3" \
        "/opt/venv/bin/python3" \
        "$(which aquaclean-bridge 2>/dev/null | xargs -I{} dirname {} 2>/dev/null)/python3" \
        "python3"; do
        [ -z "$candidate" ] && continue
        if "$candidate" -c "import aquaclean_console_app" 2>/dev/null; then
            PYTHON="$candidate"
            echo "Auto-detected Python: $PYTHON"
            break
        fi
    done
fi
PYTHON="${PYTHON:-python3}"
PIP="${PIP:-$(dirname "$PYTHON")/pip}"
REF="${1:-main}"
REPO="https://github.com/jens62/geberit-aquaclean.git"
BACKUP="/tmp/aquaclean_config.ini.bak"

# Locate the installed config.ini
CONFIG=$($PYTHON -c "import os, aquaclean_console_app; print(os.path.join(os.path.dirname(aquaclean_console_app.__file__), 'config.ini'))")
echo "Config found at: $CONFIG"

# Back it up
cp "$CONFIG" "$BACKUP"
echo "Config backed up to: $BACKUP"

# Reinstall from branch or tag
echo "Installing from: $REF ..."
$PIP install --force-reinstall "git+${REPO}@${REF}"

# Restore config
cp "$BACKUP" "$CONFIG"
echo "Config restored."

echo ""
echo "Done. Installed version:"
"$(dirname "$PYTHON")/aquaclean-bridge" --version
echo ""
echo "Restart the service to apply:"
echo "  sudo systemctl restart aquaclean-bridge"
