// Identity blink bench test, for testing camera detection without ESP-IDF.
//
// This is the same pattern as laser_identity_level() in ../../main/controller.c, and the
// two must agree: the laser is lit except for one gap of GAP_MS at the start of every
// period, and the period identifies the controller. Change CONTROLLER_ID per board.
//
// Board: "ESP32 Dev Module". Nothing else is driven: no Wi-Fi, no servo, no buttons.
// The full game firmware is ESP-IDF and does not build under Arduino; this sketch exists
// only so the blink can be put on the board and measured.

const int CONTROLLER_ID = 1;   // 1, 2 or 3, unique per controller
const int LASER_GPIO = 25;     // gate of the laser MOSFET, as in the ESP-IDF firmware
const uint32_t GAP_MS = 133;   // four frames at 30 fps

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
bool identityLevel(int controllerId, uint32_t gapMs, uint32_t elapsedMs) {
  uint32_t period = identityPeriodMs(controllerId);
  if (gapMs == 0 || gapMs >= period) {
    return true;
  }
  return (elapsedMs % period) >= gapMs;
}

uint32_t started = 0;
bool lit = false;

void setup() {
  Serial.begin(115200);
  pinMode(LASER_GPIO, OUTPUT);
  digitalWrite(LASER_GPIO, LOW);   // laser starts off, as the real firmware does
  started = millis();
  Serial.printf("identity blink: controller %d, period %u ms, gap %u ms, laser on GPIO%d\n",
                CONTROLLER_ID, identityPeriodMs(CONTROLLER_ID), GAP_MS, LASER_GPIO);
}

void loop() {
  bool wanted = identityLevel(CONTROLLER_ID, GAP_MS, millis() - started);
  if (wanted != lit) {
    digitalWrite(LASER_GPIO, wanted ? HIGH : LOW);
    lit = wanted;
  }
  delay(2);   // well inside the 133 ms gap, so edges land within a frame
}
