// Wireless controller over ESP-NOW, for a room where the venue network cannot be trusted.
//
// ESP-NOW is peer to peer on the Wi-Fi radio: no access point, no association, no DHCP and
// nothing from the venue involved, which is the part that has already failed on this
// project when client isolation stopped controllers reaching the laptop. A laptop cannot
// speak ESP-NOW, so a third board runs receiver_espnow and relays over USB.
//
// The payload is exactly the PROTOCOL.md v1 input packet, so nothing downstream changes.
//
// Every board in the group must sit on the same channel, which is fixed here rather than
// inherited from an access point that does not exist.

#include <WiFi.h>
#include <esp_now.h>
#include <esp_wifi.h>

const int CONTROLLER_ID = 2;         // 1 or 2, unique per controller
const int LASER_GPIO = 25;
const int FIRE_GPIO = 14;            // button one
const int SPECIAL_GPIO = 27;         // button two
const uint32_t DEBOUNCE_MS = 15;
const uint32_t HEARTBEAT_MS = 20;
const uint8_t CHANNEL = 1;           // must match receiver_espnow

uint8_t BROADCAST[6] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};

uint32_t identityPeriodMs(int id) { return id == 2 ? 800 : 600; }
uint32_t identityGapMs(int id) { return id == 2 ? identityPeriodMs(id) * 22 / 100 : 0; }

bool identityLevel(int id, uint32_t gapMs, uint32_t elapsedMs) {
  uint32_t period = identityPeriodMs(id);
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

Button fire, special;
uint32_t boot = 0, seq = 0, pressedAt = 0, lastSendMs = 0;
bool lit = false;

void setup() {
  Serial.begin(115200);
  pinMode(LASER_GPIO, OUTPUT);
  digitalWrite(LASER_GPIO, LOW);       // dark at power-up and whenever FIRE is released
  pinMode(FIRE_GPIO, INPUT_PULLUP);
  pinMode(SPECIAL_GPIO, INPUT_PULLUP);
  boot = esp_random();

  WiFi.mode(WIFI_STA);
  WiFi.disconnect();                   // no access point is involved at all
  esp_wifi_set_channel(CHANNEL, WIFI_SECOND_CHAN_NONE);
  if (esp_now_init() != ESP_OK) {
    Serial.println("esp_now_init failed");
    return;
  }
  esp_now_peer_info_t peer = {};
  memcpy(peer.peer_addr, BROADCAST, 6);
  peer.channel = CHANNEL;
  peer.encrypt = false;
  esp_now_add_peer(&peer);
  Serial.printf("controller %d over ESP-NOW on channel %d; fire GPIO%d, special GPIO%d\n",
                CONTROLLER_ID, CHANNEL, FIRE_GPIO, SPECIAL_GPIO);
}

void sendInput() {
  unsigned buttons = (fire.pressed ? 1U : 0U) | (special.pressed ? 2U : 0U);
  char packet[200];
  seq += 1;
  int length = snprintf(packet, sizeof(packet),
      "{\"v\":1,\"type\":\"input\",\"id\":%d,\"boot\":%u,\"seq\":%u,"
      "\"buttons\":%u,\"command_seq\":0,\"laser\":%s}",
      CONTROLLER_ID, boot, seq, buttons, lit ? "true" : "false");
  if (length > 0) {
    esp_now_send(BROADCAST, (const uint8_t *)packet, (size_t)length);
  }
}

void loop() {
  uint32_t now = millis();
  bool changed = false;
  if (fire.update(digitalRead(FIRE_GPIO) == LOW, now)) {
    changed = true;
    if (fire.pressed) {
      pressedAt = now;
    }
  }
  if (special.update(digitalRead(SPECIAL_GPIO) == LOW, now)) {
    changed = true;
  }
  bool wanted = fire.pressed && identityLevel(CONTROLLER_ID, identityGapMs(CONTROLLER_ID), now - pressedAt);
  if (wanted != lit) {
    digitalWrite(LASER_GPIO, wanted ? HIGH : LOW);
    lit = wanted;
  }
  if (changed || now - lastSendMs >= HEARTBEAT_MS) {
    sendInput();
    lastSendMs = now;
  }
  delay(2);
}
