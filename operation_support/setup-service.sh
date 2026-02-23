#!/usr/bin/env bash
# setup-service.sh — install (or reinstall) the aquaclean-bridge systemd service
#                    and logrotate config.
#
# Run this once after installing aquaclean-bridge.
# Safe to re-run: if the service is already installed it will be updated in place.
#
# Usage:
#   bash operation_support/setup-service.sh
#
# Or without cloning the repo — requires the service files already on the system
# (they are not downloaded by this script alone; use install.sh first).
#
# What it does:
#   1. Substitutes YOUR_USER and /path/to/venv placeholders in aquaclean-bridge.service
#   2. Installs the service to /etc/systemd/system/
#   3. Installs the logrotate config to /etc/logrotate.d/
#   4. Enables and (re)starts the service

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_SRC="${SCRIPT_DIR}/aquaclean-bridge.service"
LOGROTATE_SRC="${SCRIPT_DIR}/aquaclean-bridge.logrotate"
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
    echo "         bash operation_support/install.sh latest"
    echo ""
    echo "       Or set AQUACLEAN_VENV to the correct venv path:"
    echo "         AQUACLEAN_VENV=/opt/venv bash operation_support/setup-service.sh"
    exit 1
fi

USER_NAME="$(whoami)"

echo "==> User:       ${USER_NAME}"
echo "==> Venv:       ${VENV}"
echo "==> Service:    ${SERVICE_DEST}"
echo "==> Logrotate:  ${LOGROTATE_DEST}"
echo ""

# --- Stop existing service (if running) ------------------------------------------

if systemctl is-active --quiet aquaclean-bridge 2>/dev/null; then
    echo "==> Stopping existing aquaclean-bridge service..."
    sudo systemctl stop aquaclean-bridge
fi

# --- Install service unit ---------------------------------------------------------

echo "==> Installing systemd service..."
sed -e "s|YOUR_USER|${USER_NAME}|g" \
    -e "s|/path/to/venv|${VENV}|g" \
    "${SERVICE_SRC}" \
  | sudo tee "${SERVICE_DEST}" > /dev/null

# --- Install logrotate config -----------------------------------------------------

echo "==> Installing logrotate config..."
sudo cp "${LOGROTATE_SRC}" "${LOGROTATE_DEST}"

# --- Enable and start -------------------------------------------------------------

echo "==> Reloading systemd and enabling service..."
sudo systemctl daemon-reload
sudo systemctl enable aquaclean-bridge
sudo systemctl start aquaclean-bridge

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
