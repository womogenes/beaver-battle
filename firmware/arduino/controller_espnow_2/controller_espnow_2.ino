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
#include <driver/gpio.h>
#include <esp_now.h>
#include <esp_wifi.h>

const int CONTROLLER_ID = 2;         // 1 or 2, unique per controller
const int LASER_GPIO = 25;         // default; 'p<n>' over serial moves it on the bench
int laserPin = LASER_GPIO;
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

// GPIO25 is DAC1. A pad left routed to the DAC, or to any other peripheral, keeps driving
// an analog level through digitalWrite, which shows up as a laser that lights but is dim
// and small rather than one that fails outright -- measured on controller 2 as a dot of
// median area 1 px2 and redness 18, against controller 1's 98 and 31 on the same rig. An
// ordinary reflash does not clear it because nothing in the sketch ever touched the pad
// configuration. Reset it to plain GPIO and ask for the strongest drive the pad offers, so
// the MOSFET gate sees a full swing.
void setupLaserPin() {
  gpio_reset_pin((gpio_num_t)laserPin);
#if SOC_DAC_SUPPORTED
  dacDisable(laserPin);
#endif
  pinMode(laserPin, OUTPUT);
  gpio_set_drive_capability((gpio_num_t)laserPin, GPIO_DRIVE_CAP_3);
  digitalWrite(laserPin, LOW);     // dark at power-up and whenever FIRE is released
}

// Move the laser to another pad without reflashing, so a pad that cannot drive the gate
// can be told from a laser that is simply weak. Bench only, like the other serial commands.
void switchLaserPin(int pin) {
  // GPIO12 (MTDI) selects flash voltage at reset, and a board left with it pulled high does
  // not boot at all -- "invalid header: 0xffffffff", recoverable only by holding BOOT. 0, 2
  // and 15 are the other straps, and 6-11 are wired to the SPI flash. None may be driven.
  const int forbidden[] = {0, 2, 6, 7, 8, 9, 10, 11, 12, 15};
  for (size_t i = 0; i < sizeof(forbidden) / sizeof(forbidden[0]); i++) {
    if (pin == forbidden[i]) {
      Serial.printf("laser pin %d refused: strapping or flash pin\n", pin);
      return;
    }
  }
  if (pin < 0 || pin > 33) {
    Serial.printf("laser pin %d out of range\n", pin);
    return;
  }
  digitalWrite(laserPin, LOW);
  gpio_reset_pin((gpio_num_t)laserPin);
  laserPin = pin;
  setupLaserPin();
  Serial.printf("laser now on GPIO%d\n", laserPin);
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
// Bench diagnostics over USB serial. In the game the controller runs from a power bank with
// nothing on its USB port, so these only ever fire on the bench. They make the laser
// testable without a hand holding a button for the length of a measurement.
//   l  force the laser solid   o  force it off   b  back to the FIRE button
//   p<n>  move the laser to GPIO<n>          ?  report current state
int laserOverride = -1;              // -1 follows FIRE, 0 forced off, 1 forced solid
uint32_t boot = 0, seq = 0, pressedAt = 0, lastSendMs = 0, reportedMs = 0;
uint32_t sent = 0, failedToQueue = 0, delivered = 0, notDelivered = 0;
bool lit = false;

// Whether the radio actually got it out is the difference between a dead controller and a
// dead receiver, and only the send callback can tell them apart.
void onSent(const wifi_tx_info_t *info, esp_now_send_status_t status) {
  (void)info;
  if (status == ESP_NOW_SEND_SUCCESS) {
    delivered += 1;
  } else {
    notDelivered += 1;
  }
}

void setup() {
  Serial.begin(115200);
  Serial.setTimeout(100);   // 'p<n>' parses a number; do not stall the loop waiting
  setupLaserPin();
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
  esp_now_register_send_cb(onSent);
  uint8_t primary = 0;
  wifi_second_chan_t second = WIFI_SECOND_CHAN_NONE;
  esp_wifi_get_channel(&primary, &second);
  Serial.printf("controller %d over ESP-NOW on channel %d (asked for %d); fire GPIO%d, special GPIO%d\n",
                CONTROLLER_ID, primary, CHANNEL, FIRE_GPIO, SPECIAL_GPIO);
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
    sent += 1;
    if (esp_now_send(BROADCAST, (const uint8_t *)packet, (size_t)length) != ESP_OK) {
      failedToQueue += 1;
    }
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
    Serial.printf("button one %s\n", fire.pressed ? "PRESSED" : "released");
  }
  if (special.update(digitalRead(SPECIAL_GPIO) == LOW, now)) {
    changed = true;
    Serial.printf("button two %s\n", special.pressed ? "PRESSED" : "released");
  }
  while (Serial.available()) {
    int command = Serial.read();
    if (command == 'l') { laserOverride = 1; Serial.println("laser forced SOLID"); }
    else if (command == 'o') { laserOverride = 0; Serial.println("laser forced OFF"); }
    else if (command == 'b') { laserOverride = -1; Serial.println("laser follows FIRE"); }
    else if (command == 'p') { switchLaserPin((int)Serial.parseInt()); }
    else if (command == '?') {
      Serial.printf("controller %d, laser GPIO%d, override %d, lit %d\n",
                    CONTROLLER_ID, laserPin, laserOverride, (int)lit);
    }
  }
  bool wanted = laserOverride >= 0
      ? laserOverride == 1
      : (fire.pressed && identityLevel(CONTROLLER_ID, identityGapMs(CONTROLLER_ID), now - pressedAt));
  if (wanted != lit) {
    digitalWrite(laserPin, wanted ? HIGH : LOW);
    lit = wanted;
  }
  if (changed || now - lastSendMs >= HEARTBEAT_MS) {
    sendInput();
    lastSendMs = now;
  }
  if (now - reportedMs >= 2000) {
    reportedMs = now;
    uint8_t primary = 0;
    wifi_second_chan_t second = WIFI_SECOND_CHAN_NONE;
    esp_wifi_get_channel(&primary, &second);
    // Raw pin levels as well as debounced state: a pin stuck high reads as a button
    // nobody is pressing, and a pin stuck low as one held forever, and neither shows up
    // in the debounced view at all.
    Serial.printf("controller %d ch%d sent %u, radio ok %u/%u | raw fire(GPIO%d)=%d special(GPIO%d)=%d"
                  " | pressed fire=%d special=%d | mask %u\n",
                  CONTROLLER_ID, primary, sent, delivered, notDelivered + delivered,
                  FIRE_GPIO, digitalRead(FIRE_GPIO), SPECIAL_GPIO, digitalRead(SPECIAL_GPIO),
                  fire.pressed ? 1 : 0, special.pressed ? 1 : 0,
                  (fire.pressed ? 1U : 0U) | (special.pressed ? 2U : 0U));
  }
  delay(2);
}
