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

const int CONTROLLER_ID = 2;      // 1, 2 or 3, unique per controller
const int LASER_GPIO = 25;        // gate of the laser MOSFET, as in the ESP-IDF firmware
const int FIRE_GPIO = 14;         // button one, CONFIG_BB_FIRE_GPIO; switch pulls to GND
const int SPECIAL_GPIO = 27;      // button two, CONFIG_BB_SPECIAL_GPIO
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
// Matching laser_identity_gap_ms() in controller.c. Controller 1 never blinks: blinking
// costs detection, and the one controller that needs no gap to be recognised keeps all of
// its light for tracking. The others take a constant share of their period, since one
// fixed gap made the longest period the weakest at 88 percent against 100.
uint32_t identityGapMs(int controllerId) {
  if (controllerId == 1) {
    return 0;   // controller 1 never blinks; that is what identifies it
  }
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
Button special;
uint32_t pressedAt = 0;
bool lit = false;

void setup() {
  Serial.begin(115200);
  pinMode(LASER_GPIO, OUTPUT);
  digitalWrite(LASER_GPIO, LOW);      // dark at power-up, and until FIRE is held
  pinMode(FIRE_GPIO, INPUT_PULLUP);      // switch pulls the pin to GND when pressed
  pinMode(SPECIAL_GPIO, INPUT_PULLUP);
  Serial.printf("identity blink: controller %d, period %u ms, gap %u ms; fire GPIO%d, special GPIO%d\n",
                CONTROLLER_ID, identityPeriodMs(CONTROLLER_ID), identityGapMs(CONTROLLER_ID),
                FIRE_GPIO, SPECIAL_GPIO);
}

void loop() {
  uint32_t now = millis();
  if (fire.update(digitalRead(FIRE_GPIO) == LOW, now) && fire.pressed) {
    pressedAt = now;   // the pattern starts where the press does, so its phase is known
  }
  // The second button drives no output here; reporting it is how its wiring gets checked.
  if (special.update(digitalRead(SPECIAL_GPIO) == LOW, now)) {
    Serial.printf("special %s\n", special.pressed ? "PRESSED" : "released");
  }
  bool wanted = fire.pressed && identityLevel(CONTROLLER_ID, identityGapMs(CONTROLLER_ID), now - pressedAt);
  if (wanted != lit) {
    digitalWrite(LASER_GPIO, wanted ? HIGH : LOW);
    lit = wanted;
    Serial.printf("fire %s, laser %s\n", fire.pressed ? "held" : "released", lit ? "ON" : "off");
  }
  delay(2);   // well inside the 133 ms gap, so edges land within a frame
}
