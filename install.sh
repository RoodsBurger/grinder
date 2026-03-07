#!/bin/bash
#
# Install Script for Motor Control + WiFi Setup
# Run as root: sudo bash install.sh
#
# Flags:
#   (none)   Full install: deps, copy files, services
#   -simple  Just restart services (no install, no file copy)
#

set -e

if [ "$EUID" -ne 0 ]; then
    echo "ERROR: Please run as root (sudo bash install.sh)"
    exit 1
fi

INSTALL_DIR="/opt/motor-control"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- SIMPLE MODE: copy files and restart services (no dependency install) ---
if [ "$1" == "-simple" ]; then
    echo "=========================================="
    echo "Quick Deploy (copy files + restart)"
    echo "=========================================="

    echo ""
    echo "[1/3] Stopping services..."
    systemctl stop motor-control.service 2>/dev/null || true
    systemctl stop wifi-setup.service 2>/dev/null || true

    echo ""
    echo "[2/3] Copying files..."
    mkdir -p "$INSTALL_DIR"
    cp "$SCRIPT_DIR/motor_control.py" "$INSTALL_DIR/"
    cp "$SCRIPT_DIR/motor_only.py" "$INSTALL_DIR/"
    cp "$SCRIPT_DIR/motor_configs.json" "$INSTALL_DIR/"
    cp "$SCRIPT_DIR/wifi_setup.py" "$INSTALL_DIR/"
    cp "$SCRIPT_DIR/lcd_display.py" "$INSTALL_DIR/"
    cp "$SCRIPT_DIR/touch_screen.py" "$INSTALL_DIR/"
    chmod +x "$INSTALL_DIR"/*.py

    echo ""
    echo "[3/3] Starting services..."
    systemctl start wifi-setup.service
    systemctl start motor-control.service

    echo ""
    echo "Done. View logs: journalctl -u motor-control -f"
    echo ""
    exit 0
fi

# --- FULL INSTALL ---
echo "=========================================="
echo "Motor Control + WiFi Setup Installer"
echo "=========================================="

echo ""
echo "[1/7] Installing Python dependencies..."
apt-get update
apt-get install -y python3-pip python3-pil python3-numpy
pip3 install --break-system-packages spidev smbus2 RPi.GPIO opencv-python-headless

echo ""
echo "[2/7] Stopping old services..."
systemctl stop grinder.service 2>/dev/null || true
systemctl disable grinder.service 2>/dev/null || true
systemctl stop motor-control.service 2>/dev/null || true
systemctl stop wifi-setup.service 2>/dev/null || true

echo ""
echo "[3/7] Creating install directory..."
mkdir -p "$INSTALL_DIR"

echo ""
echo "[4/7] Copying files..."
cp "$SCRIPT_DIR/motor_control.py" "$INSTALL_DIR/"
cp "$SCRIPT_DIR/motor_only.py" "$INSTALL_DIR/"
cp "$SCRIPT_DIR/motor_configs.json" "$INSTALL_DIR/"
cp "$SCRIPT_DIR/wifi_setup.py" "$INSTALL_DIR/"
cp "$SCRIPT_DIR/lcd_display.py" "$INSTALL_DIR/"
cp "$SCRIPT_DIR/touch_screen.py" "$INSTALL_DIR/"

chmod +x "$INSTALL_DIR"/*.py

echo ""
echo "[5/7] Installing services..."
cp "$SCRIPT_DIR/wifi-setup.service" /etc/systemd/system/
cp "$SCRIPT_DIR/motor-control.service" /etc/systemd/system/

echo ""
echo "[6/7] Enabling services..."
systemctl daemon-reload
systemctl enable wifi-setup.service
systemctl enable motor-control.service

echo ""
echo "[7/7] Starting services..."
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
