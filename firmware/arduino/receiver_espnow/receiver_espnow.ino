// ESP-NOW receiver, the piece a laptop cannot be. It listens for controller packets and
// writes each one to USB serial as a line, which beaver_battle.relay turns back into the
// UDP the game already expects. Nothing downstream changes.
//
// No access point, no association, no DHCP: only this board and the controllers, on a
// fixed channel they all agree on.

#include <WiFi.h>
#include <esp_now.h>
#include <esp_wifi.h>

const uint8_t CHANNEL = 1;          // must match the controllers
const int LED_GPIO = 2;             // onboard LED on most DevKit boards
uint32_t received = 0;

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
                CHANNEL, WiFi.macAddress().c_str());
}

void loop() {
  delay(1000);
}
