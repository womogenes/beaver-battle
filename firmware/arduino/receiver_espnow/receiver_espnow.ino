// ESP-NOW receiver, the piece a laptop cannot be. It listens for controller packets and
// writes each one to USB serial as a line, which beaver_battle.relay turns back into the
// UDP the game already expects. Nothing downstream changes.
//
// No access point, no association, no DHCP: only this board and the controllers, on a
// fixed channel they all agree on.

#include <WiFi.h>
#include <esp_mac.h>
#include <esp_now.h>
#include <esp_wifi.h>

const uint8_t CHANNEL = 1;          // must match the controllers
const int LED_GPIO = 2;             // onboard LED on most DevKit boards
uint32_t received = 0;

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
  if (length <= 0 || length > 240) {
    return;
  }
  // One packet per line; the relay reads lines and forwards them unchanged.
  Serial.write(data, (size_t)length);
  Serial.write('\n');
  received += 1;
  digitalWrite(LED_GPIO, (received & 8) ? HIGH : LOW);
}

void setup() {
  Serial.begin(115200);
  pinMode(LED_GPIO, OUTPUT);
  WiFi.mode(WIFI_STA);
  WiFi.disconnect();
  esp_wifi_set_channel(CHANNEL, WIFI_SECOND_CHAN_NONE);
  if (esp_now_init() != ESP_OK) {
    Serial.println("{\"error\":\"esp_now_init failed\"}");
    return;
  }
  esp_now_register_recv_cb(onReceive);
  Serial.printf("{\"receiver\":\"ready\",\"channel\":%d,\"mac\":\"%s\"}\n",
                CHANNEL, macText().c_str());
}

void loop() {
  // A heartbeat, so the receiver can be told apart from a dead link during a demo. It is
  // not an input packet, so the relay ignores it.
  static uint32_t reported = 0;
  if (millis() - reported >= 2000) {
    reported = millis();
    uint8_t primary = 0;
    wifi_second_chan_t second = WIFI_SECOND_CHAN_NONE;
    esp_wifi_get_channel(&primary, &second);
    Serial.printf("{\"receiver\":\"alive\",\"channel\":%d,\"mac\":\"%s\",\"packets\":%u}\n",
                  primary, macText().c_str(), received);
  }
  delay(20);
}
