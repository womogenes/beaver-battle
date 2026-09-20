// Identity blink bench test, for testing camera detection without ESP-IDF.
//
// The laser blinks this controller's identity pattern only while FIRE is held, and is off
// at every other moment including power-up. Holding a button is the only thing that ever
// lights it, which is what the ESP-IDF bench mode does too.
//
// The pattern mirrors laser_identity_level() in ../../main/controller.c, and the button
// mirrors button_update(). Both are checked against the C originals by checks/check_blink.py,
// so the sketch and the firmware cannot drift apart unnoticed.
//
// Board: "ESP32 Dev Module". No Wi-Fi, no servo. The full game firmware is ESP-IDF and
// does not build under Arduino; this sketch exists only to put the blink on a board.

const int CONTROLLER_ID = 3;      // 1, 2 or 3, unique per controller
const int LASER_GPIO = 25;        // gate of the laser MOSFET, as in the ESP-IDF firmware
const int FIRE_GPIO = 27;         // CONFIG_BB_FIRE_GPIO; switch connects the pin to GND
// GAP_MS comes from identityGapMs() below; a fixed gap starves the longest period.
const uint32_t DEBOUNCE_MS = 15;  // BUTTON_DEBOUNCE_MS in controller.h

// 600, 800 and 1000 are in the ratio 3:4:5, so no period is a harmonic of another and a
// camera cannot mistake one for a multiple of the next.
uint32_t identityPeriodMs(int controllerId) {
  switch (controllerId) {
    case 2:  return 800;
    case 3:  return 1000;
    default: return 600;
  }
}

// Lit except for one blanking gap per period. The camera identifies a controller from how
// often the gap comes round, never from how much of the time the laser is on: a frame
// where tracking missed the dot looks exactly like a gap, so a duty measurement is
// confounded by dropouts, while random dropouts leave the period where it is.
// A constant share of the period, matching laser_identity_gap_ms() in controller.c. One
// fixed gap made the longest period the weakest: 133 ms is 22 percent of 600 but only 13
// percent of 1000, so the longest carried the least signal and was identified correctly on
// 88 percent of three second windows where the shortest managed 100.
uint32_t identityGapMs(int controllerId) {
  return identityPeriodMs(controllerId) * 22 / 100;
}

bool identityLevel(int controllerId, uint32_t gapMs, uint32_t elapsedMs) {
  uint32_t period = identityPeriodMs(controllerId);
  if (gapMs == 0 || gapMs >= period) {
    return true;
  }
  return (elapsedMs % period) >= gapMs;
}

// The same settle rule as button_update() in controller.c. Kept as a member so the
// Arduino preprocessor cannot hoist a prototype above the type it mentions.
struct Button {
  bool pressed = false;
  bool candidate = false;
  uint32_t changedMs = 0;

  bool update(bool nowPressed, uint32_t nowMs) {
    if (nowPressed != candidate) {
      candidate = nowPressed;
      changedMs = nowMs;
    }
    if (pressed != candidate && nowMs - changedMs >= DEBOUNCE_MS) {
      pressed = candidate;
      return true;
    }
    return false;
  }
};

Button fire;
uint32_t pressedAt = 0;
bool lit = false;

void setup() {
  Serial.begin(115200);
  pinMode(LASER_GPIO, OUTPUT);
  digitalWrite(LASER_GPIO, LOW);      // dark at power-up, and until FIRE is held
  pinMode(FIRE_GPIO, INPUT_PULLUP);   // switch pulls the pin to GND when pressed
  Serial.printf("identity blink: controller %d, period %u ms, gap %u ms; hold GPIO%d to fire\n",
                CONTROLLER_ID, identityPeriodMs(CONTROLLER_ID), identityGapMs(CONTROLLER_ID), FIRE_GPIO);
}

void loop() {
  uint32_t now = millis();
  if (fire.update(digitalRead(FIRE_GPIO) == LOW, now) && fire.pressed) {
    pressedAt = now;   // the pattern starts where the press does, so its phase is known
  }
  bool wanted = fire.pressed && identityLevel(CONTROLLER_ID, identityGapMs(CONTROLLER_ID), now - pressedAt);
  if (wanted != lit) {
    digitalWrite(LASER_GPIO, wanted ? HIGH : LOW);
    lit = wanted;
    Serial.printf("laser %s\n", lit ? "ON" : "off");
  }
  delay(2);   // well inside the 133 ms gap, so edges land within a frame
}
