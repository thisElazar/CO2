#!/usr/bin/env python3
"""
Complete hardware test for LED status monitor
- Common anode RGB LEDs (3 separate LEDs)
- Shutdown/Wake button on GPIO 3

IMPORTANT: Common anode LEDs are INVERTED:
- GPIO LOW = LED ON (current flows through LED to ground)
- GPIO HIGH = LED OFF (no voltage difference)
"""

import RPi.GPIO as GPIO
import time

# GPIO pin assignments
PIN_GREEN = 17   # System Ready LED
PIN_BLUE = 27    # Data Capture LED
PIN_RED = 22     # Error/Warning LED
PIN_BUTTON = 3   # Shutdown/Wake button (Pin 5)

def setup():
    """Initialize GPIO"""
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    
    # Setup LED outputs
    GPIO.setup(PIN_GREEN, GPIO.OUT)
    GPIO.setup(PIN_BLUE, GPIO.OUT)
    GPIO.setup(PIN_RED, GPIO.OUT)
    
    # Setup button input with pull-up resistor
    # Button connects GPIO 3 to GND when pressed
    GPIO.setup(PIN_BUTTON, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    
    # Start with all OFF (HIGH for common anode)
    GPIO.output(PIN_GREEN, GPIO.HIGH)
    GPIO.output(PIN_BLUE, GPIO.HIGH)
    GPIO.output(PIN_RED, GPIO.HIGH)
    
    print("=" * 60)
    print("GPIO HARDWARE TEST - LEDs + Button")
    print("=" * 60)
    print(f"Green LED:  GPIO {PIN_GREEN} (Pin 11)")
    print(f"Blue LED:   GPIO {PIN_BLUE} (Pin 13)")
    print(f"Red LED:    GPIO {PIN_RED} (Pin 15)")
    print(f"Button:     GPIO {PIN_BUTTON} (Pin 5 to Pin 6/GND)")
    print()
    print("Common anode wiring: Pin 1 (3.3V) to all LED common anodes")
    print("Each color leg through 220Ω resistor to GPIO pin")
    print("=" * 60)
    print()

def led_on(pin):
    """Turn LED ON (set GPIO LOW for common anode)"""
    GPIO.output(pin, GPIO.LOW)

def led_off(pin):
    """Turn LED OFF (set GPIO HIGH for common anode)"""
    GPIO.output(pin, GPIO.HIGH)

def all_off():
    """Turn all LEDs OFF"""
    led_off(PIN_GREEN)
    led_off(PIN_BLUE)
    led_off(PIN_RED)

def test_individual_leds():
    """Test each LED individually"""
    print("=" * 50)
    print("Testing individual LEDs (2 seconds each)")
    print("=" * 50)
    
    # Green
    print("Green LED ON")
    led_on(PIN_GREEN)
    time.sleep(2)
    all_off()
    time.sleep(0.5)
    
    # Blue
    print("Blue LED ON")
    led_on(PIN_BLUE)
    time.sleep(2)
    all_off()
    time.sleep(0.5)
    
    # Red
    print("Red LED ON")
    led_on(PIN_RED)
    time.sleep(2)
    all_off()
    time.sleep(0.5)

def test_combinations():
    """Test LED combinations"""
    print("=" * 50)
    print("Testing combinations")
    print("=" * 50)
    
    # All on
    print("All LEDs ON (white)")
    led_on(PIN_GREEN)
    led_on(PIN_BLUE)
    led_on(PIN_RED)
    time.sleep(2)
    all_off()
    time.sleep(0.5)
    
    # Cyan (green + blue)
    print("Green + Blue (cyan)")
    led_on(PIN_GREEN)
    led_on(PIN_BLUE)
    time.sleep(2)
    all_off()
    time.sleep(0.5)
    
    # Magenta (red + blue)
    print("Red + Blue (magenta)")
    led_on(PIN_RED)
    led_on(PIN_BLUE)
    time.sleep(2)
    all_off()
    time.sleep(0.5)
    
    # Yellow (red + green)
    print("Red + Green (yellow)")
    led_on(PIN_RED)
    led_on(PIN_GREEN)
    time.sleep(2)
    all_off()

def test_button_basic():
    """Test basic button press detection"""
    print("=" * 50)
    print("Testing button - basic press detection")
    print("=" * 50)
    print("Press the button 5 times (green LED will light on each press)")
    print("Waiting for button presses...")
    print()
    
    press_count = 0
    last_state = GPIO.HIGH
    
    while press_count < 5:
        current_state = GPIO.input(PIN_BUTTON)
        
        # Detect button press (transition from HIGH to LOW)
        if current_state == GPIO.LOW and last_state == GPIO.HIGH:
            press_count += 1
            print(f"  Press #{press_count} detected!")
            
            # Light green LED while button is held
            led_on(PIN_GREEN)
            
            # Wait for release with debounce
            time.sleep(0.05)
            while GPIO.input(PIN_BUTTON) == GPIO.LOW:
                time.sleep(0.01)
            
            led_off(PIN_GREEN)
            time.sleep(0.1)  # Debounce after release
        
        last_state = current_state
        time.sleep(0.01)
    
    print("✓ Button press detection working!\n")

def test_button_hold():
    """Test button hold duration with visual feedback"""
    print("=" * 50)
    print("Testing button - hold duration detection")
    print("=" * 50)
    print("This simulates the shutdown sequence:")
    print("  Hold 0-1s: Red slow blink")
    print("  Hold 1-2s: Red medium blink")
    print("  Hold 2-3s: Red fast blink")
    print("  Hold 3s+:  All LEDs flash, then red solid")
    print()
    print("Press and HOLD the button for 3+ seconds...")
    print()
    
    # Wait for button press
    while GPIO.input(PIN_BUTTON) == GPIO.HIGH:
        time.sleep(0.01)
    
    print("Button pressed! Monitoring hold duration...")
    
    start_time = time.time()
    last_feedback_time = start_time
    feedback_interval = 0.5
    
    # Monitor hold duration
    while GPIO.input(PIN_BUTTON) == GPIO.LOW:
        hold_duration = time.time() - start_time
        
        # Accelerate blink rate based on hold time
        if hold_duration < 1.0:
            feedback_interval = 0.5   # Slow blink
            status = "SLOW"
        elif hold_duration < 2.0:
            feedback_interval = 0.25  # Medium blink
            status = "MEDIUM"
        else:
            feedback_interval = 0.125 # Fast blink
            status = "FAST"
        
        # Blink red LED
        if time.time() - last_feedback_time >= feedback_interval:
            current_led_state = GPIO.input(PIN_RED)
            GPIO.output(PIN_RED, GPIO.LOW if current_led_state == GPIO.HIGH else GPIO.HIGH)
            last_feedback_time = time.time()
        
        # Check for 3-second threshold
        if hold_duration >= 3.0:
            print(f"  Hold duration: {hold_duration:.1f}s - THRESHOLD REACHED!")
            
            # Flash all LEDs
            led_off(PIN_RED)
            time.sleep(0.1)
            for _ in range(3):
                led_on(PIN_GREEN)
                led_on(PIN_BLUE)
                led_on(PIN_RED)
                time.sleep(0.15)
                all_off()
                time.sleep(0.15)
            
            # Red solid (shutdown state)
            led_on(PIN_RED)
            print("  RED SOLID - simulated shutdown state")
            print("  (In actual use, system would shutdown now)")
            
            # Wait for button release
            while GPIO.input(PIN_BUTTON) == GPIO.LOW:
                time.sleep(0.01)
            
            all_off()
            time.sleep(0.5)
            
            print("✓ Shutdown sequence simulation complete!\n")
            return
        
        time.sleep(0.01)
    
    # Button released before 3 seconds
    hold_duration = time.time() - start_time
    print(f"  Hold duration: {hold_duration:.1f}s - released before threshold")
    print("  Shutdown cancelled (as expected)\n")
    all_off()

def test_button_interactive():
    """Interactive button test - press to cycle LEDs"""
    print("=" * 50)
    print("Interactive button test")
    print("=" * 50)
    print("Press button to cycle through LED states:")
    print("  1. Green ON")
    print("  2. Blue ON")
    print("  3. Red ON")
    print("  4. All OFF (back to start)")
    print()
    print("Press button 4 times to cycle through all states...")
    print()
    
    states = [
        ("Green ON", lambda: (led_on(PIN_GREEN), led_off(PIN_BLUE), led_off(PIN_RED))),
        ("Blue ON", lambda: (led_off(PIN_GREEN), led_on(PIN_BLUE), led_off(PIN_RED))),
        ("Red ON", lambda: (led_off(PIN_GREEN), led_off(PIN_BLUE), led_on(PIN_RED))),
        ("All OFF", lambda: all_off())
    ]
    
    state_index = 0
    last_button_state = GPIO.HIGH
    
    while state_index < len(states):
        current_button_state = GPIO.input(PIN_BUTTON)
        
        # Detect button press
        if current_button_state == GPIO.LOW and last_button_state == GPIO.HIGH:
            state_name, state_function = states[state_index]
            state_function()
            print(f"  State {state_index + 1}: {state_name}")
            state_index += 1
            
            # Wait for release
            time.sleep(0.05)
            while GPIO.input(PIN_BUTTON) == GPIO.LOW:
                time.sleep(0.01)
            time.sleep(0.1)
        
        last_button_state = current_button_state
        time.sleep(0.01)
    
    print("✓ Interactive test complete!\n")
    all_off()

def test_blink():
    """Test blinking patterns"""
    print("=" * 50)
    print("Testing blink patterns")
    print("=" * 50)
    
    # Slow blink (1 Hz)
    print("Green SLOW blink (1 Hz) - 5 cycles")
    for _ in range(5):
        led_on(PIN_GREEN)
        time.sleep(0.5)
        led_off(PIN_GREEN)
        time.sleep(0.5)
    
    time.sleep(0.5)
    
    # Fast blink (4 Hz)
    print("Blue FAST blink (4 Hz) - 10 cycles")
    for _ in range(10):
        led_on(PIN_BLUE)
        time.sleep(0.125)
        led_off(PIN_BLUE)
        time.sleep(0.125)

def test_system_ready_states():
    """Simulate the actual system states"""
    print("=" * 50)
    print("Simulating actual system states")
    print("=" * 50)
    
    # System ready (green solid)
    print("State: SYSTEM READY (green solid)")
    led_on(PIN_GREEN)
    time.sleep(3)
    all_off()
    time.sleep(1)
    
    # Degraded mode (green blink)
    print("State: NO WIFI (green blink)")
    for _ in range(5):
        led_on(PIN_GREEN)
        time.sleep(0.5)
        led_off(PIN_GREEN)
        time.sleep(0.5)
    time.sleep(1)
    
    # Active data capture (blue solid)
    print("State: ACTIVE DATA CAPTURE (blue solid)")
    led_on(PIN_BLUE)
    time.sleep(3)
    all_off()
    time.sleep(1)
    
    # Uploading (blue fast blink)
    print("State: UPLOADING TO DRIVE (blue fast blink)")
    for _ in range(8):
        led_on(PIN_BLUE)
        time.sleep(0.125)
        led_off(PIN_BLUE)
        time.sleep(0.125)
    time.sleep(1)
    
    # Warning (red slow blink)
    print("State: WARNING (red slow blink)")
    for _ in range(5):
        led_on(PIN_RED)
        time.sleep(0.5)
        led_off(PIN_RED)
        time.sleep(0.5)
    time.sleep(1)
    
    # Critical (red fast blink)
    print("State: CRITICAL ERROR (red fast blink)")
    for _ in range(8):
        led_on(PIN_RED)
        time.sleep(0.125)
        led_off(PIN_RED)
        time.sleep(0.125)
    time.sleep(1)
    
    # Shutdown (red solid)
    print("State: SHUTTING DOWN (red solid)")
    led_on(PIN_RED)
    time.sleep(3)
    all_off()

def cleanup():
    """Clean up GPIO"""
    all_off()
    GPIO.cleanup()
    print("\nGPIO cleaned up - all LEDs off")

def main():
    """Run all tests"""
    try:
        setup()
        
        print("\nStarting hardware tests...")
        print("Press Ctrl+C at any time to stop\n")
        time.sleep(2)
        
        # LED Tests
        test_individual_leds()
        time.sleep(1)
        
        # Skip combination test note
        print("=" * 50)
        print("Skipping LED combination tests")
        print("(You're using 3 separate LEDs, not 1 RGB)")
        print("=" * 50)
        print()
        time.sleep(1)
        
        test_blink()
        time.sleep(1)
        
        test_system_ready_states()
        time.sleep(2)
        
        # Button Tests
        print("\n" + "=" * 60)
        print("BUTTON TESTS - Starting in 3 seconds...")
        print("=" * 60)
        time.sleep(3)
        
        test_button_basic()
        time.sleep(1)
        
        test_button_hold()
        time.sleep(1)
        
        test_button_interactive()
        time.sleep(1)
        
        # Final confirmation
        print("\n" + "=" * 60)
        print("ALL TESTS COMPLETE! 🎉")
        print("=" * 60)
        print()
        print("Hardware validation summary:")
        print("  ✓ Green LED - System Ready indicator")
        print("  ✓ Blue LED - Data Capture indicator")
        print("  ✓ Red LED - Error/Warning indicator")
        print("  ✓ Button press detection")
        print("  ✓ Button hold duration timing")
        print("  ✓ Shutdown sequence simulation")
        print()
        print("Your hardware is ready for the full service!")
        print("Next step: Upload pi_status_monitor.py and configure systemd")
        print()
        
    except KeyboardInterrupt:
        print("\n\nTest interrupted by user")
    
    finally:
        cleanup()

if __name__ == '__main__':
    main()
