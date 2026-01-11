/*
 * CO2 Monitor - Rotary Encoder Edition v3.1
 * 
 * FIXES IN v3.1:
 * - Removed LOGGING_START message (Python detects first DATA line)
 * - Changed LOGGING_STOP to STOP (Python compatibility)
 * - Fixed metadata format for 'M' command: META:type=control,location=outdoor
 * - Added DATA transmission every second during LOGGING
 * - Faster LED color tracking (smoothing /3 instead of /10)
 * - Added THROWAWAY experiment type for debugging (no metadata collection)
 */

#include <Wire.h>
#include <LiquidCrystal_I2C.h>
#include <SoftwareSerial.h>
#include "MHZ19.h"

// ===== HARDWARE =====
SoftwareSerial sensor1Serial(2, 4);
SoftwareSerial sensor2Serial(7, 8);
MHZ19 sensor1;
MHZ19 sensor2;
LiquidCrystal_I2C lcd(0x27, 20, 4);

// LED Pins
const int LED1_RED = 3, LED1_GREEN = 5, LED1_BLUE = 6;
const int LED2_RED = 9, LED2_GREEN = 10, LED2_BLUE = 11;
const int RECORDING_LED = 13;

// Rotary Encoder Pins
const int ENCODER_CLK = A1;
const int ENCODER_DT = A2;
const int ENCODER_SW = A3;

// ===== STATE MACHINE =====
enum State {
  MODE_SELECT,
  EXP_TYPE_SELECT,
  CONTROL_LOCATION,
  TEST_TYPE_SELECT,
  DURATION_SELECT,
  LIVE_READING,
  LOGGING,
  CALIBRATION_INSTRUCT,
  CALIBRATING,
  EXPERIMENT_COMPLETE
};

State currentState = LIVE_READING;
State previousState = LIVE_READING;

// ===== EXPERIMENT METADATA =====
enum ExperimentType { CONTROL, TEST, THROWAWAY };
ExperimentType expType = CONTROL;

enum ControlLocation { INDOORS, WINDOW, OUTDOORS };
ControlLocation controlLoc = OUTDOORS;

enum TestType { HUMAN_BREATH, PLANT_PHOTOSYNTHESIS, VARIED_CONDITIONS };
TestType testType = PLANT_PHOTOSYNTHESIS;

// ===== EXPERIMENT STATISTICS =====
int startCO2_1 = 0, startCO2_2 = 0;
int endCO2_1 = 0, endCO2_2 = 0;
int startDelta = 0, endDelta = 0;
unsigned long actualDuration = 0;

// ===== ROTARY ENCODER CLASS =====
class RotaryEncoder {
  private:
    int clkPin, dtPin;
    int lastCLK;
    int position;
    int minPos, maxPos;
    bool wrapEnabled;
    
  public:
    RotaryEncoder(int clk, int dt) : clkPin(clk), dtPin(dt), position(0), minPos(0), maxPos(100), wrapEnabled(true) {
      pinMode(clkPin, INPUT_PULLUP);
      pinMode(dtPin, INPUT_PULLUP);
      lastCLK = digitalRead(clkPin);
    }
    
    void setRange(int min, int max, bool wrap = true) {
      minPos = min;
      maxPos = max;
      wrapEnabled = wrap;
      
      if (position < minPos) position = wrapEnabled ? maxPos : minPos;
      if (position > maxPos) position = wrapEnabled ? minPos : maxPos;
    }
    
    void setPosition(int pos) {
      position = pos;
      if (position < minPos) position = wrapEnabled ? maxPos : minPos;
      if (position > maxPos) position = wrapEnabled ? minPos : maxPos;
    }
    
    int getPosition() {
      return position;
    }
    
    void update() {
      int currentCLK = digitalRead(clkPin);
      
      if (currentCLK != lastCLK && currentCLK == LOW) {
        if (digitalRead(dtPin) == HIGH) {
          position++;
        } else {
          position--;
        }
        
        if (wrapEnabled) {
          if (position > maxPos) position = minPos;
          if (position < minPos) position = maxPos;
        } else {
          position = constrain(position, minPos, maxPos);
        }
      }
      
      lastCLK = currentCLK;
    }
};

RotaryEncoder encoder(ENCODER_CLK, ENCODER_DT);

// ===== BUTTON HANDLING =====
class Button {
  private:
    int pin;
    bool lastReading;
    bool stableState;
    bool currentlyPressed;
    unsigned long lastStateChangeTime;
    unsigned long pressStartTime;
    const unsigned long debounceDelay = 50;
    const unsigned long minPressTime = 50;
    const unsigned long longPressTime = 1500;
    bool longPressTriggered;
    bool pressAcknowledged;
    
  public:
    Button(int p) : pin(p), lastReading(HIGH), stableState(HIGH), currentlyPressed(false), 
                    lastStateChangeTime(0), pressStartTime(0), longPressTriggered(false),
                    pressAcknowledged(false) {
      pinMode(pin, INPUT_PULLUP);
    }
    
    void update() {
      bool reading = digitalRead(pin);
      
      if (reading != lastReading) {
        lastStateChangeTime = millis();
      }
      lastReading = reading;
      
      if (millis() - lastStateChangeTime > debounceDelay) {
        if (reading != stableState) {
          stableState = reading;
          
          if (stableState == LOW && !currentlyPressed) {
            currentlyPressed = true;
            pressStartTime = millis();
            longPressTriggered = false;
            pressAcknowledged = false;
          }
          
          if (stableState == HIGH && currentlyPressed) {
            currentlyPressed = false;
          }
        }
      }
    }
    
    bool wasPressed() {
      update();
      
      if (!currentlyPressed && !pressAcknowledged && pressStartTime > 0) {
        unsigned long pressDuration = millis() - pressStartTime;
        
        if (pressDuration >= minPressTime && pressDuration < longPressTime && !longPressTriggered) {
          pressAcknowledged = true;
          pressStartTime = 0;
          return true;
        }
        
        if (pressDuration < minPressTime || longPressTriggered) {
          pressAcknowledged = true;
          pressStartTime = 0;
        }
      }
      
      return false;
    }
    
    bool isLongPressing() {
      update();
      if (currentlyPressed && !longPressTriggered) {
        if (millis() - pressStartTime >= longPressTime) {
          return true;
        }
      }
      return false;
    }
    
    bool wasLongPressed() {
      update();
      if (currentlyPressed && !longPressTriggered) {
        if (millis() - pressStartTime >= longPressTime) {
          longPressTriggered = true;
          pressAcknowledged = true;
          return true;
        }
      }
      return false;
    }
};

Button button(ENCODER_SW);

// ===== TIMING =====
unsigned long lastSensorRead = 0;
unsigned long lastDisplayUpdate = 0;
unsigned long experimentStartTime = 0;
unsigned long experimentDuration = 0;

const unsigned long SENSOR_INTERVAL = 1000;
const unsigned long DISPLAY_INTERVAL = 500;

bool needsFullRedraw = true;
State lastDisplayedState = LIVE_READING;
int lastMenuSelection = -1;

// ===== SENSOR DATA =====
int co2_1 = 0, temp_1 = 0;
int co2_2 = 0, temp_2 = 0;

bool sensor1_error = false;
bool sensor2_error = false;

// ===== LED STATE =====
int led1_r = 255, led1_g = 255, led1_b = 255;
int led2_r = 255, led2_g = 255, led2_b = 255;

// ===== DURATION OPTIONS =====
const int DURATION_OPTIONS[] = {
  1, 2, 3, 4, 5, 6, 7, 8, 9, 10,           // 1-min increments
  15, 20, 25, 30,                           // 5-min increments
  40, 50, 60,                               // 10-min increments
  90, 120, 150, 180,                        // 30-min increments
  240, 300, 360,                            // 1-hour increments
  480, 600, 720                             // 2-hour increments
};
const int NUM_DURATIONS = sizeof(DURATION_OPTIONS) / sizeof(DURATION_OPTIONS[0]);

// ===== SELECTION FUNCTIONS =====
int getSelectedMode() {
  encoder.setRange(0, 2, true);
  return encoder.getPosition();
}

int getSelectedExpType() {
  encoder.setRange(0, 2, true);  // Now 3 options: Control, Test, Throwaway
  return encoder.getPosition();
}

int getSelectedControlLocation() {
  encoder.setRange(0, 2, true);
  return encoder.getPosition();
}

int getSelectedTestType() {
  encoder.setRange(0, 2, true);
  return encoder.getPosition();
}

int getSelectedDuration() {
  encoder.setRange(0, NUM_DURATIONS - 1, false);
  int pos = encoder.getPosition();
  return DURATION_OPTIONS[pos];
}

// ===== SETUP =====
void setup() {
  Serial.begin(9600);
  
  lcd.init();
  lcd.backlight();
  lcd.clear();
  lcd.print(F("CO2 Monitor"));
  lcd.setCursor(0, 1);
  lcd.print(F("by thisElazar"));
  lcd.setCursor(0, 2);
  lcd.print(F("Initializing..."));
  
  sensor1Serial.begin(9600);
  sensor2Serial.begin(9600);
  sensor1.begin(sensor1Serial);
  sensor2.begin(sensor2Serial);
  sensor1.autoCalibration(false);
  sensor2.autoCalibration(false);
  
  pinMode(LED1_RED, OUTPUT);
  pinMode(LED1_GREEN, OUTPUT);
  pinMode(LED1_BLUE, OUTPUT);
  pinMode(LED2_RED, OUTPUT);
  pinMode(LED2_GREEN, OUTPUT);
  pinMode(LED2_BLUE, OUTPUT);
  pinMode(RECORDING_LED, OUTPUT);
  
  setLED(1, 255, 255, 255);
  setLED(2, 255, 255, 255);

  rainbowDance();

  digitalWrite(RECORDING_LED, LOW);
  
  delay(2000);
  lcd.clear();
  
  Serial.println(F("READY"));
}

void loop() {
  encoder.update();
  
  if (millis() - lastSensorRead >= SENSOR_INTERVAL) {
    readSensors();
    lastSensorRead = millis();
  }
  
  updateLEDs();
  handleState();
  
  if (millis() - lastDisplayUpdate >= DISPLAY_INTERVAL) {
    updateDisplay();
    lastDisplayUpdate = millis();
  }
  
  handleSerialCommands();
}

void readSensors() {
  sensor1Serial.listen();
  delay(50);
  co2_1 = sensor1.getCO2();
  temp_1 = sensor1.getTemperature();
  
  // Check for sensor 1 disconnect
  if (temp_1 < -10 || temp_1 > 60 || co2_1 < 0) {
    sensor1_error = true;
  } else {
    sensor1_error = false;
  }
  
  sensor2Serial.listen();
  delay(50);
  co2_2 = sensor2.getCO2();
  temp_2 = sensor2.getTemperature();
  
  // Check for sensor 2 disconnect
  if (temp_2 < -10 || temp_2 > 60 || co2_2 < 0) {
    sensor2_error = true;
  } else {
    sensor2_error = false;
  }
}

void handleState() {
  switch (currentState) {
    case MODE_SELECT:
      if (button.wasPressed()) {
        int selection = getSelectedMode();
        if (selection == 0) {
          currentState = LIVE_READING;
          encoder.setPosition(0);
          needsFullRedraw = true;
        }
        else if (selection == 1) {
          currentState = EXP_TYPE_SELECT;
          encoder.setPosition(0);
          needsFullRedraw = true;
        }
        else {
          currentState = CALIBRATION_INSTRUCT;
          needsFullRedraw = true;
        }
      }
      if (button.wasLongPressed()) {
        currentState = LIVE_READING;
        encoder.setPosition(0);
        needsFullRedraw = true;
      }
      break;
      
    case EXP_TYPE_SELECT:
      if (button.wasPressed()) {
        int selection = getSelectedExpType();
        if (selection == 0) {
          expType = CONTROL;
          currentState = CONTROL_LOCATION;
          encoder.setPosition(static_cast<int>(controlLoc));
        } else if (selection == 1) {
          expType = TEST;
          currentState = TEST_TYPE_SELECT;
          encoder.setPosition(static_cast<int>(testType));
        } else {
          expType = THROWAWAY;
          // Skip metadata - go straight to duration
          currentState = DURATION_SELECT;
          encoder.setPosition(50);
        }
        needsFullRedraw = true;
      }
      if (button.wasLongPressed()) {
        currentState = MODE_SELECT;
        encoder.setPosition(0);
        needsFullRedraw = true;
      }
      break;
      
    case CONTROL_LOCATION:
      if (button.wasPressed()) {
        int selection = getSelectedControlLocation();
        controlLoc = static_cast<ControlLocation>(selection);
        currentState = DURATION_SELECT;
        encoder.setPosition(50);
        needsFullRedraw = true;
      }
      if (button.wasLongPressed()) {
        currentState = EXP_TYPE_SELECT;
        encoder.setPosition(0);
        needsFullRedraw = true;
      }
      break;
      
    case TEST_TYPE_SELECT:
      if (button.wasPressed()) {
        int selection = getSelectedTestType();
        testType = static_cast<TestType>(selection);
        currentState = DURATION_SELECT;
        encoder.setPosition(50);
        needsFullRedraw = true;
      }
      if (button.wasLongPressed()) {
        currentState = EXP_TYPE_SELECT;
        encoder.setPosition(1);
        needsFullRedraw = true;
      }
      break;
      
    case DURATION_SELECT:
      if (button.wasPressed()) {
        int duration = getSelectedDuration();
        experimentDuration = (unsigned long)duration * 60 * 1000;
        
        startCO2_1 = co2_1;
        startCO2_2 = co2_2;
        startDelta = co2_1 - co2_2;
        experimentStartTime = millis();
        
        currentState = LOGGING;
        needsFullRedraw = true;
      }
      if (button.wasLongPressed()) {
        if (expType == CONTROL) {
          currentState = CONTROL_LOCATION;
          encoder.setPosition(static_cast<int>(controlLoc));
        } else if (expType == TEST) {
          currentState = TEST_TYPE_SELECT;
          encoder.setPosition(static_cast<int>(testType));
        } else {
          // Throwaway - go back to experiment type
          currentState = EXP_TYPE_SELECT;
          encoder.setPosition(2);
        }
        needsFullRedraw = true;
      }
      break;
      
    case LIVE_READING:
      if (button.wasPressed()) {
        currentState = MODE_SELECT;
        encoder.setPosition(0);
        needsFullRedraw = true;
      }
      break;
      
    case LOGGING:
      {
        digitalWrite(RECORDING_LED, HIGH);
        
        unsigned long elapsed = millis() - experimentStartTime;
        unsigned long elapsedSeconds = elapsed / 1000;
        
        // Send data every second
        static unsigned long lastDataSend = 0;
        if (millis() - lastDataSend >= 1000) {
          Serial.print(F("DATA,"));
          Serial.print(elapsedSeconds);
          Serial.print(F(","));
          Serial.print(co2_1);
          Serial.print(F(","));
          Serial.print(temp_1);
          Serial.print(F(","));
          Serial.print(co2_2);
          Serial.print(F(","));
          Serial.println(temp_2);
          
          lastDataSend = millis();
        }
        
        if (elapsed >= experimentDuration) {
          endCO2_1 = co2_1;
          endCO2_2 = co2_2;
          endDelta = co2_1 - co2_2;
          actualDuration = elapsed;
          
          
          digitalWrite(RECORDING_LED, LOW);
          Serial.println(F("STOP"));
           rainbowDance();
          currentState = EXPERIMENT_COMPLETE;
          needsFullRedraw = true;
        }
        
        if (button.wasPressed()) {
  endCO2_1 = co2_1;
  endCO2_2 = co2_2;
  endDelta = co2_1 - co2_2;
  actualDuration = elapsed;
  
  digitalWrite(RECORDING_LED, LOW);
  Serial.println(F("STOP"));
  
  rainbowDance(); 
  
  currentState = EXPERIMENT_COMPLETE;
  needsFullRedraw = true;
}
      }
      break;
      
    case CALIBRATION_INSTRUCT:
  if (button.wasPressed()) {
    currentState = CALIBRATING;
    lcd.clear();
    lcd.print(F("Calibrating..."));
    lcd.setCursor(0, 1);
    lcd.print(F("Please wait 10 sec"));
    
    sensor1Serial.listen();
    delay(100);
    sensor1.calibrate();
    
    sensor2Serial.listen();
    delay(100);
    sensor2.calibrate();
    
    // Rainbow dance during calibration wait (~10 seconds)
    unsigned long calibStart = millis();
    while (millis() - calibStart < 10000) {
      rainbowDance();
    }
    
    lcd.clear();
    lcd.print(F("Calibration"));
    lcd.setCursor(0, 1);
    lcd.print(F("Complete!"));
    
    rainbowDance();
    delay(2000);
    
    currentState = LIVE_READING;
    encoder.setPosition(0);
    needsFullRedraw = true;
  }
      break;
      
    case EXPERIMENT_COMPLETE:
      if (button.wasPressed()) {
        currentState = LIVE_READING;
        encoder.setPosition(0);
        needsFullRedraw = true;
      }
      break;
  }
}

void handleSerialCommands() {
  if (Serial.available()) {
    char cmd = Serial.read();
    
    if (cmd == 'R' || cmd == 'r') {
      sensor1Serial.listen();
      delay(50);
      int co2_1_read = sensor1.getCO2();
      int temp_1_read = sensor1.getTemperature();
      
      sensor2Serial.listen();
      delay(50);
      int co2_2_read = sensor2.getCO2();
      int temp_2_read = sensor2.getTemperature();
      
      Serial.print(F("Sensor 1:  "));
      Serial.print(co2_1_read);
      Serial.print(F(" ppm,  "));
      Serial.print(temp_1_read);
      Serial.println(F(" C"));
      
      Serial.print(F("Sensor 2:  "));
      Serial.print(co2_2_read);
      Serial.print(F(" ppm,  "));
      Serial.print(temp_2_read);
      Serial.println(F(" C"));
      
      Serial.print(F("Delta:     "));
      int delta = co2_1_read - co2_2_read;
      if (delta >= 0) Serial.print("+");
      Serial.print(delta);
      Serial.println(F(" ppm"));
    }
    else if (cmd == 'M' || cmd == 'm') {
      // Send metadata in Python-compatible format
      Serial.print(F("META:type="));
      
      if (expType == CONTROL) {
        Serial.print(F("control,location="));
        if (controlLoc == INDOORS) Serial.println(F("indoor"));
        else if (controlLoc == WINDOW) Serial.println(F("window"));
        else Serial.println(F("outdoor"));
      } 
      else if (expType == TEST) {
        Serial.print(F("test,test_subtype="));
        if (testType == HUMAN_BREATH) Serial.println(F("breath"));
        else if (testType == PLANT_PHOTOSYNTHESIS) Serial.println(F("photosynthesis"));
        else Serial.println(F("varied"));
      }
      else {
        // THROWAWAY type
        Serial.println(F("throwaway"));
      }
    }
  }
}

void updateDisplay() {
  if (currentState != lastDisplayedState) {
    lcd.clear();
    lastDisplayedState = currentState;
    needsFullRedraw = true;
  }
  
  switch (currentState) {
    case MODE_SELECT:
      {
        int selection = getSelectedMode();
        
        if (needsFullRedraw) {
          lcd.setCursor(0, 0);
          lcd.print(F("SELECT MODE:"));
          lcd.setCursor(1, 1);
          lcd.print(F(" Live Reading"));
          lcd.setCursor(1, 2);
          lcd.print(F(" Logging"));
          lcd.setCursor(1, 3);
          lcd.print(F(" Calibrate"));
          needsFullRedraw = false;
          lastMenuSelection = -1;
        }
        
        if (selection != lastMenuSelection) {
          lcd.setCursor(0, 1);
          lcd.print(F(" "));
          lcd.setCursor(0, 2);
          lcd.print(F(" "));
          lcd.setCursor(0, 3);
          lcd.print(F(" "));
          
          lcd.setCursor(0, selection + 1);
          lcd.print(F(">"));
          
          lastMenuSelection = selection;
        }
      }
      break;
      
    case EXP_TYPE_SELECT:
      {
        int selection = getSelectedExpType();
        
        if (needsFullRedraw) {
          lcd.setCursor(0, 0);
          lcd.print(F("EXPERIMENT TYPE:"));
          lcd.setCursor(1, 1);
          lcd.print(F(" Control"));
          lcd.setCursor(1, 2);
          lcd.print(F(" Test"));
          lcd.setCursor(1, 3);
          lcd.print(F(" Throwaway"));
          needsFullRedraw = false;
          lastMenuSelection = -1;
        }
        
        if (selection != lastMenuSelection) {
          lcd.setCursor(0, 1);
          lcd.print(selection == 0 ? F(">") : F(" "));
          lcd.setCursor(0, 2);
          lcd.print(selection == 1 ? F(">") : F(" "));
          lcd.setCursor(0, 3);
          lcd.print(selection == 2 ? F(">") : F(" "));
          lastMenuSelection = selection;
        }
      }
      break;
      
    case CONTROL_LOCATION:
      {
        int selection = getSelectedControlLocation();
        
        if (needsFullRedraw) {
          lcd.setCursor(0, 0);
          lcd.print(F("CONTROL LOCATION:"));
          lcd.setCursor(1, 1);
          lcd.print(F(" Indoors"));
          lcd.setCursor(1, 2);
          lcd.print(F(" Window"));
          lcd.setCursor(1, 3);
          lcd.print(F(" Outdoors"));
          needsFullRedraw = false;
          lastMenuSelection = -1;
        }
        
        if (selection != lastMenuSelection) {
          lcd.setCursor(0, 1);
          lcd.print(selection == 0 ? F(">") : F(" "));
          lcd.setCursor(0, 2);
          lcd.print(selection == 1 ? F(">") : F(" "));
          lcd.setCursor(0, 3);
          lcd.print(selection == 2 ? F(">") : F(" "));
          lastMenuSelection = selection;
        }
      }
      break;
      
    case TEST_TYPE_SELECT:
      {
        int selection = getSelectedTestType();
        
        if (needsFullRedraw) {
          lcd.setCursor(0, 0);
          lcd.print(F("TEST TYPE:"));
          lcd.setCursor(1, 1);
          lcd.print(F(" Human Breath"));
          lcd.setCursor(1, 2);
          lcd.print(F(" Plant Photosyn."));
          lcd.setCursor(1, 3);
          lcd.print(F(" Varied Cond."));
          needsFullRedraw = false;
          lastMenuSelection = -1;
        }
        
        if (selection != lastMenuSelection) {
          lcd.setCursor(0, 1);
          lcd.print(selection == 0 ? F(">") : F(" "));
          lcd.setCursor(0, 2);
          lcd.print(selection == 1 ? F(">") : F(" "));
          lcd.setCursor(0, 3);
          lcd.print(selection == 2 ? F(">") : F(" "));
          lastMenuSelection = selection;
        }
      }
      break;
      
    case DURATION_SELECT:
      {
        static int lastDuration = -1;
        int duration = getSelectedDuration();
        
        if (needsFullRedraw) {
          lcd.setCursor(0, 0);
          lcd.print(F("LOGGING MODE"));
          lcd.setCursor(0, 1);
          lcd.print(F("Set Duration:"));
          lcd.setCursor(0, 3);
          lcd.print(F("[Press to start]"));
          needsFullRedraw = false;
          lastDuration = -1;
        }
        
        if (duration != lastDuration) {
          lcd.setCursor(0, 2);
          lcd.print(F("  >>>             "));
          lcd.setCursor(6, 2);
          
          if (duration >= 60) {
            int hours = duration / 60;
            int mins = duration % 60;
            lcd.print(hours);
            lcd.print(F("h"));
            if (mins > 0) {
              lcd.print(F(" "));
              lcd.print(mins);
              lcd.print(F("m"));
            }
          } else {
            lcd.print(duration);
            lcd.print(F(" min"));
          }
          lcd.print(F(" <<<"));
          
          lastDuration = duration;
        }
      }
      break;
      
    case LIVE_READING:
      {
        if (needsFullRedraw) {
          lcd.setCursor(0, 0);
          lcd.print(F("LIVE READING        "));
          lcd.setCursor(0, 1);
          lcd.print(F("Ctl:                "));
          lcd.setCursor(0, 2);
          lcd.print(F("Trt:                "));
          lcd.setCursor(0, 3);
          lcd.print(F("D:     [Press:Menu] "));
          needsFullRedraw = false;
        }
        
        lcd.setCursor(5, 1);
        lcd.print(co2_2);
        lcd.print(F(" ppm "));
        lcd.print(temp_2);
        lcd.print(F("C  "));
        
        lcd.setCursor(5, 2);
        lcd.print(co2_1);
        lcd.print(F(" ppm "));
        lcd.print(temp_1);
        lcd.print(F("C  "));
        
        lcd.setCursor(2, 3);
        lcd.print(F("     "));
        lcd.setCursor(2, 3);
        int delta = co2_1 - co2_2;
        if (delta >= 0) lcd.print(F("+"));
        lcd.print(delta);
      }
      break;
      
    case LOGGING:
      {
        if (needsFullRedraw) {
          lcd.setCursor(0, 1);
          lcd.print(F("Ctl:                "));
          lcd.setCursor(0, 2);
          lcd.print(F("Trt:                "));
          lcd.setCursor(0, 3);
          lcd.print(F("D:     [Press:Stop] "));
          needsFullRedraw = false;
        }
        
        unsigned long elapsed = millis() - experimentStartTime;
        unsigned long elapsedSec = elapsed / 1000;
        unsigned long totalSec = experimentDuration / 1000;
        
        int elapsedHours = elapsedSec / 3600;
        int elapsedMins = (elapsedSec % 3600) / 60;
        int elapsedSecs = elapsedSec % 60;
        
        int totalHours = totalSec / 3600;
        int totalMins = (totalSec % 3600) / 60;
        
        lcd.setCursor(0, 0);
        lcd.print(F("LOG:                "));
        lcd.setCursor(5, 0);
        
        if (elapsedHours > 0) {
          lcd.print(elapsedHours);
          lcd.print(F(":"));
          if (elapsedMins < 10) lcd.print(F("0"));
          lcd.print(elapsedMins);
        } else {
          if (elapsedMins < 10) lcd.print(F("0"));
          lcd.print(elapsedMins);
          lcd.print(F(":"));
          if (elapsedSecs < 10) lcd.print(F("0"));
          lcd.print(elapsedSecs);
        }
        
        lcd.print(F("/"));
        
        if (totalHours > 0) {
          lcd.print(totalHours);
          lcd.print(F(":"));
          if (totalMins < 10) lcd.print(F("0"));
          lcd.print(totalMins);
        } else {
          lcd.print(totalMins);
          lcd.print(F("m"));
        }
        lcd.print(F("  "));
        
        lcd.setCursor(5, 1);
        lcd.print(co2_2);
        lcd.print(F(" ppm    "));
        
        lcd.setCursor(5, 2);
        lcd.print(co2_1);
        lcd.print(F(" ppm    "));
        
        lcd.setCursor(2, 3);
        lcd.print(F("     "));
        lcd.setCursor(2, 3);
        int delta = co2_1 - co2_2;
        if (delta >= 0) lcd.print(F("+"));
        lcd.print(delta);
      }
      break;
      
    case CALIBRATION_INSTRUCT:
      lcd.setCursor(0, 0);
      lcd.print(F("CALIBRATION MODE    "));
      lcd.setCursor(0, 1);
      lcd.print(F("Take outside to     "));
      lcd.setCursor(0, 2);
      lcd.print(F("fresh air, then     "));
      lcd.setCursor(0, 3);
      lcd.print(F("[Press when ready]  "));
      break;
      
    case CALIBRATING:
      break;
      
    case EXPERIMENT_COMPLETE:
      {
        lcd.setCursor(0, 0);
        lcd.print(F("EXPERIMENT COMPLETE"));
        
        int deltaCO2_1 = endCO2_1 - startCO2_1;
        int deltaCO2_2 = endCO2_2 - startCO2_2;
        int deltaChange = endDelta - startDelta;
        
        lcd.setCursor(0, 1);
        lcd.print(F("Ctl: "));
        if (deltaCO2_2 >= 0) lcd.print(F("+"));
        lcd.print(deltaCO2_2);
        lcd.print(F(" ppm   "));
        
        lcd.setCursor(0, 2);
        lcd.print(F("Trt: "));
        if (deltaCO2_1 >= 0) lcd.print(F("+"));
        lcd.print(deltaCO2_1);
        lcd.print(F(" ppm   "));
        
        lcd.setCursor(0, 3);
        lcd.print(F("D:"));
        if (deltaChange >= 0) lcd.print(F("+"));
        lcd.print(deltaChange);
        lcd.print(F(" [Press:OK]"));
      }
      break;
  }
  
  if (button.isLongPressing()) {
    lcd.setCursor(0, 3);
    lcd.print(F("[HOLD:BACK]         "));
  }
}

void rainbowDance() {
  for (int cycle = 0; cycle < 5; cycle++) {  // 2 full rainbow cycles
    for (int hue = 0; hue < 360; hue += 15) {  // Step through colors
      int r, g, b;
      
      // Simple HSV to RGB conversion
      if (hue < 60) {
        r = 255; g = hue * 255 / 60; b = 0;
      } else if (hue < 120) {
        r = (120 - hue) * 255 / 60; g = 255; b = 0;
      } else if (hue < 180) {
        r = 0; g = 255; b = (hue - 120) * 255 / 60;
      } else if (hue < 240) {
        r = 0; g = (240 - hue) * 255 / 60; b = 255;
      } else if (hue < 300) {
        r = (hue - 240) * 255 / 60; g = 0; b = 255;
      } else {
        r = 255; g = 0; b = (360 - hue) * 255 / 60;
      }
      
      setLED(1, r, g, b);
      setLED(2, r, g, b);
      delay(20);  // Animation speed
    }
  }
}

void updateLEDs() {
  updateSingleLED(1, co2_1);
  updateSingleLED(2, co2_2);
}

void updateSingleLED(int num, int co2) {
    if ((num == 1 && sensor1_error) || (num == 2 && sensor2_error)) {
    setLED(num, 255, 255, 255);  // White = disconnected
    return;
  }
  
  int r, g, b;
  
  if (co2 <= 0) {
    r = 255; g = 255; b = 200;
  }
  else if (co2 < 400) {
    int brightness = map(co2, 0, 400, 150, 0);
    r = 255;
    g = 255;
    b = constrain(brightness, 0, 255);
  }
  else if (co2 == 400) {
    r = 255; g = 255; b = 0;
  }
  else if (co2 < 550) {
    int progress = map(co2, 400, 550, 0, 255);
    r = 255;
    g = 255 - progress;
    b = progress;
  }
  else if (co2 < 700) {
    int progress = map(co2, 550, 700, 0, 255);
    r = 255 - progress;
    g = 0;
    b = 255;
  }
  else if (co2 == 700) {
    r = 0; g = 0; b = 255;
  }
  else if (co2 < 850) {
    int progress = map(co2, 700, 850, 0, 255);
    r = 0;
    g = progress / 2;
    b = 255;
  }
  else if (co2 < 1000) {
    int progress = map(co2, 850, 1000, 0, 255);
    r = 0;
    g = 127 + (progress / 2);
    b = 255;
  }
  else {
    r = 0; g = 255; b = 255;
  }
  
  // Faster tracking - /3 instead of /10
  if (num == 1) {
    led1_r += (r - led1_r) / 3;
    led1_g += (g - led1_g) / 3;
    led1_b += (b - led1_b) / 3;
    setLED(1, led1_r, led1_g, led1_b);
  } else {
    led2_r += (r - led2_r) / 3;
    led2_g += (g - led2_g) / 3;
    led2_b += (b - led2_b) / 3;
    setLED(2, led2_r, led2_g, led2_b);
  }
}

void setLED(int num, int r, int g, int b) {
  if (num == 1) {
    analogWrite(LED1_RED, r);
    analogWrite(LED1_GREEN, g);
    analogWrite(LED1_BLUE, b);
  } else {
    analogWrite(LED2_RED, r);
    analogWrite(LED2_GREEN, g);
    analogWrite(LED2_BLUE, b);
  }
}
