#!/bin/bash
# WiFi Watchdog Installation Script
# Run this on your Raspberry Pi to install the watchdog service

echo "Installing WiFi Watchdog..."

# Copy the Python script
sudo cp wifi_watchdog.py /usr/local/bin/
sudo chmod +x /usr/local/bin/wifi_watchdog.py

# Copy the service file
sudo cp wifi-watchdog.service /etc/systemd/system/

# Reload systemd to recognize new service
sudo systemctl daemon-reload

# Enable service to start on boot
sudo systemctl enable wifi-watchdog.service

# Start the service now
sudo systemctl start wifi-watchdog.service

echo ""
echo "Installation complete!"
echo ""
echo "Check status with: sudo systemctl status wifi-watchdog.service"
echo "View logs with: sudo tail -f /var/log/wifi_watchdog.log"
echo ""
echo "The watchdog will:"
echo "  - Check connectivity every 60 seconds"
echo "  - Force reconnect after 3 consecutive failures (~3 minutes)"
echo "  - Log all activity to /var/log/wifi_watchdog.log"
