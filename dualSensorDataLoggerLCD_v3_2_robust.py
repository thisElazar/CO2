#!/usr/bin/env python3
"""
Dual CO2 Sensor Data Logger v3.2 - Robust Reconnection Edition

ARCHITECTURE:
- Arduino = Experiment Controller (runs experiment, enters LOGGING mode)
- Pi = Data Logger (passively listens to Arduino output, saves to RAM → SD)

CHANGES FROM v3.1:
- Automatic reconnection on serial disconnection (up to 10 attempts)
- Better port selection (strongly prefers ACM/USB, rejects generic serial)
- Saves active experiments before reconnection attempts
- Graceful recovery from Arduino disconnect during experiments
- No more crashes on serial I/O errors

CHANGES FROM v3.0:
- Passive listening instead of requesting with 'R' command
- Detects Arduino mode automatically (LOGGING vs LIVE)
- Starts logging when Arduino enters LOGGING mode
- Stops when Arduino exits LOGGING mode
- All SD-card protection features maintained

Arduino sends:
- LOGGING mode: "DATA,elapsed,co2_1,temp_1,co2_2,temp_2"
- LIVE mode: "HEARTBEAT,co2_1,co2_2"
"""

import serial
import serial.tools.list_ports
import time
import json
import csv
import sys
import shutil
from datetime import datetime
from pathlib import Path


# ============== Configuration ==============

BAUD_RATE = 9600
SERIAL_TIMEOUT = 0.1

# SD CARD PROTECTION SETTINGS
SYNC_INTERVAL = 300  # Backup to SD every 5 minutes

# RAM-based logging (tmpfs - no SD writes!)
RAM_BASE_DIR = Path('/tmp/co2_experiments')

# SD card storage (only for backups and final copy)
SD_BASE_DIR = Path.home() / "Documents" / "co2_experiments"

# Calibration directory
CALIBRATION_DIR = Path('./calibration')


# ============== Serial Connection ==============

def find_arduino_port():
    """Auto-detect Arduino port, strongly prefer ACM/USB."""
    ports = list(serial.tools.list_ports.comports())
    
    if not ports:
        raise Exception("No serial ports found!")
    
    # First pass: Look for Arduino-specific ports
    for port in ports:
        if 'ttyACM' in port.device or 'ttyUSB' in port.device:
            print(f"✓ Auto-selected Arduino port: {port.device}")
            return port.device
    
    # Second pass: Reject known non-Arduino ports
    non_arduino_ports = ['ttyS0', 'ttyS1', 'ttyAMA']
    for port in ports:
        if not any(bad in port.device for bad in non_arduino_ports):
            print(f"⚠ Using port: {port.device} (not ideal)")
            return port.device
    
    raise Exception("No Arduino-like ports found! Only generic serial ports available.")


def connect_to_arduino(port):
    """Open serial connection and wait for READY signal."""
    ser = serial.Serial(
        port,
        BAUD_RATE,
        timeout=SERIAL_TIMEOUT,
        write_timeout=0.1
    )
    time.sleep(2)  # Give Arduino time to boot
    ser.reset_input_buffer()
    ser.reset_output_buffer()
    
    # Wait for READY signal
    start = time.time()
    while time.time() - start < 10:
        if ser.in_waiting:
            line = ser.readline().decode('utf-8', errors='ignore').strip()
            if line == "READY":
                print("✓ Arduino ready!")
                return ser
    
    print("⚠ No READY signal, but connected")
    return ser


def load_calibration_offset():
    """Load calibration offset if available."""
    if not CALIBRATION_DIR.exists():
        return 0.0
    
    cal_files = sorted(CALIBRATION_DIR.glob('calibration_*.json'), reverse=True)
    cal_files = [f for f in cal_files if '_raw' not in f.name]
    
    if not cal_files:
        return 0.0
    
    try:
        with open(cal_files[0], 'r') as f:
            cal = json.load(f)
        offset = cal['statistics']['delta_mean']
        print(f"✓ Loaded calibration: {cal_files[0].name}")
        print(f"  Offset: {offset:+.1f} ppm")
        return offset
    except Exception as e:
        print(f"⚠ Could not load calibration: {e}")
        return 0.0


def attempt_reconnection(max_attempts=10, retry_delay=3):
    """Attempt to reconnect to Arduino after disconnection."""
    for attempt in range(1, max_attempts + 1):
        try:
            print(f"\n🔄 Reconnection attempt {attempt}/{max_attempts}...")
            port = find_arduino_port()
            ser = connect_to_arduino(port)
            print("✓ Reconnected successfully!")
            return ser
        except Exception as e:
            print(f"  ❌ Failed: {e}")
            if attempt < max_attempts:
                print(f"  Waiting {retry_delay}s before retry...")
                time.sleep(retry_delay)
    
    print("\n❌ Could not reconnect after multiple attempts")
    return None


# ============== Experiment Session Manager ==============

class ExperimentSession:
    def __init__(self, metadata, offset=0.0):
        self.metadata = metadata
        self.offset = offset
        self.start_time = None
        self.readings = []
        self.csv_file = None
        self.csv_writer = None
        self.last_sync_time = 0
        
        # Generate folder name
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        exp_type = metadata.get('experiment_type', 'unknown')
        location = metadata.get('control_location', '')
        test_sub = metadata.get('test_subtype', '')
        
        if location:
            folder_name = f"{timestamp}_{exp_type}_{location}"
        elif test_sub:
            folder_name = f"{timestamp}_{exp_type}_{test_sub}"
        else:
            folder_name = f"{timestamp}_{exp_type}"
        
        self.ram_folder = RAM_BASE_DIR / folder_name
        self.sd_folder = SD_BASE_DIR / folder_name
    
    def create_folders(self):
        """Create RAM folder (SD folder created later)."""
        self.ram_folder.mkdir(parents=True, exist_ok=True)
        print(f"✓ Created RAM folder: {self.ram_folder}")
    
    def open_csv(self):
        """Open CSV file in RAM for writing."""
        csv_path = self.ram_folder / "experiment_data.csv"
        self.csv_file = open(csv_path, 'w', newline='')
        self.csv_writer = csv.writer(self.csv_file)
        
        # Write header
        self.csv_writer.writerow([
            'timestamp', 'elapsed_seconds',
            'co2_treatment', 'temp_treatment',
            'co2_control', 'temp_control',
            'delta_raw', 'delta_corrected'
        ])
        self.csv_file.flush()
        print(f"✓ Opened CSV: {csv_path.name}")
    
    def log_reading(self, elapsed, co2_1, temp_1, co2_2, temp_2):
        """Log a reading to RAM."""
        timestamp = datetime.now().isoformat()
        delta_raw = co2_1 - co2_2
        co2_2_corrected = co2_2 + self.offset
        delta_corrected = co2_1 - co2_2_corrected
        
        self.csv_writer.writerow([
            timestamp, elapsed,
            co2_1, temp_1,
            co2_2, temp_2,
            delta_raw, round(delta_corrected, 1)
        ])
        self.csv_file.flush()
        
        self.readings.append({
            'timestamp': timestamp,
            'elapsed_seconds': elapsed,
            'co2_treatment': co2_1,
            'temp_treatment': temp_1,
            'co2_control': co2_2,
            'temp_control': temp_2,
            'delta_raw': delta_raw,
            'delta_corrected': round(delta_corrected, 1)
        })
    
    def should_sync(self):
        """Check if it's time to sync to SD."""
        now = time.time()
        if now - self.last_sync_time >= SYNC_INTERVAL:
            self.last_sync_time = now
            return True
        return False
    
    def sync_to_sd(self):
        """Periodic backup from RAM to SD."""
        try:
            self.sd_folder.mkdir(parents=True, exist_ok=True)
            
            for item in self.ram_folder.iterdir():
                if item.is_file():
                    dest = self.sd_folder / item.name
                    shutil.copy2(item, dest)
            
            print(f"  [Synced to SD: {datetime.now().strftime('%H:%M:%S')}]")
        except Exception as e:
            print(f"  [Sync failed: {e}]")
    
    def save_metadata(self):
        """Write metadata to RAM."""
        meta_path = self.ram_folder / "metadata.json"
        full_meta = {
            **self.metadata,
            'start_time': self.start_time.isoformat() if self.start_time else None,
            'total_readings': len(self.readings),
            'valid_readings': sum(1 for r in self.readings if r is not None),
            'calibration_offset': self.offset,
        }
        with open(meta_path, 'w') as f:
            json.dump(full_meta, f, indent=2)
        return meta_path
    
    def finalize_to_sd(self):
        """Final copy from RAM to SD at experiment end."""
        print("\n📁 Final save to SD card... ", end='', flush=True)
        
        try:
            self.sd_folder.mkdir(parents=True, exist_ok=True)
            
            for item in self.ram_folder.iterdir():
                dest = self.sd_folder / item.name
                if item.is_file():
                    shutil.copy2(item, dest)
            
            print("✓")
            print(f"  Saved to: {self.sd_folder}")
            
        except Exception as e:
            print(f"⚠ Failed: {e}")
    
    def close(self):
        """Close CSV file."""
        if self.csv_file:
            self.csv_file.close()


# ============== Main Loop ==============

def main():
    print("\n" + "=" * 60)
    print("  Dual CO2 Sensor Data Logger v3.1 - Passive Listener")
    print("  RAM-based logging with periodic SD backup")
    print("  Arduino controls experiment, Pi logs data")
    print("=" * 60)
    
    # Ensure RAM directory exists
    RAM_BASE_DIR.mkdir(parents=True, exist_ok=True)
    
    # Load calibration
    offset = load_calibration_offset()
    
    # Find and connect to Arduino
    try:
        port = find_arduino_port()
    except Exception as e:
        print(f"❌ Error finding Arduino: {e}")
        sys.exit(1)
    
    print(f"\nConnecting to {port}...")
    
    try:
        ser = connect_to_arduino(port)
    except Exception as e:
        print(f"❌ Failed to connect: {e}")
        sys.exit(1)
    
    print("\n" + "=" * 60)
    print("  Listening for Arduino experiment data...")
    print("  Waiting for Arduino to start logging...")
    print("=" * 60)
    
    current_session = None
    in_logging_mode = False
    
    try:
        while True:
            try:
                # Check for incoming data (can raise OSError on disconnect)
                if ser.in_waiting:
                    try:
                        line = ser.readline().decode('utf-8', errors='ignore').strip()
                        
                        if not line:
                            continue
                        
                        # Parse DATA lines (Arduino in LOGGING mode)
                        if line.startswith("DATA,"):
                            parts = line.split(',')
                            if len(parts) == 6:
                                try:
                                    elapsed = int(parts[1])
                                    co2_1 = int(parts[2])
                                    temp_1 = int(parts[3])
                                    co2_2 = int(parts[4])
                                    temp_2 = int(parts[5])
                                    
                                    # Start new session if needed
                                    if not in_logging_mode:
                                        print("\n🟢 Arduino started logging!")
                                        
                                        # Get metadata from Arduino
                                        ser.write(b'M')
                                        ser.flush()
                                        time.sleep(0.1)
                                        
                                        metadata_line = None
                                        for _ in range(10):
                                            if ser.in_waiting:
                                                meta = ser.readline().decode('utf-8', errors='ignore').strip()
                                                if meta.startswith("META:"):
                                                    metadata_line = meta
                                                    break
                                            time.sleep(0.05)
                                        
                                        # Parse metadata
                                        if metadata_line:
                                            metadata = {}
                                            parts = metadata_line.replace("META:", "").split(',')
                                            for part in parts:
                                                if '=' in part:
                                                    key, value = part.split('=', 1)
                                                    metadata[key] = value
                                            
                                            exp_type = metadata.get('type', 'unknown')
                                            print(f"  Experiment type: {exp_type}")
                                            
                                            if exp_type == 'control':
                                                location = metadata.get('location', 'unspecified')
                                                print(f"  Location: {location}")
                                                session_meta = {
                                                    'experiment_type': exp_type,
                                                    'control_location': location,
                                                    'test_subtype': None,
                                                }
                                            elif exp_type == 'test':
                                                subtype = metadata.get('test_subtype', 'unspecified')
                                                print(f"  Test subtype: {subtype}")
                                                session_meta = {
                                                    'experiment_type': exp_type,
                                                    'control_location': None,
                                                    'test_subtype': subtype,
                                                }
                                            else:
                                                session_meta = {
                                                    'experiment_type': exp_type,
                                                    'control_location': None,
                                                    'test_subtype': None,
                                                }
                                        else:
                                            print("  ⚠ No metadata received")
                                            session_meta = {
                                                'experiment_type': 'unknown',
                                                'control_location': None,
                                                'test_subtype': None,
                                            }
                                        
                                        # Create new session
                                        current_session = ExperimentSession(session_meta, offset)
                                        current_session.create_folders()
                                        current_session.open_csv()
                                        current_session.start_time = datetime.now()
                                        current_session.last_sync_time = time.time()
                                        
                                        in_logging_mode = True
                                        print("\n📊 Logging to RAM (SD-card safe)...\n")
                                    
                                    # Log the reading
                                    if current_session:
                                        current_session.log_reading(elapsed, co2_1, temp_1, co2_2, temp_2)
                                        
                                        # Progress display
                                        mins = elapsed // 60
                                        secs = elapsed % 60
                                        delta = co2_1 - (co2_2 + offset)
                                        print(f"\r  {mins:02d}:{secs:02d} | T:{co2_1} C:{co2_2} Δ:{delta:+.0f}", end='', flush=True)
                                        
                                        # Periodic sync
                                        if current_session.should_sync():
                                            current_session.save_metadata()
                                            current_session.sync_to_sd()
                                    
                                except (ValueError, IndexError) as e:
                                    print(f"\n⚠ Parse error: {e}")
                        
                        # Detect end of logging (HEARTBEAT or STOP)
                        elif line.startswith("HEARTBEAT,") or line == "STOP":
                            if in_logging_mode and current_session:
                                print("\n\n🔴 Arduino stopped logging!")
                                
                                # Finalize session
                                current_session.save_metadata()
                                current_session.finalize_to_sd()
                                current_session.close()
                                
                                # Summary
                                valid = [r for r in current_session.readings if r is not None]
                                if valid:
                                    first = valid[0]
                                    last = valid[-1]
                                    
                                    delta_start = first['delta_corrected']
                                    delta_end = last['delta_corrected']
                                    delta_change = delta_end - delta_start
                                    
                                    elapsed_min = last['elapsed_seconds'] / 60
                                    rate = delta_change / elapsed_min if elapsed_min > 0 else 0
                                    
                                    print("\n" + "-" * 50)
                                    print("  RESULTS")
                                    print("-" * 50)
                                    print(f"  Readings:  {len(valid)}")
                                    print(f"  Duration:  {elapsed_min:.1f} minutes")
                                    print(f"  Delta:     {delta_start:.0f} → {delta_end:.0f} ppm  ({delta_change:+.1f})")
                                    print(f"  Rate:      {rate:+.2f} ppm/min")
                                    print("-" * 50)
                                
                                print(f"\n✓ Experiment complete")
                                print(f"  RAM folder: {current_session.ram_folder}")
                                print(f"  SD backup: {current_session.sd_folder}")
                                
                                current_session = None
                                in_logging_mode = False
                                
                                print("\n" + "=" * 60)
                                print("  Waiting for next Arduino experiment...")
                                print("=" * 60)
                        
                        # Debug: Show other messages
                        elif line not in ["READY", "PONG"] and not line.startswith("HEARTBEAT"):
                            if line:  # Don't print empty lines
                                print(f"\n[Arduino]: {line}")
                    
                    except Exception as e:
                        print(f"\n⚠ Error processing line: {e}")
                
                time.sleep(0.01)  # Small delay to prevent CPU spin
            
            except (OSError, serial.SerialException) as e:
                # Serial connection lost!
                print(f"\n\n⚠️  SERIAL CONNECTION LOST: {e}")
                
                # Save current session if active
                if current_session:
                    print("💾 Saving current experiment data...")
                    try:
                        current_session.save_metadata()
                        current_session.finalize_to_sd()
                        current_session.close()
                        print("✓ Data saved successfully")
                    except Exception as save_error:
                        print(f"❌ Error saving session: {save_error}")
                    
                    current_session = None
                    in_logging_mode = False
                
                # Close bad connection
                try:
                    ser.close()
                except:
                    pass
                
                # Attempt reconnection
                print("\n" + "=" * 60)
                print("  ATTEMPTING RECONNECTION")
                print("=" * 60)
                
                ser = attempt_reconnection()
                
                if ser is None:
                    print("\n❌ Reconnection failed. Exiting.")
                    break
                
                # Successfully reconnected
                print("\n" + "=" * 60)
                print("  Listening for Arduino experiment data...")
                print("  Waiting for Arduino to start logging...")
                print("=" * 60)
                continue
    
    except KeyboardInterrupt:
        print("\n\n⚠ Interrupted by user")
        if current_session:
            current_session.save_metadata()
            current_session.finalize_to_sd()
            current_session.close()
    
    finally:
        if ser:
            ser.close()
        print("\n✓ Logger stopped")


if __name__ == "__main__":
    main()
