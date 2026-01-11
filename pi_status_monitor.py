#!/usr/bin/env python3
"""
Pi GPIO Status Monitor for CO2 Monitoring System
Provides visual feedback via LEDs and safe shutdown via button

LED Behavior:
- Green (GPIO 17): System Ready
  - Solid: All services + WiFi
  - Blinking: All services, no WiFi
  - OFF: Service failure
  
- Blue (GPIO 27): Data Capture
  - Solid: Active data writing (<5s ago)
  - Fast blink: Drive upload in progress
  - Slow blink: Stale data (waiting/paused)
  - OFF: Idle, no experiment
  
- Red (GPIO 22): Errors/Warnings
  - OFF: Healthy
  - Slow blink: Warning (recoverable)
  - Fast blink: Critical failure
  - Solid: Shutdown in progress

Button (GPIO 3): Shutdown/Wake
- 3-second hold: Shutdown sequence
- When off: Single press to wake
"""

import RPi.GPIO as GPIO
import subprocess
import time
import os
from pathlib import Path
from datetime import datetime
import sys
import signal

# GPIO Pin Assignments
PIN_GREEN_LED = 17  # System Ready
PIN_BLUE_LED = 27   # Data Capture
PIN_RED_LED = 22    # Error/Warning
PIN_BUTTON = 3      # Shutdown/Wake button

# LED States
LED_OFF = 0
LED_SOLID = 1
LED_SLOW_BLINK = 2  # 1 Hz
LED_FAST_BLINK = 3  # 4 Hz

# Timing Constants
DATA_FRESH_THRESHOLD = 5.0  # Seconds - data file age for "active capture"
BUTTON_HOLD_TIME = 5.0      # Seconds - hold duration for shutdown
SHUTDOWN_CANCEL_TIME = 5.0  # Seconds - cancellable countdown after hold
BUTTON_DEBOUNCE = 0.05      # Seconds - debounce delay
BLINK_SLOW = 0.5            # Seconds - slow blink half-period
BLINK_FAST = 0.125          # Seconds - fast blink half-period

# Service Names
SERVICE_LOGGER = 'co2logger.service'
SERVICE_DRIVE = 'co2-drive-sync.service'

# File Paths
TMP_DIR = Path('/tmp/co2_experiments')
UPLOAD_FLAG = Path('/tmp/upload_in_progress')

# Global state for LED control
led_states = {
    PIN_GREEN_LED: LED_OFF,
    PIN_BLUE_LED: LED_OFF,
    PIN_RED_LED: LED_OFF
}

blink_phases = {
    PIN_GREEN_LED: False,
    PIN_BLUE_LED: False,
    PIN_RED_LED: False
}

last_blink_times = {
    PIN_GREEN_LED: 0,
    PIN_BLUE_LED: 0,
    PIN_RED_LED: 0
}

shutdown_initiated = False


def setup_gpio():
    """Initialize GPIO pins"""
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    
    # Setup LED outputs
    GPIO.setup(PIN_GREEN_LED, GPIO.OUT)
    GPIO.setup(PIN_BLUE_LED, GPIO.OUT)
    GPIO.setup(PIN_RED_LED, GPIO.OUT)
    
    # Setup button input with pull-up
    GPIO.setup(PIN_BUTTON, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    
    # Start with all LEDs off
    # COMMON ANODE: HIGH = OFF, LOW = ON
    GPIO.output(PIN_GREEN_LED, GPIO.HIGH)
    GPIO.output(PIN_BLUE_LED, GPIO.HIGH)
    GPIO.output(PIN_RED_LED, GPIO.HIGH)
    
    print("GPIO initialized successfully (common anode RGB LEDs)")


def cleanup_gpio():
    """Clean up GPIO on exit"""
    try:
        # Turn off LEDs BEFORE cleanup (COMMON ANODE: HIGH = OFF)
        GPIO.output(PIN_GREEN_LED, GPIO.HIGH)
        GPIO.output(PIN_BLUE_LED, GPIO.HIGH)
        GPIO.output(PIN_RED_LED, GPIO.HIGH)
    except:
        pass  # Ignore errors if GPIO already reset
    
    # Now clean up GPIO (this resets the mode)
    GPIO.cleanup()
    print("GPIO cleaned up")


def check_service_status(service_name):
    """Check if a systemd service is active"""
    try:
        result = subprocess.run(
            ['systemctl', 'is-active', service_name],
            capture_output=True,
            text=True,
            timeout=2
        )
        return result.stdout.strip() == 'active'
    except:
        return False


def check_wifi_connected():
    """Check if WiFi interface has an IP address"""
    try:
        result = subprocess.run(
            ['ip', 'addr', 'show', 'wlan0'],
            capture_output=True,
            text=True,
            timeout=2
        )
        # Check if interface has an IP address assigned
        return 'inet ' in result.stdout
    except:
        return False


def get_newest_data_file():
    """Find the most recently modified data CSV file"""
    try:
        # Search in /tmp/co2_experiments for both direct files and subdirectories
        data_files = list(TMP_DIR.glob('*_data.csv'))  # Direct files
        data_files.extend(list(TMP_DIR.glob('*/*_data.csv')))  # Files in subdirectories
        
        if not data_files:
            return None
        return max(data_files, key=lambda f: f.stat().st_mtime)
    except:
        return None


def get_file_age(filepath):
    """Get age of file in seconds"""
    try:
        mtime = filepath.stat().st_mtime
        return time.time() - mtime
    except:
        return float('inf')


def determine_green_led_state():
    """
    Determine green LED state based on service health and WiFi
    Returns: LED_SOLID, LED_SLOW_BLINK, or LED_OFF
    """
    logger_ok = check_service_status(SERVICE_LOGGER)
    drive_ok = check_service_status(SERVICE_DRIVE)
    wifi_ok = check_wifi_connected()
    
    if logger_ok and drive_ok:
        if wifi_ok:
            return LED_SOLID       # Full operational
        else:
            return LED_SLOW_BLINK  # Degraded mode (no WiFi)
    else:
        return LED_OFF             # Service failure


def determine_blue_led_state():
    """
    Determine blue LED state based on data activity and persistence
    Returns: LED_SOLID, LED_FAST_BLINK, LED_SLOW_BLINK, or LED_OFF
    """
    # Check for active upload first
    if UPLOAD_FLAG.exists():
        return LED_FAST_BLINK  # Drive upload in progress
    
    # Check for data files in /tmp
    newest_file = get_newest_data_file()
    if newest_file is None:
        return LED_OFF  # No experiment data
    
    # Check file age
    age = get_file_age(newest_file)
    
    if age < DATA_FRESH_THRESHOLD:
        return LED_SOLID  # Active capture - data being written NOW
    
    # File is stale - check if it's been persisted to SD card
    # Extract experiment folder name from the file's parent directory
    experiment_folder = newest_file.parent.name if newest_file.parent != TMP_DIR else None
    
    if not experiment_folder:
        # File directly in /tmp/co2_experiments (unusual, but handle it)
        return LED_SLOW_BLINK
    
    # Check if experiment folder exists in permanent storage
    permanent_dir = Path('/home/thiselazar/Documents/co2_experiments')
    permanent_experiment = permanent_dir / experiment_folder
    
    # Look for the actual CSV file using the same filename as in /tmp
    permanent_csv = permanent_experiment / newest_file.name
    
    # Debug logging (only log once per state change to avoid spam)
    if not hasattr(determine_blue_led_state, 'last_check'):
        determine_blue_led_state.last_check = None
    
    current_check = (str(newest_file), permanent_csv.exists())
    if determine_blue_led_state.last_check != current_check:
        print(f"[Blue LED Check] TMP file: {newest_file}")
        print(f"[Blue LED Check] Looking for SD: {permanent_csv}")
        print(f"[Blue LED Check] SD exists: {permanent_csv.exists()}")
        determine_blue_led_state.last_check = current_check
    
    if permanent_csv.exists():
        # Data safely on SD card - turn OFF
        return LED_OFF
    else:
        # WARNING: Data in /tmp but not persisted to SD!
        return LED_SLOW_BLINK


def determine_red_led_state():
    """
    Determine red LED state based on system health
    Returns: LED_SOLID, LED_FAST_BLINK, LED_SLOW_BLINK, or LED_OFF
    """
    if shutdown_initiated:
        return LED_SOLID  # Shutdown in progress
    
    logger_ok = check_service_status(SERVICE_LOGGER)
    drive_ok = check_service_status(SERVICE_DRIVE)
    
    # Both services down = critical
    if not logger_ok and not drive_ok:
        return LED_FAST_BLINK  # Critical failure
    
    # One service down = warning
    if not logger_ok or not drive_ok:
        return LED_SLOW_BLINK  # Warning
    
    return LED_OFF  # All healthy


def update_led_states():
    """Update all LED target states based on system conditions"""
    global led_states
    
    led_states[PIN_GREEN_LED] = determine_green_led_state()
    led_states[PIN_BLUE_LED] = determine_blue_led_state()
    led_states[PIN_RED_LED] = determine_red_led_state()


def update_led_outputs():
    """Update physical LED outputs based on current states and blink timing
    
    COMMON ANODE LOGIC:
    - GPIO.HIGH = LED OFF (no voltage difference)
    - GPIO.LOW = LED ON (current flows from 3.3V through LED to GPIO ground)
    """
    global blink_phases, last_blink_times
    
    # Check if GPIO is initialized
    try:
        GPIO.getmode()
    except RuntimeError:
        # GPIO not initialized or was cleaned up
        return
    
    current_time = time.time()
    
    for pin in [PIN_GREEN_LED, PIN_BLUE_LED, PIN_RED_LED]:
        state = led_states[pin]
        
        try:
            if state == LED_OFF:
                GPIO.output(pin, GPIO.HIGH)  # HIGH = OFF for common anode
                
            elif state == LED_SOLID:
                GPIO.output(pin, GPIO.LOW)   # LOW = ON for common anode
                
            elif state == LED_SLOW_BLINK:
                # Toggle at slow rate (1 Hz)
                if current_time - last_blink_times[pin] >= BLINK_SLOW:
                    blink_phases[pin] = not blink_phases[pin]
                    last_blink_times[pin] = current_time
                GPIO.output(pin, GPIO.LOW if blink_phases[pin] else GPIO.HIGH)
                
            elif state == LED_FAST_BLINK:
                # Toggle at fast rate (4 Hz)
                if current_time - last_blink_times[pin] >= BLINK_FAST:
                    blink_phases[pin] = not blink_phases[pin]
                    last_blink_times[pin] = current_time
                GPIO.output(pin, GPIO.LOW if blink_phases[pin] else GPIO.HIGH)
        
        except RuntimeError:
            # GPIO mode was reset - likely during shutdown, just exit
            return


def flash_all_leds(times=3, duration=0.2):
    """Flash all LEDs together for visual feedback"""
    for _ in range(times):
        # COMMON ANODE: LOW = ON
        GPIO.output(PIN_GREEN_LED, GPIO.LOW)
        GPIO.output(PIN_BLUE_LED, GPIO.LOW)
        GPIO.output(PIN_RED_LED, GPIO.LOW)
        time.sleep(duration)
        # COMMON ANODE: HIGH = OFF
        GPIO.output(PIN_GREEN_LED, GPIO.HIGH)
        GPIO.output(PIN_BLUE_LED, GPIO.HIGH)
        GPIO.output(PIN_RED_LED, GPIO.HIGH)
        time.sleep(duration)


def shutdown_confirmation_flash():
    """
    Shutdown confirmation flash sequence
    - 3 flashes at even pace (0.3s)
    - 2 flashes at faster pace (0.15s)
    """
    try:
        # 3 even flashes
        for _ in range(3):
            GPIO.output(PIN_GREEN_LED, GPIO.LOW)
            GPIO.output(PIN_BLUE_LED, GPIO.LOW)
            GPIO.output(PIN_RED_LED, GPIO.LOW)
            time.sleep(0.3)
            GPIO.output(PIN_GREEN_LED, GPIO.HIGH)
            GPIO.output(PIN_BLUE_LED, GPIO.HIGH)
            GPIO.output(PIN_RED_LED, GPIO.HIGH)
            time.sleep(0.3)
        
        # Small pause
        time.sleep(0.2)
        
        # 2 fast flashes
        for _ in range(2):
            GPIO.output(PIN_GREEN_LED, GPIO.LOW)
            GPIO.output(PIN_BLUE_LED, GPIO.LOW)
            GPIO.output(PIN_RED_LED, GPIO.LOW)
            time.sleep(0.15)
            GPIO.output(PIN_GREEN_LED, GPIO.HIGH)
            GPIO.output(PIN_BLUE_LED, GPIO.HIGH)
            GPIO.output(PIN_RED_LED, GPIO.HIGH)
            time.sleep(0.15)
    except RuntimeError:
        # GPIO reset, just return
        pass


def cancellable_countdown_dance():
    """
    5-second countdown with LED dance animation
    Returns True if shutdown should proceed, False if cancelled
    """
    print("Shutdown countdown - press button repeatedly to cancel!")
    
    # Dance pattern: chase in reverse (red → blue → green)
    chase_speed = 0.12  # Faster than boot for urgency
    start_time = time.time()
    cancel_detected = False
    last_button_state = GPIO.HIGH
    
    try:
        while time.time() - start_time < SHUTDOWN_CANCEL_TIME:
            # Check for button press (cancel signal)
            try:
                current_button_state = GPIO.input(PIN_BUTTON)
                if current_button_state == GPIO.LOW and last_button_state == GPIO.HIGH:
                    cancel_detected = True
                    print("Shutdown CANCELLED by button press!")
                    break
                last_button_state = current_button_state
            except RuntimeError:
                break
            
            # Red
            GPIO.output(PIN_RED_LED, GPIO.LOW)
            GPIO.output(PIN_BLUE_LED, GPIO.HIGH)
            GPIO.output(PIN_GREEN_LED, GPIO.HIGH)
            time.sleep(chase_speed)
            
            # Check button again
            try:
                if GPIO.input(PIN_BUTTON) == GPIO.LOW:
                    cancel_detected = True
                    print("Shutdown CANCELLED by button press!")
                    break
            except RuntimeError:
                break
            
            # Blue
            GPIO.output(PIN_RED_LED, GPIO.HIGH)
            GPIO.output(PIN_BLUE_LED, GPIO.LOW)
            GPIO.output(PIN_GREEN_LED, GPIO.HIGH)
            time.sleep(chase_speed)
            
            # Check button again
            try:
                if GPIO.input(PIN_BUTTON) == GPIO.LOW:
                    cancel_detected = True
                    print("Shutdown CANCELLED by button press!")
                    break
            except RuntimeError:
                break
            
            # Green
            GPIO.output(PIN_RED_LED, GPIO.HIGH)
            GPIO.output(PIN_BLUE_LED, GPIO.HIGH)
            GPIO.output(PIN_GREEN_LED, GPIO.LOW)
            time.sleep(chase_speed)
            
            # Check button one more time
            try:
                if GPIO.input(PIN_BUTTON) == GPIO.LOW:
                    cancel_detected = True
                    print("Shutdown CANCELLED by button press!")
                    break
            except RuntimeError:
                break
        
        # Turn off all LEDs
        GPIO.output(PIN_RED_LED, GPIO.HIGH)
        GPIO.output(PIN_BLUE_LED, GPIO.HIGH)
        GPIO.output(PIN_GREEN_LED, GPIO.HIGH)
        
        if cancel_detected:
            # Celebratory flash - shutdown cancelled!
            for _ in range(3):
                GPIO.output(PIN_GREEN_LED, GPIO.LOW)
                time.sleep(0.1)
                GPIO.output(PIN_GREEN_LED, GPIO.HIGH)
                time.sleep(0.1)
            return False  # Don't shutdown
        else:
            return True  # Proceed with shutdown
    
    except RuntimeError:
        # GPIO error during countdown, treat as cancel
        print("GPIO error during countdown - treating as cancel")
        return False


def handle_button_press():
    """
    Handle shutdown button with enhanced visual feedback
    - Hold for 5 seconds with accelerating blink
    - Flash confirmation sequence
    - 5-second cancellable countdown with dance
    - Button press during countdown cancels shutdown
    """
    global shutdown_initiated
    
    # Debounce
    time.sleep(BUTTON_DEBOUNCE)
    if GPIO.input(PIN_BUTTON) == GPIO.HIGH:
        return  # False trigger
    
    print("Button pressed - monitoring hold duration...")
    
    start_time = time.time()
    hold_duration = 0
    last_feedback_time = start_time
    feedback_interval = 0.5  # Start with slow blink
    
    # Monitor button hold with visual feedback
    while GPIO.input(PIN_BUTTON) == GPIO.LOW:
        hold_duration = time.time() - start_time
        
        # Accelerate blink rate as hold time increases
        if hold_duration < 1.5:
            feedback_interval = 0.5   # Slow - 1 Hz
        elif hold_duration < 3.0:
            feedback_interval = 0.3   # Medium - ~1.7 Hz
        elif hold_duration < 4.5:
            feedback_interval = 0.15  # Fast - ~3.3 Hz
        else:
            feedback_interval = 0.08  # Very fast - ~6 Hz
        
        # Blink red LED (COMMON ANODE: toggle between LOW/HIGH)
        if time.time() - last_feedback_time >= feedback_interval:
            current_state = GPIO.input(PIN_RED_LED)
            GPIO.output(PIN_RED_LED, GPIO.LOW if current_state == GPIO.HIGH else GPIO.HIGH)
            last_feedback_time = time.time()
        
        # Check if hold threshold reached
        if hold_duration >= BUTTON_HOLD_TIME:
            print(f"Shutdown threshold reached ({BUTTON_HOLD_TIME}s)!")
            
            # Turn off feedback LED
            GPIO.output(PIN_RED_LED, GPIO.HIGH)
            
            # Wait for button release
            while GPIO.input(PIN_BUTTON) == GPIO.LOW:
                time.sleep(0.01)
            
            # Confirmation flash sequence
            shutdown_confirmation_flash()
            
            # Cancellable countdown
            should_shutdown = cancellable_countdown_dance()
            
            if should_shutdown:
                # Proceed with shutdown
                print("Shutdown confirmed - initiating system shutdown...")
                
                # Red LED solid during shutdown
                GPIO.output(PIN_RED_LED, GPIO.LOW)
                shutdown_initiated = True
                
                # Initiate shutdown
                subprocess.run(['sudo', 'shutdown', '-h', 'now'])
                
                # Keep red LED on until power cuts
                while True:
                    time.sleep(1)
            else:
                # Shutdown cancelled
                print("Shutdown cancelled - returning to normal operation")
                return
        
        time.sleep(0.01)  # Small delay to prevent CPU spinning
    
    # Button released before threshold
    if hold_duration < BUTTON_HOLD_TIME:
        print(f"Button released after {hold_duration:.1f}s - shutdown cancelled")
        GPIO.output(PIN_RED_LED, GPIO.HIGH)  # COMMON ANODE: HIGH = OFF


def boot_sequence():
    """
    Visual boot sequence - LED dance while system loads
    Chase pattern: Green → Blue → Red → repeat
    """
    print("Starting boot sequence - LED dance...")
    
    # Chase pattern - each LED lights in sequence
    chase_speed = 0.15  # seconds per LED
    num_chases = 8      # number of complete cycles
    
    for cycle in range(num_chases):
        # Green
        GPIO.output(PIN_GREEN_LED, GPIO.LOW)
        GPIO.output(PIN_BLUE_LED, GPIO.HIGH)
        GPIO.output(PIN_RED_LED, GPIO.HIGH)
        time.sleep(chase_speed)
        
        # Blue
        GPIO.output(PIN_GREEN_LED, GPIO.HIGH)
        GPIO.output(PIN_BLUE_LED, GPIO.LOW)
        GPIO.output(PIN_RED_LED, GPIO.HIGH)
        time.sleep(chase_speed)
        
        # Red
        GPIO.output(PIN_GREEN_LED, GPIO.HIGH)
        GPIO.output(PIN_BLUE_LED, GPIO.HIGH)
        GPIO.output(PIN_RED_LED, GPIO.LOW)
        time.sleep(chase_speed)
    
    # All off at end
    GPIO.output(PIN_GREEN_LED, GPIO.HIGH)
    GPIO.output(PIN_BLUE_LED, GPIO.HIGH)
    GPIO.output(PIN_RED_LED, GPIO.HIGH)
    
    print("Boot sequence complete - system ready!")


def signal_handler(sig, frame):
    """Handle graceful shutdown on SIGTERM/SIGINT"""
    print("\nReceived shutdown signal, cleaning up...")
    cleanup_gpio()
    sys.exit(0)


def main():
    """Main monitoring loop"""
    global shutdown_initiated
    
    # Register signal handlers
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    print("=" * 60)
    print("Pi GPIO Status Monitor Starting")
    print("=" * 60)
    
    try:
        setup_gpio()
        
        # Run boot sequence
        boot_sequence()
        
        print("Entering main monitoring loop...")
        print(f"Monitoring services: {SERVICE_LOGGER}, {SERVICE_DRIVE}")
        print(f"Button on GPIO {PIN_BUTTON} - hold {BUTTON_HOLD_TIME}s to shutdown")
        print(f"During shutdown countdown, press button to cancel")
        print("-" * 60)
        
        last_status_time = 0
        status_interval = 10  # Print status every 10 seconds
        
        while not shutdown_initiated:
            # Update LED states based on system conditions
            update_led_states()
            
            # Update physical LED outputs
            update_led_outputs()
            
            # Check for button press (with error handling)
            try:
                if GPIO.input(PIN_BUTTON) == GPIO.LOW:
                    handle_button_press()
                    # If shutdown was initiated, break out of loop
                    if shutdown_initiated:
                        break
            except RuntimeError:
                # GPIO was reset, likely during shutdown
                break
            
            # Periodic status logging
            if time.time() - last_status_time >= status_interval:
                logger_ok = check_service_status(SERVICE_LOGGER)
                drive_ok = check_service_status(SERVICE_DRIVE)
                wifi_ok = check_wifi_connected()
                newest_file = get_newest_data_file()
                
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Status: "
                      f"Logger={logger_ok} Drive={drive_ok} WiFi={wifi_ok} "
                      f"Data={'YES' if newest_file else 'NO'}")
                
                last_status_time = time.time()
            
            # Small sleep to prevent CPU spinning
            time.sleep(0.01)
    
    except KeyboardInterrupt:
        print("\nKeyboard interrupt received")
    
    except Exception as e:
        print(f"Error in main loop: {e}")
        import traceback
        traceback.print_exc()
    
    finally:
        cleanup_gpio()


if __name__ == '__main__':
    main()
