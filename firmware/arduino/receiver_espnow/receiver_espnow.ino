// ESP-NOW receiver, the piece a laptop cannot be. It listens for controller packets and
// writes each one to USB serial as a line, which beaver_battle.relay turns back into the
// UDP the game already expects. Nothing downstream changes.
//
// No access point, no association, no DHCP: only this board and the controllers, on a
// fixed channel they all agree on.
//
// The radio callback never touches Serial. Two controllers at 50 Hz offer about 10 kB/s
// of JSON, which is 86 percent of a 115200 link, and Arduino's Serial.write blocks once
// the transmit buffer fills. Blocking inside the receive callback stalls the WiFi task,
// and the driver then drops incoming frames: measured on the bench as 6.5 percent loss
// on one controller with almost no truncated lines. So the callback only copies into a
// ring and returns, loop() is the sole writer to Serial, and BAUD leaves headroom.

#include <WiFi.h>
#include <esp_mac.h>
#include <esp_now.h>
#include <esp_wifi.h>

const uint8_t CHANNEL = 1;          // must match the controllers
const int LED_GPIO = 2;             // onboard LED on most DevKit boards
const uint32_t BAUD = 460800;       // must match beaver_battle.relay --baud

// Single producer (the WiFi task) and single consumer (loop), so a power-of-two ring
// with one index each needs no lock. SLOTS must stay a power of two for the mask.
const size_t SLOTS = 64;
const size_t SLOT_BYTES = 250;      // an ESP-NOW payload cannot exceed this

struct Slot {
  uint8_t data[SLOT_BYTES];
  uint16_t length;
};

Slot ring[SLOTS];
volatile uint32_t filled = 0;        // producer advances
volatile uint32_t drained = 0;          // consumer advances
uint32_t received = 0;
volatile uint32_t overflowed = 0;   // packets the ring had no room for

// Read straight from the driver: WiFi.macAddress() can answer before the driver has
// filled it in, which showed as 00:00:00:00:00:00 on the bench.
String macText() {
  uint8_t mac[6] = {0};
  esp_wifi_get_mac(WIFI_IF_STA, mac);
  char text[18];
  snprintf(text, sizeof(text), "%02x:%02x:%02x:%02x:%02x:%02x",
           mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
  return String(text);
}

void onReceive(const esp_now_recv_info_t *info, const uint8_t *data, int length) {
  (void)info;
  if (length <= 0 || (size_t)length > SLOT_BYTES) {
    return;
  }
  if (filled - drained >= SLOTS) {    // consumer has fallen behind; drop, never block
    overflowed += 1;
    return;
  }
  Slot &slot = ring[filled & (SLOTS - 1)];
  memcpy(slot.data, data, (size_t)length);
  slot.length = (uint16_t)length;
  filled += 1;                     // publish only after the payload is in place
}

void setup() {
  Serial.setTxBufferSize(4096);     // absorb bursts before write() ever has to wait
  Serial.begin(BAUD);
  pinMode(LED_GPIO, OUTPUT);
  WiFi.mode(WIFI_STA);
  WiFi.disconnect();
  esp_wifi_set_channel(CHANNEL, WIFI_SECOND_CHAN_NONE);
  if (esp_now_init() != ESP_OK) {
    Serial.println("{\"error\":\"esp_now_init failed\"}");
    return;
  }
  esp_now_register_recv_cb(onReceive);
  Serial.printf("{\"receiver\":\"ready\",\"channel\":%d,\"mac\":\"%s\",\"baud\":%u}\n",
                CHANNEL, macText().c_str(), BAUD);
}

void loop() {
  // Sole writer to Serial: drain the ring first, then the heartbeat, so no line can be
  // cut in half by the other task.
  while (drained != filled) {
    Slot &slot = ring[drained & (SLOTS - 1)];
    Serial.write(slot.data, slot.length);
    Serial.write('\n');
    drained += 1;
    received += 1;
    digitalWrite(LED_GPIO, (received & 8) ? HIGH : LOW);
  }

  // A heartbeat, so the receiver can be told apart from a dead link during a demo. It is
  // not an input packet, so the relay ignores it. `dropped` should stay at zero: if it
  // climbs, the serial link is too slow for the offered packet rate.
  static uint32_t reported = 0;
  if (millis() - reported >= 2000) {
    reported = millis();
    uint8_t primary = 0;
    wifi_second_chan_t second = WIFI_SECOND_CHAN_NONE;
    esp_wifi_get_channel(&primary, &second);
    Serial.printf("{\"receiver\":\"alive\",\"channel\":%d,\"mac\":\"%s\",\"packets\":%u,"
                  "\"dropped\":%u}\n",
                  primary, macText().c_str(), received, overflowed);
  }

  delay(1);   // let the idle task run; the ring absorbs anything that lands meanwhile
}
