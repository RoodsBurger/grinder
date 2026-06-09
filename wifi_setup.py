#!/usr/bin/env python3
"""
Simple one-time network setup script.
Runs once at boot to ensure WiFi is connected, then exits.
"""

import json
import os
import subprocess
import time
import sys

# --- CONFIGURATION ---
# Credentials live in wifi_config.json next to this script (untracked, see
# .gitignore) so they never land in the repo. Format:
#   {"primary": {"ssid": "...", "password": "..."},
#    "fallback": {"ssid": "...", "password": "..."}}
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wifi_config.json")

MAX_WAIT = 60  # Maximum seconds to wait for connection


def load_network_config():
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except FileNotFoundError:
        log(f"WARNING: {CONFIG_PATH} not found — cannot connect to new networks")
        return None
    except Exception as e:
        log(f"WARNING: bad wifi_config.json ({e})")
        return None

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
    """Ping the default gateway (from the routing table) to verify connectivity"""
    success, stdout, _ = run_command(['ip', 'route', 'show', 'default'])
    if not success:
        return False
    parts = stdout.split()
    if 'via' not in parts:
        return False
    gateway = parts[parts.index('via') + 1]
    success, _, _ = run_command(['ping', '-c', '1', '-W', '3', gateway], timeout=5)
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
    network_config = load_network_config()

    # First, wait a bit for system to settle
    time.sleep(5)

    # Check if already connected
    is_connected, ssid, ip = get_wifi_status()
    if is_connected and ping_gateway():
        log(f"Already connected to '{ssid}' ({ip})")
        log("Setup complete!")
        return 0

    if network_config is None:
        log("No network credentials available — relying on NetworkManager saved profiles")
        log("Continuing anyway - motor control will start without network")
        return 1

    # Try to connect
    attempts = 0
    while time.time() - start_time < MAX_WAIT:
        attempts += 1
        log(f"Connection attempt {attempts}...")

        # Try primary network
        if connect_to_wifi(network_config['primary']['ssid'],
                          network_config['primary']['password']):
            time.sleep(5)
            is_connected, ssid, ip = get_wifi_status()
            if is_connected and ping_gateway():
                log(f"SUCCESS: Connected to '{ssid}' ({ip})")
                log("Setup complete!")
                return 0

        # Try fallback network
        if connect_to_wifi(network_config['fallback']['ssid'],
                          network_config['fallback']['password']):
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