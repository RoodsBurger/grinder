#!/usr/bin/env python3
"""
Simple one-time network setup script.
Runs once at boot to ensure WiFi is connected, then exits.
"""

import subprocess
import time
import sys

# --- CONFIGURATION ---
NETWORK_CONFIG = {
    "primary": {
        "ssid": "Cookie Face",
        "password": "huskydaisy483",
    },
    "fallback": {
        "ssid": "Welcome to Hell",
        "password": "ric19077",
    }
}

GATEWAY = "192.168.86.1"
MAX_WAIT = 60  # Maximum seconds to wait for connection

def log(msg):
    print(f"[wifi-setup] {msg}")
    sys.stdout.flush()

def run_command(cmd, timeout=30):
    """Run a command and return (success, stdout, stderr)"""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return result.returncode == 0, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return False, "", "Command timed out"
    except Exception as e:
        return False, "", str(e)

def get_wifi_status():
    """Check if connected to WiFi. Returns (is_connected, ssid, ip_address)"""
    # Check for IP address
    success, stdout, _ = run_command(['ip', '-4', 'addr', 'show', 'wlan0'])
    if not success:
        return False, None, None

    ip_address = None
    for line in stdout.split('\n'):
        if 'inet ' in line:
            ip_address = line.strip().split()[1].split('/')[0]
            break

    if not ip_address:
        return False, None, None

    # Get connected SSID
    success, stdout, _ = run_command(['nmcli', '-t', '-f', 'ACTIVE,SSID', 'dev', 'wifi'])
    ssid = None
    if success:
        for line in stdout.split('\n'):
            if line.startswith('yes:'):
                ssid = line.split(':', 1)[1]
                break

    return True, ssid, ip_address

def ping_gateway():
    """Ping gateway to verify connectivity"""
    success, _, _ = run_command(['ping', '-c', '1', '-W', '3', GATEWAY], timeout=5)
    return success

def connect_to_wifi(ssid, password):
    """Connect to specific WiFi network"""
    log(f"Attempting to connect to '{ssid}'...")

    # Check if connection profile exists
    success, stdout, _ = run_command(['nmcli', '-t', '-f', 'NAME', 'connection', 'show'])
    connection_exists = ssid in stdout if success else False

    if connection_exists:
        success, _, stderr = run_command(['nmcli', 'connection', 'up', ssid], timeout=30)
    else:
        success, _, stderr = run_command(
            ['nmcli', 'device', 'wifi', 'connect', ssid, 'password', password],
            timeout=30
        )

    if success:
        log(f"Connected to '{ssid}'")
        time.sleep(3)
    else:
        log(f"Failed to connect to '{ssid}': {stderr}")

    return success

def main():
    log("=" * 40)
    log("WiFi Setup Starting (one-time)")
    log("=" * 40)

    start_time = time.time()

    # First, wait a bit for system to settle
    time.sleep(5)

    # Check if already connected
    is_connected, ssid, ip = get_wifi_status()
    if is_connected and ping_gateway():
        log(f"Already connected to '{ssid}' ({ip})")
        log("Setup complete!")
        return 0

    # Try to connect
    attempts = 0
    while time.time() - start_time < MAX_WAIT:
        attempts += 1
        log(f"Connection attempt {attempts}...")

        # Try primary network
        if connect_to_wifi(NETWORK_CONFIG['primary']['ssid'],
                          NETWORK_CONFIG['primary']['password']):
            time.sleep(5)
            is_connected, ssid, ip = get_wifi_status()
            if is_connected and ping_gateway():
                log(f"SUCCESS: Connected to '{ssid}' ({ip})")
                log("Setup complete!")
                return 0

        # Try fallback network
        if connect_to_wifi(NETWORK_CONFIG['fallback']['ssid'],
                          NETWORK_CONFIG['fallback']['password']):
            time.sleep(5)
            is_connected, ssid, ip = get_wifi_status()
            if is_connected and ping_gateway():
                log(f"SUCCESS: Connected to '{ssid}' ({ip})")
                log("Setup complete!")
                return 0

        log("Waiting before retry...")
        time.sleep(10)

    # Timeout
    log("ERROR: Could not establish connection within timeout")
    log("Continuing anyway - motor control will start without network")
    return 1

if __name__ == "__main__":
    sys.exit(main())