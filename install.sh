#!/bin/bash
#
# Simple Install Script for Motor Control + WiFi Setup
# Run as root: sudo bash install.sh
#

set -e

echo "=========================================="
echo "Motor Control + WiFi Setup Installer"
echo "=========================================="

if [ "$EUID" -ne 0 ]; then
    echo "ERROR: Please run as root (sudo bash install.sh)"
    exit 1
fi

INSTALL_DIR="/opt/motor-control"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo ""
echo "[1/6] Stopping old services..."
systemctl stop grinder.service 2>/dev/null || true
systemctl disable grinder.service 2>/dev/null || true
systemctl stop motor-control.service 2>/dev/null || true
systemctl stop wifi-setup.service 2>/dev/null || true

echo ""
echo "[2/6] Creating install directory..."
mkdir -p "$INSTALL_DIR"

echo ""
echo "[3/6] Copying files..."
cp "$SCRIPT_DIR/motor_control.py" "$INSTALL_DIR/"
cp "$SCRIPT_DIR/wifi_setup.py" "$INSTALL_DIR/"

# Copy driver files from home directory
for f in lcd_display.py touch_screen.py pololu_lib.py; do
    if [ -f "$SCRIPT_DIR/$f" ]; then
        cp "$SCRIPT_DIR/$f" "$INSTALL_DIR/"
    elif [ -f "/home/step/$f" ]; then
        cp "/home/step/$f" "$INSTALL_DIR/"
    else
        echo "WARNING: $f not found"
    fi
done

chmod +x "$INSTALL_DIR"/*.py

echo ""
echo "[4/6] Installing services..."
cp "$SCRIPT_DIR/wifi-setup.service" /etc/systemd/system/
cp "$SCRIPT_DIR/motor-control.service" /etc/systemd/system/

echo ""
echo "[5/6] Enabling services..."
systemctl daemon-reload
systemctl enable wifi-setup.service
systemctl enable motor-control.service

echo ""
echo "[6/6] Starting services..."
systemctl start wifi-setup.service
systemctl start motor-control.service

echo ""
echo "=========================================="
echo "Installation Complete!"
echo "=========================================="
echo ""
echo "Commands:"
echo "  View logs:  journalctl -u motor-control -f"
echo "  Restart:    sudo systemctl restart motor-control"
echo "  Status:     systemctl status motor-control"
echo ""