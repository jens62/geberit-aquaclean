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
#   2. Substitutes YOUR_USER and /path/to/venv placeholders
#   3. Installs the service to /etc/systemd/system/
#   4. Installs the logrotate config to /etc/logrotate.d/
#   5. Enables and (re)starts the service

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

echo "==> User:       ${USER_NAME}"
echo "==> Venv:       ${VENV}"
echo "==> Service:    ${SERVICE_DEST}"
echo "==> Logrotate:  ${LOGROTATE_DEST}"
echo ""

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

# --- Stop existing service (if running) ------------------------------------------

if systemctl is-active --quiet aquaclean-bridge 2>/dev/null; then
    echo "==> Stopping existing aquaclean-bridge service..."
    sudo systemctl stop aquaclean-bridge
fi

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

echo "==> Starting service..."
if sudo systemctl start aquaclean-bridge; then
    echo ""
    echo "==> Service started successfully."
else
    echo ""
    echo "==> Service did not start (exit code $?)."
    echo "    This is normal if config.ini has not been configured yet."
    echo "    Edit config.ini, then run: sudo systemctl start aquaclean-bridge"
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
