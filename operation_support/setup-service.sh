#!/usr/bin/env bash
# setup-service.sh — install (or reinstall) the aquaclean-bridge systemd service
#                    and logrotate config.
#
# Run this once after installing aquaclean-bridge.
# Safe to re-run: if the service is already installed it will be updated in place.
#
# Usage (repo cloned):
#   bash operation_support/setup-service.sh
#
# Usage (no clone needed):
#   curl -fsSL https://raw.githubusercontent.com/jens62/geberit-aquaclean/main/operation_support/setup-service.sh | bash
#
# What it does:
#   1. Fetches aquaclean-bridge.service and aquaclean-bridge.logrotate from the
#      repo (or uses local copies if the repo is already cloned)
#   2. Creates /var/log/aquaclean with correct ownership
#   3. Substitutes YOUR_USER and /path/to/venv placeholders
#   4. Installs the service to /etc/systemd/system/
#   5. Installs the logrotate config to /etc/logrotate.d/
#   6. Enables and (re)starts the service

set -euo pipefail

REPO_RAW="https://raw.githubusercontent.com/jens62/geberit-aquaclean/main/operation_support"
SERVICE_DEST="/etc/systemd/system/aquaclean-bridge.service"
LOGROTATE_DEST="/etc/logrotate.d/aquaclean-bridge"

# --- Resolve venv path -----------------------------------------------------------

VENV="${HOME}/venv"

# Allow override via environment variable
if [ -n "${AQUACLEAN_VENV:-}" ]; then
    VENV="$AQUACLEAN_VENV"
fi

if [ ! -f "${VENV}/bin/aquaclean-bridge" ]; then
    echo "ERROR: aquaclean-bridge not found at ${VENV}/bin/aquaclean-bridge"
    echo "       Install it first with:"
    echo "         curl -fsSL ${REPO_RAW}/install.sh | bash -s -- latest"
    echo ""
    echo "       Or set AQUACLEAN_VENV to the correct venv path:"
    echo "         AQUACLEAN_VENV=/opt/venv bash setup-service.sh"
    exit 1
fi

USER_NAME="$(whoami)"

# --- Check config.ini ------------------------------------------------------------

CONFIG_PATH=$("${VENV}/bin/python3" -c \
    "import os, aquaclean_console_app; print(os.path.join(os.path.dirname(aquaclean_console_app.__file__), 'config.ini'))" \
    2>/dev/null || echo "")

CONFIG_DEVICE_ID=""
CONFIG_MQTT_SERVER=""
if [ -n "$CONFIG_PATH" ] && [ -f "$CONFIG_PATH" ]; then
    CONFIG_DEVICE_ID=$(grep -i '^\s*device_id\s*=' "$CONFIG_PATH" | head -1 | cut -d= -f2 | tr -d ' ' || echo "")
    CONFIG_MQTT_SERVER=$(grep -i '^\s*server\s*=' "$CONFIG_PATH" | head -1 | cut -d= -f2 | tr -d ' ' || echo "")
fi

echo "==> User:         ${USER_NAME}"
echo "==> Venv:         ${VENV}"
echo "==> Service:      ${SERVICE_DEST}"
echo "==> Logrotate:    ${LOGROTATE_DEST}"
echo "==> config.ini:   ${CONFIG_PATH:-not found}"
echo "==> BLE device:   ${CONFIG_DEVICE_ID:-NOT SET}"
echo "==> MQTT server:  ${CONFIG_MQTT_SERVER:-NOT SET}"
echo ""

if [ -z "$CONFIG_DEVICE_ID" ] || [ -z "$CONFIG_MQTT_SERVER" ]; then
    echo "WARNING: config.ini is not fully configured."
    echo "         The service will be installed but may not start until you set:"
    if [ -z "$CONFIG_DEVICE_ID" ]; then
        echo "           [BLE]  device_id = XX:XX:XX:XX:XX:XX"
    fi
    if [ -z "$CONFIG_MQTT_SERVER" ]; then
        echo "           [MQTT] server    = 192.168.x.x"
    fi
    echo "         Then run: sudo systemctl start aquaclean-bridge"
    echo ""
fi

# --- Locate or download template files -------------------------------------------

# When run via curl|bash, BASH_SOURCE[0] is /dev/stdin — local files won't exist.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-/dev/stdin}")" 2>/dev/null && pwd || echo "")"
SERVICE_SRC="${SCRIPT_DIR}/aquaclean-bridge.service"
LOGROTATE_SRC="${SCRIPT_DIR}/aquaclean-bridge.logrotate"

TMPDIR_USED=""
if [ ! -f "$SERVICE_SRC" ] || [ ! -f "$LOGROTATE_SRC" ]; then
    echo "==> Downloading service files from GitHub..."
    TMPDIR_USED="$(mktemp -d)"
    curl -fsSL "${REPO_RAW}/aquaclean-bridge.service"  -o "${TMPDIR_USED}/aquaclean-bridge.service"
    curl -fsSL "${REPO_RAW}/aquaclean-bridge.logrotate" -o "${TMPDIR_USED}/aquaclean-bridge.logrotate"
    SERVICE_SRC="${TMPDIR_USED}/aquaclean-bridge.service"
    LOGROTATE_SRC="${TMPDIR_USED}/aquaclean-bridge.logrotate"
fi

cleanup() { [ -n "$TMPDIR_USED" ] && rm -rf "$TMPDIR_USED"; }
trap cleanup EXIT

# --- Create log directory --------------------------------------------------------

echo "==> Creating log directory /var/log/aquaclean..."
sudo mkdir -p /var/log/aquaclean
sudo chown "${USER_NAME}" /var/log/aquaclean

# --- Stop and reset existing service ---------------------------------------------
# Stop unconditionally — handles both running and restart-loop (failed) states.

echo "==> Stopping existing service (if any)..."
sudo systemctl stop aquaclean-bridge 2>/dev/null || true
sudo systemctl reset-failed aquaclean-bridge 2>/dev/null || true

# --- Install service unit --------------------------------------------------------

echo "==> Installing systemd service..."
sed -e "s|YOUR_USER|${USER_NAME}|g" \
    -e "s|/path/to/venv|${VENV}|g" \
    "${SERVICE_SRC}" \
  | sudo tee "${SERVICE_DEST}" > /dev/null

# --- Install logrotate config ----------------------------------------------------

echo "==> Installing logrotate config..."
sudo cp "${LOGROTATE_SRC}" "${LOGROTATE_DEST}"

# --- Enable and start ------------------------------------------------------------

echo "==> Reloading systemd and enabling service..."
sudo systemctl daemon-reload
sudo systemctl enable aquaclean-bridge

if [ -n "$CONFIG_DEVICE_ID" ] && [ -n "$CONFIG_MQTT_SERVER" ]; then
    echo "==> Starting service..."
    if sudo systemctl start aquaclean-bridge; then
        echo ""
        echo "==> Service started successfully."
    else
        echo ""
        echo "==> Service failed to start. Check the log:"
        echo "      journalctl -xeu aquaclean-bridge.service"
    fi
else
    echo "==> Skipping start — configure config.ini first (see WARNING above)."
fi

echo ""
echo "==> Service status:"
sudo systemctl status aquaclean-bridge --no-pager -l || true

echo ""
echo "==> Done."
echo ""
echo "   Useful commands:"
echo "     sudo systemctl status  aquaclean-bridge"
echo "     sudo systemctl restart aquaclean-bridge"
echo "     sudo systemctl stop    aquaclean-bridge"
echo "     tail -f /var/log/aquaclean/aquaclean.log"
