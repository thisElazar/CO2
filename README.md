# CO2 Experiment Logger

A dual CO2 sensor monitoring system built with Raspberry Pi Zero 2 W and Arduino.

## Overview

This system passively monitors two CO2 sensors connected to an Arduino, logs experiment data, and automatically uploads results to Google Drive when WiFi is available.

## Architecture

- **Arduino**: Experiment controller - runs experiments and enters LOGGING mode
- **Raspberry Pi**: Data logger - listens to Arduino output, saves data with SD card protection

## Features

- Dual CO2 sensor support with temperature readings
- RAM-based logging (protects SD card from wear)
- Periodic backups to SD storage
- Automatic serial reconnection on disconnect
- Google Drive auto-upload when experiments complete
- LED status indicators
- LCD display support
- WiFi watchdog for connection reliability

## Hardware

- Raspberry Pi Zero 2 W
- Arduino (via USB serial)
- 2x CO2 sensors
- LCD display
- Status LEDs

## Files

| File | Description |
|------|-------------|
| `dualSensorDataLoggerLCD_v3_2_robust.py` | Main data logger with reconnection support |
| `co2_drive_uploader.py` | Google Drive upload service |
| `pi_status_monitor.py` | System status and LED control |
| `wifi_watchdog.py` | WiFi connection monitor |
| `led_test.py` | LED diagnostic utility |
| `install_watchdog.sh` | Watchdog setup script |

## Data Format

Arduino sends data in two modes:
- **LOGGING mode**: `DATA,elapsed,co2_1,temp_1,co2_2,temp_2`
- **LIVE mode**: `HEARTBEAT,co2_1,co2_2`

## Storage

- RAM buffer: `/tmp/co2_experiments/`
- SD storage: `~/Documents/co2_experiments/`
- Sync interval: Every 5 minutes
