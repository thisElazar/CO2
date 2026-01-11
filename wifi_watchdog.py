#!/usr/bin/env python3
# /usr/local/bin/wifi_watchdog.py
# WiFi connectivity watchdog for autonomous Raspberry Pi deployments
# Monitors internet connectivity and forces reconnection when lost

import subprocess
import time
import logging
from datetime import datetime

# Set up logging
logging.basicConfig(
    filename='/var/log/wifi_watchdog.log',
    level=logging.INFO,
    format='%(asctime)s - %(message)s'
)

def check_connectivity():
    """Returns True if we can reach the internet"""
    try:
        result = subprocess.run(
            ['ping', '-c', '1', '-W', '3', '8.8.8.8'],
            capture_output=True,
            timeout=5
        )
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        return False
    except Exception as e:
        logging.error(f"Error checking connectivity: {e}")
        return False

def get_wifi_status():
    """Get current WiFi connection info"""
    try:
        result = subprocess.run(
            ['wpa_cli', '-i', 'wlan0', 'status'],
            capture_output=True,
            text=True,
            timeout=5
        )
        return result.stdout
    except Exception as e:
        logging.error(f"Error getting WiFi status: {e}")
        return ""

def force_reconnect():
    """Force WiFi to disconnect and reconnect"""
    logging.info("Connectivity lost - forcing WiFi reconnect")
    
    try:
        # Log current WiFi status before reconnecting
        status = get_wifi_status()
        logging.info(f"Current WiFi status:\n{status}")
        
        # Force disconnect
        subprocess.run(['sudo', 'wpa_cli', '-i', 'wlan0', 'disconnect'], timeout=5)
        time.sleep(2)
        
        # Trigger scan for available networks
        subprocess.run(['sudo', 'wpa_cli', '-i', 'wlan0', 'scan'], timeout=5)
        time.sleep(3)
        
        # Reconnect to best available network
        subprocess.run(['sudo', 'wpa_cli', '-i', 'wlan0', 'reconnect'], timeout=5)
        
        logging.info("Reconnect command sent")
        
    except Exception as e:
        logging.error(f"Error during reconnect: {e}")

def main():
    logging.info("WiFi watchdog started")
    consecutive_failures = 0
    
    while True:
        try:
            if check_connectivity():
                if consecutive_failures > 0:
                    logging.info(f"Connectivity restored after {consecutive_failures} failures")
                consecutive_failures = 0
            else:
                consecutive_failures += 1
                logging.warning(f"Connectivity check failed (attempt {consecutive_failures})")
                
                # Wait for 3 failures before taking action (avoid false positives)
                if consecutive_failures >= 3:
                    force_reconnect()
                    consecutive_failures = 0  # Reset counter
                    time.sleep(30)  # Give it time to reconnect
                    
        except Exception as e:
            logging.error(f"Watchdog error: {e}")
        
        time.sleep(60)  # Check every minute

if __name__ == '__main__':
    main()
