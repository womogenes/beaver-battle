// Wireless controller for Arduino, speaking PROTOCOL.md v1 over UDP.
//
// The ESP-IDF firmware in ../../main is the real one and already sends both buttons; this
// exists because Arduino cannot build it, and it is the only way to get button two to the
// laptop from a machine without ESP-IDF.
//
// What it does: reads both buttons, drives the laser locally with this controller's
// identity pattern while FIRE is held, and sends input packets to the laptop.
//
// What it deliberately does not do: act on laptop commands. The laser is driven by the
// button here rather than by the host, so a lost command cannot leave a player unable to
// aim. That means `command_seq` is always 0 and the host's laser commands are ignored,
// which the identity scheduler must not be relied on to drive while this build is in use.
//
// Wi-Fi credentials live in secrets.h, which is not committed. Copy secrets.example.h.

#include <WiFi.h>
#include <WiFiUdp.h>

#include "secrets.h"

const int CONTROLLER_ID = 2;      // 1 or 2, unique per controller
const int LASER_GPIO = 25;        // gate of the laser MOSFET
const int FIRE_GPIO = 14;         // button one
const int SPECIAL_GPIO = 27;      // button two
const uint32_t DEBOUNCE_MS = 15;  // BUTTON_DEBOUNCE_MS in controller.h
const uint32_t HEARTBEAT_MS = 20; // the protocol's steady send interval

uint32_t identityPeriodMs(int controllerId) {
  return controllerId == 2 ? 800 : 600;
}

// Controller 1 never blinks; controller 2 is dark for a fixed share of each period.
uint32_t identityGapMs(int controllerId) {
  return controllerId == 2 ? identityPeriodMs(controllerId) * 22 / 100 : 0;
}

bool identityLevel(int controllerId, uint32_t gapMs, uint32_t elapsedMs) {
  uint32_t period = identityPeriodMs(controllerId);
  if (gapMs == 0 || gapMs >= period) {
    return true;
  }
  return (elapsedMs % period) >= gapMs;
}

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

WiFiUDP udp;
IPAddress laptop;
Button fire, special;
uint32_t boot = 0, seq = 0, pressedAt = 0, lastSendMs = 0;
bool lit = false;

void setup() {
  Serial.begin(115200);
  pinMode(LASER_GPIO, OUTPUT);
  digitalWrite(LASER_GPIO, LOW);      // dark at power-up and whenever FIRE is released
  pinMode(FIRE_GPIO, INPUT_PULLUP);
  pinMode(SPECIAL_GPIO, INPUT_PULLUP);
  boot = esp_random();
  laptop.fromString(LAPTOP_IP);
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);               // sleep adds tens of ms to every packet
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  udp.begin(0);
  Serial.printf("controller %d: fire GPIO%d, special GPIO%d, laser GPIO%d -> %s:%d\n",
                CONTROLLER_ID, FIRE_GPIO, SPECIAL_GPIO, LASER_GPIO, LAPTOP_IP, LAPTOP_PORT);
}

void sendInput() {
  unsigned buttons = (fire.pressed ? 1U : 0U) | (special.pressed ? 2U : 0U);
  char packet[200];
  seq += 1;
  int length = snprintf(packet, sizeof(packet),
      "{\"v\":1,\"type\":\"input\",\"id\":%d,\"boot\":%u,\"seq\":%u,"
      "\"buttons\":%u,\"command_seq\":0,\"laser\":%s}",
      CONTROLLER_ID, boot, seq, buttons, lit ? "true" : "false");
  if (length > 0 && udp.beginPacket(laptop, LAPTOP_PORT)) {
    udp.write((const uint8_t *)packet, (size_t)length);
    udp.endPacket();
  }
}

void loop() {
  uint32_t now = millis();
  bool changed = false;
  if (fire.update(digitalRead(FIRE_GPIO) == LOW, now)) {
    changed = true;
    if (fire.pressed) {
      pressedAt = now;   // the identity pattern starts where the press does
    }
  }
  if (special.update(digitalRead(SPECIAL_GPIO) == LOW, now)) {
    changed = true;
    Serial.printf("special %s\n", special.pressed ? "PRESSED" : "released");
  }

  bool wanted = fire.pressed && identityLevel(CONTROLLER_ID, identityGapMs(CONTROLLER_ID), now - pressedAt);
  if (wanted != lit) {
    digitalWrite(LASER_GPIO, wanted ? HIGH : LOW);
    lit = wanted;
  }

  // A button change goes out at once; otherwise the protocol's steady heartbeat carries it.
  if (WiFi.status() == WL_CONNECTED && (changed || now - lastSendMs >= HEARTBEAT_MS)) {
    sendInput();
    lastSendMs = now;
  }
  delay(2);
}
