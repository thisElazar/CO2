# CO2 Dual Sensor Monitoring System

Mormon Slough Restoration Research - Air Quality Science Project

---

## Quick Reference

### Key Commands

| Command | Where | What it does |
|---------|-------|--------------|
| `python3 co2_drive_analyzer.py --list` | Mac | List experiments in Drive root folder |
| `python3 co2_drive_analyzer.py --list -r` | Mac | List all experiments including subfolders |
| `python3 co2_drive_analyzer.py --list -f EarlySetupData` | Mac | List experiments in a specific subfolder |
| `python3 co2_drive_analyzer.py --analyze latest` | Mac | Generate analysis chart for most recent experiment |
| `python3 co2_drive_analyzer.py --analyze all` | Mac | Generate charts for all experiments (skips existing) |
| `python3 co2_drive_analyzer.py --analyze all -r` | Mac | Generate charts for all experiments including subfolders |
| `python3 co2_drive_analyzer.py --index` | Mac | Regenerate public JSON index with stats |
| `python3 co2_drive_analyzer.py --dashboard` | Mac | Generate self-contained HTML dashboard |
| `python3 co2_drive_analyzer.py --dashboard -r` | Mac | Dashboard including subfolder experiments |
| `python3 co2_drive_analyzer.py --local data.csv` | Mac | Analyze a local CSV file |
| `ssh thiselazar@co2sensor01.local` | Mac | Connect to the Pi sensor device |
| `sudo systemctl status co2logger` | Pi | Check data logger service status |
| `sudo systemctl status co2-drive-sync` | Pi | Check Drive uploader service status |
| `journalctl -u co2logger -f` | Pi | Live view of data logger output |
| `journalctl -u co2-drive-sync -f` | Pi | Live view of uploader output |

### Key Files

| File | Location | Purpose |
|------|----------|---------|
| `dualSensorLoggerLEDScreen_v3_1.ino` | Arduino | Sensor control, UI, experiment management |
| `dualSensorDataLoggerLCD_v3_2_robust.py` | Pi | Receives data from Arduino, saves to disk |
| `co2_drive_uploader.py` | Pi | Monitors experiments, uploads to Google Drive |
| `co2_drive_analyzer.py` | Mac | Downloads from Drive, generates analysis charts |
| `photosynthesis_comparison.png` | Mac | Latest comparative analysis visualization |

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              HARDWARE LAYER                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   ┌─────────────┐     ┌─────────────┐                                       │
│   │  MHZ19 #1   │     │  MHZ19 #2   │    Two CO2 sensors                    │
│   │ (Treatment) │     │  (Control)  │    measuring simultaneously           │
│   └──────┬──────┘     └──────┬──────┘                                       │
│          │                   │                                              │
│          └─────────┬─────────┘                                              │
│                    ▼                                                        │
│   ┌────────────────────────────────┐                                        │
│   │     Arduino (Nano/Uno)         │                                        │
│   │  • Rotary encoder UI           │                                        │
│   │  • 20x4 LCD display            │                                        │
│   │  • RGB LED feedback            │                                        │
│   │  • Experiment state machine    │                                        │
│   └───────────────┬────────────────┘                                        │
│                   │ Serial @ 9600 baud                                      │
│                   ▼                                                         │
│   ┌────────────────────────────────┐                                        │
│   │   Raspberry Pi Zero 2 W        │   co2sensor01.local                    │
│   │  • Data logger service         │                                        │
│   │  • Drive upload service        │                                        │
│   │  • WiFi connectivity           │                                        │
│   └───────────────┬────────────────┘                                        │
│                   │ WiFi                                                    │
└───────────────────┼─────────────────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              CLOUD LAYER                                     │
├─────────────────────────────────────────────────────────────────────────────┤
│   ┌────────────────────────────────┐                                        │
│   │        Google Drive            │                                        │
│   │  • Experiment folders          │                                        │
│   │  • CSV data files              │                                        │
│   │  • Analysis PNG charts         │                                        │
│   │  • experiments_index.json      │                                        │
│   └───────────────┬────────────────┘                                        │
└───────────────────┼─────────────────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                             ANALYSIS LAYER                                   │
├─────────────────────────────────────────────────────────────────────────────┤
│   ┌────────────────────────────────┐                                        │
│   │        Mac / Workstation       │                                        │
│   │  • co2_drive_analyzer.py       │   Runs on demand                       │
│   │  • Chart generation            │                                        │
│   │  • Statistical analysis        │                                        │
│   │  • HTML dashboard export       │                                        │
│   └────────────────────────────────┘                                        │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Data Flow

### 1. Data Collection (Arduino → Pi)

```
Arduino sends every second during LOGGING mode:
DATA,elapsed_sec,co2_treatment,temp_treatment,co2_control,temp_control

Example: DATA,127,485,22,491,23
```

The Pi's `dualSensorDataLoggerLCD_v3_2_robust.py`:
- Listens passively for DATA lines
- Writes to RAM (`/tmp/co2_experiments/`) first (protects SD card)
- Syncs to SD (`~/Documents/co2_experiments/`) every 5 minutes
- Creates experiment folder with timestamp: `YYYYMMDD_HHMMSS_<type>_<location>`

### 2. Upload to Cloud (Pi → Google Drive)

The `co2_drive_uploader.py` service:
- Monitors `~/Documents/co2_experiments/` for new folders
- Waits 12 minutes after last modification (experiment complete)
- Uploads CSV + metadata.json to Google Drive
- Blinks blue LED during upload

### 3. Analysis (Google Drive → Mac)

Run manually on Mac with `co2_drive_analyzer.py`:
- Downloads experiment CSVs from Drive
- Computes statistics (correlation, delta mean/std, duration)
- Generates multi-panel PNG visualizations
- Uploads PNGs back to Drive

---

## Components

### Arduino Firmware (`dualSensorLoggerLEDScreen_v3_1.ino`)

**State Machine:**
- `LIVE_READING` - Default mode, shows real-time CO2 on LCD
- `MODE_SELECT` - Choose: Live / Log Experiment / Calibrate
- `EXP_TYPE_SELECT` - Choose: Control / Test / Throwaway
- `LOGGING` - Recording data, sends DATA lines over serial
- `CALIBRATION` - Zero-point calibration mode

**Hardware Pins:**
```
Sensor 1 (Treatment): RX=D2, TX=D4
Sensor 2 (Control):   RX=D7, TX=D8
LED 1 RGB: R=D3, G=D5, B=D6 (PWM)
LED 2 RGB: R=D9, G=D10, B=D11 (PWM)
Recording LED: D13
Rotary Encoder: CLK=A1, DT=A2, SW=A3
LCD I2C: SDA=A4, SCL=A5
```

**LED Color Mapping:**
- Green: CO2 < 600 ppm (fresh air)
- Yellow: 600-1000 ppm (moderate)
- Red: > 1000 ppm (high)

### Pi Data Logger (`dualSensorDataLoggerLCD_v3_2_robust.py`)

**Key Features:**
- Passive listening (Arduino controls experiment)
- RAM-first writes (SD card protection)
- Auto-reconnect on serial disconnect (up to 10 attempts)
- Saves in-progress data before reconnection

**Output Format (`data.csv`):**
```csv
elapsed_seconds,co2_treatment,temp_treatment,co2_control,temp_control,delta_raw
1,485,22,491,23,-6
2,484,22,490,23,-6
...
```

### Pi Drive Uploader (`co2_drive_uploader.py`)

**Key Features:**
- Monitors for completed experiments (12 min no changes)
- Checks WiFi before upload attempts
- Creates matching folder structure in Drive
- LED feedback during upload

**Systemd Services:**
```bash
# Data logger
sudo systemctl start co2logger
sudo systemctl stop co2logger
sudo systemctl restart co2logger

# Drive uploader
sudo systemctl start co2-drive-sync
sudo systemctl stop co2-drive-sync
sudo systemctl restart co2-drive-sync
```

### Mac Analyzer (`co2_drive_analyzer.py`)

**Analysis Output:**
- 3-panel PNG: CO2 overview, Delta, Rolling correlation
- Statistics: samples, duration, correlation, delta mean/std
- Public JSON index for web viewers

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

## Experiment Organization

Experiments in Google Drive can be organized into subfolders for better management:

```
CO2 Experiments/
├── 20260115_141221_test_photosynthesis/   ← Active experiments in root
├── 20260115_132412_test_photosynthesis/
├── EarlySetupData/                         ← Archived setup/calibration data
│   ├── 20251225_165636_control_window/
│   └── ... (92 experiments)
└── experiments_index.json
```

**Workflow:**
- New experiments upload to the root folder automatically
- After analysis, move old/setup experiments to subfolders via Google Drive
- Use `--recursive` flag when you need to analyze across all experiments

**Subfolder Flags:**
| Flag | Effect |
|------|--------|
| (none) | Only root folder experiments |
| `-r` / `--recursive` | Include all subfolders |
| `-f NAME` / `--folder NAME` | Target specific subfolder only |

---

## Data Analysis

### Key Metrics

| Metric | Description | Good Value |
|--------|-------------|------------|
| **Correlation** | How well sensors track together | > 0.95 for control |
| **Delta Mean** | Average (Treatment - Control) | ~0 for control |
| **Delta Std** | Variability of difference | < 5 ppm for control |
| **Delta Change** | Drift over experiment | < 10 ppm for control |

### Interpreting Photosynthesis Results

- **Negative delta** = Treatment sensor reads lower = Plant absorbing CO2
- **Delta decreasing over time** = Active photosynthesis
- **Negative correlation** = Sensors diverging (one up, one down)

Example from 2026-01-10 photosynthesis run:
- Control baseline: Delta = +3.9 ppm (sensors matched)
- Photosynthesis: Delta = -143.4 ppm (plant absorbing CO2)
- **Signal: -147 ppm difference**

---

## File Locations

### On Pi (`co2sensor01.local`)

```
/home/thiselazar/
├── dualSensorDataLoggerLCD_v3_2_robust.py   # Main logger
├── co2_drive_uploader.py                     # Upload service
├── Documents/co2_experiments/                # Experiment data
│   ├── YYYYMMDD_HHMMSS_type_location/
│   │   ├── data.csv
│   │   └── metadata.json
│   └── .upload_tracker.json
└── .config/co2_uploader/
    ├── credentials.json                      # OAuth app creds
    └── token.json                            # Access token
```

### On Mac

```
/Users/fields/CO2/
├── README.md                                 # This file
├── co2_drive_analyzer.py                     # Analysis script
├── photosynthesis_comparison.png             # Latest analysis
├── co2_dashboard.html                        # Web dashboard
├── co2_web_viewer.html                       # Remote viewer
└── Air Quality Science/
    └── dualSensor/
        ├── dualSensorLoggerLEDScreen_v3_1/   # Arduino firmware
        └── co2_experiments/                  # Local experiment copies
```

### On Google Drive

```
CO2 Experiments/
├── experiments_index.json                    # Public index
├── YYYYMMDD_HHMMSS_type_location/
│   ├── data.csv
│   ├── metadata.json
│   └── *_analysis.png                        # Generated charts
└── EarlySetupData/                           # Legacy experiments
```

---

## Troubleshooting

### Pi not logging data
```bash
ssh thiselazar@co2sensor01.local
journalctl -u co2logger -f          # Check for errors
sudo systemctl restart co2logger    # Restart service
```

### Experiments not uploading
```bash
journalctl -u co2-drive-sync -f     # Check uploader logs
ping google.com                      # Check WiFi
sudo systemctl restart co2-drive-sync
```

### Arduino not responding
- Check USB connection
- Verify correct port in Pi logs
- Try unplugging and reconnecting
- Check Arduino IDE serial monitor (9600 baud)

### Analysis script errors
```bash
# Re-authenticate if token expired
cd /Users/fields/CO2
python3 co2_drive_analyzer.py --list   # Will prompt for auth if needed
```

---

## Hardware BOM

- Raspberry Pi Zero 2 W
- Arduino Nano (or Uno)
- 2x MHZ19B CO2 Sensors
- 20x4 I2C LCD Display
- 2x RGB LEDs (common cathode)
- Rotary Encoder with button
- Recording LED (red)
- USB cable (Arduino to Pi)
- Power supply (5V for Pi)

---

## Future Improvements

- [ ] Automatic chart generation on Mac when new uploads detected
- [ ] Light sensor integration for photosynthesis correlation
- [ ] Temperature/humidity logging (DHT22)
- [ ] Real-time web dashboard with WebSocket updates
- [ ] Mobile alerts for experiment completion

---

*Last updated: 2026-01-15*
