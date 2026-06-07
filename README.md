# CO2 Dual Sensor Monitoring System

Mormon Slough Restoration Research - Air Quality Science Project

---

## Quick Reference

### Key Commands

| Command | Where | What it does |
|---------|-------|--------------|
| `python3 co2_analyzer.py --list` | Mac | List all experiments from WandR |
| `python3 co2_analyzer.py --list --filter test` | Mac | List only test experiments |
| `python3 co2_analyzer.py --analyze latest` | Mac | Generate analysis chart for most recent |
| `python3 co2_analyzer.py --analyze all` | Mac | Generate charts for all experiments |
| `python3 co2_analyzer.py --local data.csv` | Mac | Analyze a local CSV file |
| `ssh thiselazar@co2sensor01.local` | Mac | Connect to the Pi sensor device |
| `journalctl -u co2-data-logger -f` | Pi | Live view of data logger output |
| `journalctl -u co2-wandr-sync -f` | Pi | Live view of uploader output |
| `journalctl -u co2-wifi-manager -f` | Pi | Live view of WiFi manager |

### Web Dashboard

| URL | What |
|-----|------|
| `wandr.hatchworkshop.org/co2` | Standalone CO2 experiments dashboard |
| `wandr.hatchworkshop.org/dashboard` | Full WandR dashboard (CO2 under Science) |

### Key Repos

| Repo | What |
|------|------|
| `github.com/thisElazar/CO2` | This repo — Mac-side analyzer |
| `github.com/thisElazar/hatch-wandr` | WandR server + Pi/Arduino code (`sensors/co2/`) |

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         HARDWARE                                │
│                                                                 │
│   MHZ19 #1 (Treatment)    MHZ19 #2 (Control)                   │
│         └────────┬────────────┘                                 │
│                  ▼                                              │
│   ┌──────────────────────────┐                                  │
│   │     Arduino Uno          │  Rotary encoder UI, LCD,         │
│   │     (v3.1 firmware)      │  RGB LEDs, experiment state      │
│   └───────────┬──────────────┘  machine                         │
│               │ Serial @ 9600 baud                              │
│               ▼                                                 │
│   ┌──────────────────────────┐                                  │
│   │  Raspberry Pi Zero 2 W   │  co2sensor01.local               │
│   │  • data_logger.py        │  Listens, writes CSV             │
│   │  • uploader.py           │  Uploads to WandR via HTTPS      │
│   │  • wifi_manager.py       │  Captive portal for WiFi setup   │
│   │  • status_monitor.py     │  GPIO LED feedback               │
│   └───────────┬──────────────┘                                  │
│               │ WiFi / HTTPS                                    │
└───────────────┼─────────────────────────────────────────────────┘
                │
                ▼
┌─────────────────────────────────────────────────────────────────┐
│                    WANDR SERVER (hatch-wandr)                    │
│                                                                 │
│   POST /api/devices/upload    ← Pi uploads experiments here     │
│   GET  /api/science/experiments  ← Dashboard + analyzer read    │
│   GET  /api/devices              ← Device fleet management      │
│                                                                 │
│   /mnt/storage/science/co2_experiments/                         │
│   ├── {experiment_id}/data.csv + metadata.json                  │
│   ├── _devices/co2-sensor-01.json                               │
│   └── _tokens/                                                  │
│                                                                 │
│   Web UI:                                                       │
│   • /co2           Standalone experiments + devices dashboard   │
│   • /dashboard     Full WandR (CO2 under "CO2 Experiments")     │
└─────────────────────────────────────────────────────────────────┘
                │
                ▼
┌─────────────────────────────────────────────────────────────────┐
│                    MAC (this repo)                               │
│                                                                 │
│   co2_analyzer.py  ← Pulls from WandR API, generates PNGs      │
└─────────────────────────────────────────────────────────────────┘
```

---

## Data Flow

### 1. Data Collection (Arduino → Pi)

```
Arduino sends every second during LOGGING mode:
DATA,elapsed_sec,co2_treatment,temp_treatment,co2_control,temp_control

Example: DATA,127,485,22,491,23
```

The Pi's `data_logger.py`:
- Listens passively for DATA lines
- Writes to RAM (`/tmp/co2_experiments/`) first (protects SD card)
- Syncs to SD (`~/Documents/co2_experiments/`) every 5 minutes
- Creates experiment folder: `YYYYMMDD_HHMMSS_<type>_<location>`

### 2. Upload to WandR (Pi → Server)

The `uploader.py` service:
- Scans for completed experiments every 60 seconds
- Experiment is "complete" after 2 minutes of no changes
- HTTPS POST to `wandr.hatchworkshop.org/api/devices/upload`
- Bearer token auth (per-device, stored in `/etc/co2-sensor/device.conf`)
- Server scores experiment quality on ingest (0-5)
- Blue LED blinks during upload

### 3. Analysis

**Browser (primary):** Visit `wandr.hatchworkshop.org/co2` for interactive charts, annotations, media attachments, and device management.

**CLI (optional):** Run `python3 co2_analyzer.py` on Mac for local PNG generation.

---

## New Device Setup

### Provisioning a new sensor

```bash
# 1. Flash Raspberry Pi OS to SD card (configure WiFi in Imager)
# 2. Wire Arduino to Pi via USB
# 3. Flash Arduino with firmware from wandr/sensors/co2/firmware/
# 4. Copy sensor code to Pi:
scp -r wandr/sensors/co2/ user@new-pi.local:/tmp/co2-setup/

# 5. SSH in and provision:
ssh user@new-pi.local
bash /tmp/co2-setup/deploy/provision.sh co2-sensor-02 "Classroom Unit" "Lincoln High School"
```

The provisioning script:
- Installs dependencies (`python3-serial`)
- Registers the device with WandR (gets auth token)
- Installs code to `/opt/co2-sensor/`
- Enables systemd services
- Switches to headless mode (no desktop)

### WiFi Setup (for deployed devices)

If a device can't connect to WiFi, it broadcasts a **CO2-Sensor-Setup** hotspot. Connect with a phone (password: `co2sensor`), enter the local WiFi credentials on the captive portal page, and the device switches to the new network.

---

## Pi Services

| Service | Purpose |
|---------|---------|
| `co2-data-logger` | Reads Arduino serial, writes CSV |
| `co2-wandr-sync` | Uploads completed experiments to WandR |
| `co2-wifi-manager` | WiFi management + captive portal fallback |
| `co2-status-monitor` | GPIO LED feedback (green/blue/red) |

```bash
# Check all services
systemctl is-active co2-data-logger co2-wandr-sync co2-wifi-manager

# View logs
journalctl -u co2-wandr-sync -f
```

---

## Experiment Types

| Type | Purpose | Sensor Placement |
|------|---------|------------------|
| `control_window` | Baseline calibration | Both sensors together, near window |
| `control_indoor` | Indoor baseline | Both sensors together, indoors |
| `control_outdoor` | Outdoor baseline | Both sensors together, outside |
| `test_photosynthesis` | Plant CO2 absorption | Treatment near plant, Control away |
| `test_breath` | Human respiration test | Treatment near subject |
| `throwaway` | Quick debugging | Any configuration |

---

## Data Analysis

### Key Metrics

| Metric | Description | Good Value |
|--------|-------------|------------|
| **Correlation** | How well sensors track together | > 0.95 for control |
| **Delta Mean** | Average (Treatment - Control) | ~0 for control |
| **Delta Std** | Variability of difference | < 5 ppm for control |
| **Quality Score** | Automated 0-5 rating | 4+ for publishable data |

### Interpreting Photosynthesis Results

- **Negative delta** = Treatment sensor reads lower = Plant absorbing CO2
- **Delta decreasing over time** = Active photosynthesis
- **Negative correlation** = Sensors diverging (one up, one down)

---

## Code Locations

### This repo (Mac-side analysis)

```
co2_analyzer.py              Pulls from WandR API, generates PNGs
analyze_drawdown.py          CO2 drawdown analysis
co2_rate_analysis.py         Rate of change analysis
```

### WandR repo (`sensors/co2/`)

```
pi/
  data_logger.py             Arduino serial → CSV
  uploader.py                HTTPS upload to WandR
  wifi_manager.py            Captive portal + WiFi management
  status_monitor.py          GPIO LED feedback
  wifi_watchdog.py           Legacy WiFi reconnection
firmware/
  dualSensorLoggerLEDScreen/ Arduino .ino source (v3.1)
deploy/
  provision.sh               One-command new device setup
  *.service                  Systemd unit files
```

### WandR server

```
dashboard/server/routes/devices.js    Upload API + device registry
dashboard/server/lib/co2-scoring.js   Experiment quality scoring
/mnt/storage/science/co2_experiments/ Experiment data (source of truth)
```

---

## Hardware BOM

- Raspberry Pi Zero 2 W
- Arduino Uno
- 2x MHZ19B CO2 Sensors
- 20x4 I2C LCD Display
- 2x RGB LEDs (common cathode)
- Rotary Encoder with button
- Recording LED (red)
- USB cable (Arduino to Pi)
- Power supply (5V for Pi)

---

*Last updated: 2026-06-07*
