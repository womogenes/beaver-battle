#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <math.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

#include "cJSON.h"
#include "driver/gpio.h"
#include "driver/ledc.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_random.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/task.h"
#include "lwip/inet.h"
#include "lwip/sockets.h"
#include "nvs_flash.h"
#include "sdkconfig.h"

#include "controller.h"

enum { LASER_GPIO = 25, SERVO_GPIO = 26, FIRE_GPIO = CONFIG_BB_FIRE_GPIO,
       SPECIAL_GPIO = CONFIG_BB_SPECIAL_GPIO,
       WIFI_READY = BIT0, HEARTBEAT_MS = 20, LOOP_MS = 5, PACKET_CAPACITY = 512 };

static const char *log_tag = "beaver";
static EventGroupHandle_t wifi_events;

static uint64_t time_ms(void)
{
    return (uint64_t)esp_timer_get_time() / 1000;
}

static bool servo_enabled(void)
{
#if !defined(CONFIG_BB_LASER_BUTTON_TEST) && (defined(CONFIG_BB_SERVO_ENABLED) || defined(CONFIG_BB_SERVO_BUTTON_TEST))
    return true;
#else
    return false;
#endif
}

static void output_init(void)
{
    gpio_config_t outputs = {
        .pin_bit_mask = (UINT64_C(1) << LASER_GPIO) | (UINT64_C(1) << SERVO_GPIO),
        .mode = GPIO_MODE_OUTPUT,
    };
    ESP_ERROR_CHECK(gpio_config(&outputs));
    ESP_ERROR_CHECK(gpio_set_level(LASER_GPIO, 0));
    ESP_ERROR_CHECK(gpio_set_level(SERVO_GPIO, 0));
    ledc_timer_config_t laser_timer = {
        .speed_mode = LEDC_LOW_SPEED_MODE,
        .duty_resolution = LEDC_TIMER_10_BIT,
        .timer_num = LEDC_TIMER_0,
        .freq_hz = 5000,
        .clk_cfg = LEDC_AUTO_CLK,
    };
    ESP_ERROR_CHECK(ledc_timer_config(&laser_timer));
    ledc_channel_config_t laser_channel = {
        .gpio_num = LASER_GPIO,
        .speed_mode = LEDC_LOW_SPEED_MODE,
        .channel = LEDC_CHANNEL_0,
        .timer_sel = LEDC_TIMER_0,
        .duty = 0,
    };
    ESP_ERROR_CHECK(ledc_channel_config(&laser_channel));
    if (servo_enabled()) {
        uint32_t initial_pulse_us = CONFIG_BB_SERVO_REST_US;
#ifdef CONFIG_BB_SERVO_BUTTON_TEST
        initial_pulse_us = 1500;
#endif
        ledc_timer_config_t servo_timer = {
            .speed_mode = LEDC_LOW_SPEED_MODE,
            .duty_resolution = LEDC_TIMER_16_BIT,
            .timer_num = LEDC_TIMER_1,
            .freq_hz = 50,
            .clk_cfg = LEDC_AUTO_CLK,
        };
        ESP_ERROR_CHECK(ledc_timer_config(&servo_timer));
        ledc_channel_config_t servo_channel = {
            .gpio_num = SERVO_GPIO,
            .speed_mode = LEDC_LOW_SPEED_MODE,
            .channel = LEDC_CHANNEL_1,
            .timer_sel = LEDC_TIMER_1,
            .duty = (uint32_t)((UINT64_C(65536) * initial_pulse_us) / 20000),
        };
        ESP_ERROR_CHECK(ledc_channel_config(&servo_channel));
    }
    gpio_config_t buttons = {
        .pin_bit_mask = (UINT64_C(1) << FIRE_GPIO) | (UINT64_C(1) << SPECIAL_GPIO),
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
    };
    ESP_ERROR_CHECK(gpio_config(&buttons));
}

static void servo_pulse(uint32_t pulse_us)
{
    uint32_t duty = (uint32_t)((UINT64_C(65536) * pulse_us) / 20000);
    ESP_ERROR_CHECK(ledc_set_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_1, duty));
    ESP_ERROR_CHECK(ledc_update_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_1));
}

static void output_update(bool laser, bool pressing)
{
    static bool previous_laser = false;
    static bool previous_pressing = false;
    if (laser != previous_laser) {
        ESP_ERROR_CHECK(ledc_set_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_0,
                                     laser ? CONFIG_BB_LASER_DUTY : 0));
        ESP_ERROR_CHECK(ledc_update_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_0));
        previous_laser = laser;
    }
    if (servo_enabled() && pressing != previous_pressing) {
        uint32_t pulse_us = pressing ? CONFIG_BB_SERVO_PRESS_US : CONFIG_BB_SERVO_REST_US;
        servo_pulse(pulse_us);
        previous_pressing = pressing;
    }
}

static bool buttons_read(Button *fire, Button *special, uint64_t now)
{
    bool fire_changed = button_update(fire, gpio_get_level(FIRE_GPIO) == 0, now);
    bool special_changed = button_update(special, gpio_get_level(SPECIAL_GPIO) == 0, now);
    if (fire_changed) {
        ESP_LOGI(log_tag, "FIRE GPIO%d %s", FIRE_GPIO, fire->pressed ? "PRESSED" : "RELEASED");
    }
    if (special_changed) {
        ESP_LOGI(log_tag, "SPECIAL GPIO%d %s", SPECIAL_GPIO, special->pressed ? "PRESSED" : "RELEASED");
    }
    return fire_changed || special_changed;
}

#ifdef CONFIG_BB_LASER_BUTTON_TEST
static void laser_button_test(void)
{
    Button fire = {0};
    Button special = {0};
    bool laser = false;
    bool previous_pulse = false;
    uint64_t pulse_started = 0;
    uint64_t next_status_ms = 0;
    TickType_t wake_tick = xTaskGetTickCount();
    ESP_LOGI(log_tag, "LASER BUTTON TEST: GPIO%d steady; GPIO%d 2 Hz pulse (wins if both held); release both OFF; servo disabled; no Wi-Fi", FIRE_GPIO, SPECIAL_GPIO);
    ESP_LOGI(log_tag, "LASER GPIO%d OFF", LASER_GPIO);
    while (true) {
        uint64_t now = time_ms();
        buttons_read(&fire, &special, now);
        if (special.pressed && !previous_pulse) {
            pulse_started = now;
        }
        previous_pulse = special.pressed;
        bool requested = laser_bench_level(fire.pressed, special.pressed, now - pulse_started);
        if (requested != laser) {
            output_update(requested, false);
            laser = requested;
            ESP_LOGI(log_tag, "LASER GPIO%d %s", LASER_GPIO, laser ? "ON" : "OFF");
        }
        if (now >= next_status_ms) {
            ESP_LOGI(log_tag, "BUTTON STATUS: D%d raw=%d %s | D%d raw=%d %s | D%d laser=%s (raw 0=pressed, 1=released)",
                     FIRE_GPIO, gpio_get_level(FIRE_GPIO), fire.pressed ? "PRESSED" : "released",
                     SPECIAL_GPIO, gpio_get_level(SPECIAL_GPIO), special.pressed ? "PRESSED" : "released",
                     LASER_GPIO, laser ? "ON" : "OFF");
            next_status_ms = now + 500;
        }
        vTaskDelayUntil(&wake_tick, pdMS_TO_TICKS(LOOP_MS));
    }
}
#endif

#ifdef CONFIG_BB_LASER_IDENTITY_TEST
static void laser_identity_test(void)
{
    bool laser = false;
    uint64_t started = time_ms();
    uint64_t next_status_ms = 0;
    TickType_t wake_tick = xTaskGetTickCount();
    ESP_LOGI(log_tag, "LASER IDENTITY TEST: controller %d, %" PRIu32 " ms period, %d ms gap; no buttons, no Wi-Fi",
             CONFIG_BB_CONTROLLER_ID, laser_identity_period_ms(CONFIG_BB_CONTROLLER_ID),
             CONFIG_BB_LASER_IDENTITY_GAP_MS);
    while (true) {
        uint64_t now = time_ms();
        bool requested = laser_identity_level(CONFIG_BB_CONTROLLER_ID,
                                              CONFIG_BB_LASER_IDENTITY_GAP_MS, now - started);
        if (requested != laser) {
            output_update(requested, false);
            laser = requested;
        }
        if (now >= next_status_ms) {
            ESP_LOGI(log_tag, "IDENTITY BLINK: controller %d period %" PRIu32 " ms gap %d ms, laser %s",
                     CONFIG_BB_CONTROLLER_ID, laser_identity_period_ms(CONFIG_BB_CONTROLLER_ID),
                     CONFIG_BB_LASER_IDENTITY_GAP_MS, laser ? "ON" : "OFF");
            next_status_ms = now + 2000;
        }
        vTaskDelayUntil(&wake_tick, pdMS_TO_TICKS(LOOP_MS));
    }
}
#endif

#ifdef CONFIG_BB_SERVO_BUTTON_TEST
static void servo_button_test(void)
{
    Button fire = {0};
    Button special = {0};
    uint32_t pulse_us = 1500;
    uint64_t next_step_ms = time_ms() + 20;
    TickType_t wake_tick = xTaskGetTickCount();
    ESP_LOGI(log_tag, "SERVO BUTTON TEST: GPIO%d lowers pulse; GPIO%d raises; neither/both holds; laser off; no Wi-Fi", FIRE_GPIO, SPECIAL_GPIO);
    ESP_LOGI(log_tag, "SERVO GPIO%d 1500 us; limits %d..%d us; step %d us / 20 ms", SERVO_GPIO,
             CONFIG_BB_SERVO_TEST_MIN_US, CONFIG_BB_SERVO_TEST_MAX_US, CONFIG_BB_SERVO_TEST_STEP_US);
    while (true) {
        uint64_t now = time_ms();
        buttons_read(&fire, &special, now);
        if (now >= next_step_ms) {
            next_step_ms = now + 20;
            uint32_t requested = servo_jog(pulse_us, fire.pressed, special.pressed,
                CONFIG_BB_SERVO_TEST_MIN_US, CONFIG_BB_SERVO_TEST_MAX_US, CONFIG_BB_SERVO_TEST_STEP_US);
            if (requested != pulse_us) {
                pulse_us = requested;
                servo_pulse(pulse_us);
                ESP_LOGI(log_tag, "SERVO GPIO%d %" PRIu32 " us", SERVO_GPIO, pulse_us);
            }
        }
        vTaskDelayUntil(&wake_tick, pdMS_TO_TICKS(LOOP_MS));
    }
}
#endif

static void wifi_event(void *argument, esp_event_base_t event_base, int32_t event_id, void *event_data)
{
    (void)argument;
    (void)event_data;
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        xEventGroupClearBits(wifi_events, WIFI_READY);
        esp_wifi_connect();
    } else if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        xEventGroupSetBits(wifi_events, WIFI_READY);
        ESP_LOGI(log_tag, "Wi-Fi ready; controller %d", CONFIG_BB_CONTROLLER_ID);
    }
}

static void wifi_init(void)
{
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();
    wifi_events = xEventGroupCreate();
    configASSERT(wifi_events != NULL);
    wifi_init_config_t initialization = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&initialization));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, wifi_event, NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, wifi_event, NULL));
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));
    wifi_config_t configuration = {0};
    size_t ssid_length = strlen(CONFIG_BB_WIFI_SSID);
    size_t password_length = strlen(CONFIG_BB_WIFI_PASSWORD);
    if (ssid_length == 0) {
        ESP_LOGI(log_tag, "Wi-Fi not configured; serial button testing is available");
        return;
    }
    if (ssid_length > sizeof(configuration.sta.ssid) ||
        password_length > sizeof(configuration.sta.password)) {
        ESP_LOGE(log_tag, "Set valid Wi-Fi credentials in Beaver controller build settings");
        return;
    }
    memcpy(configuration.sta.ssid, CONFIG_BB_WIFI_SSID, ssid_length);
    memcpy(configuration.sta.password, CONFIG_BB_WIFI_PASSWORD, password_length);
    configuration.sta.threshold.authmode = WIFI_AUTH_OPEN;
    configuration.sta.pmf_cfg.capable = true;
    configuration.sta.pmf_cfg.required = false;
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &configuration));
    ESP_ERROR_CHECK(esp_wifi_start());
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));
}

static bool json_uint(const cJSON *json, const char *key, uint32_t *value)
{
    const cJSON *item = cJSON_GetObjectItemCaseSensitive(json, key);
    if (!cJSON_IsNumber(item) || !isfinite(item->valuedouble) || item->valuedouble < 0 ||
        item->valuedouble > UINT32_MAX || floor(item->valuedouble) != item->valuedouble) {
        return false;
    }
    *value = (uint32_t)item->valuedouble;
    return true;
}

static bool command_parse(const char *packet, size_t length, Command *command)
{
    if (memchr(packet, '\0', length) != NULL) {
        return false;
    }
    const char *end = NULL;
    cJSON *json = cJSON_ParseWithLengthOpts(packet, length + 1, &end, true);
    uint32_t version = 0;
    const cJSON *type = cJSON_GetObjectItemCaseSensitive(json, "type");
    const cJSON *laser = cJSON_GetObjectItemCaseSensitive(json, "laser");
    bool valid = json != NULL && cJSON_IsObject(json) && json_uint(json, "v", &version) &&
        version == 1 && cJSON_IsString(type) && strcmp(type->valuestring, "command") == 0 &&
        cJSON_IsBool(laser) && json_uint(json, "session", &command->session) &&
        json_uint(json, "seq", &command->seq) && json_uint(json, "feedback_id", &command->feedback_id) &&
        json_uint(json, "duration_ms", &command->duration_ms);
    if (valid) {
        command->laser = cJSON_IsTrue(laser);
    }
    cJSON_Delete(json);
    return valid;
}

static void input_send(int connection, const struct sockaddr_in *laptop, uint32_t boot,
                       uint32_t *seq, unsigned buttons, const Controller *controller)
{
    char packet[200];
    *seq += 1;
    int length = snprintf(packet, sizeof(packet),
        "{\"v\":1,\"type\":\"input\",\"id\":%d,\"boot\":%" PRIu32
        ",\"seq\":%" PRIu32 ",\"buttons\":%u,\"command_seq\":%" PRIu32 ",\"laser\":%s}",
        CONFIG_BB_CONTROLLER_ID, boot, *seq, buttons, controller->command_seq,
        controller->laser ? "true" : "false");
    if (length > 0 && (size_t)length < sizeof(packet)) {
        sendto(connection, packet, length, 0, (const struct sockaddr *)laptop, sizeof(*laptop));
    }
}

void app_main(void)
{
    output_init();
    ESP_LOGI(log_tag, "Controller %d: laser off; servo %s", CONFIG_BB_CONTROLLER_ID,
             servo_enabled() ? "enabled" : "disabled");
    ESP_LOGI(log_tag, "Buttons: FIRE GPIO%d, SPECIAL GPIO%d; switches connect to GND", FIRE_GPIO, SPECIAL_GPIO);
#ifdef CONFIG_BB_LASER_BUTTON_TEST
    laser_button_test();
#endif
#ifdef CONFIG_BB_SERVO_BUTTON_TEST
    servo_button_test();
#endif
#ifdef CONFIG_BB_LASER_IDENTITY_TEST
    laser_identity_test();
#endif
    esp_err_t nvs_result = nvs_flash_init();
    if (nvs_result == ESP_ERR_NVS_NO_FREE_PAGES || nvs_result == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        nvs_result = nvs_flash_init();
    }
    ESP_ERROR_CHECK(nvs_result);
    wifi_init();
    struct sockaddr_in laptop = {
        .sin_family = AF_INET,
        .sin_port = htons(CONFIG_BB_LAPTOP_PORT),
    };
    if (inet_pton(AF_INET, CONFIG_BB_LAPTOP_IP, &laptop.sin_addr) != 1) {
        ESP_LOGE(log_tag, "Laptop IP must be an IPv4 address");
        return;
    }
    Controller controller = {0};
    Button fire = {0};
    Button special = {0};
    uint32_t boot = esp_random();
    uint32_t seq = 0;
    uint64_t last_send_ms = 0;
    uint64_t retry_ms = 0;
    int connection = -1;
    TickType_t wake_tick = xTaskGetTickCount();
    while (true) {
        uint64_t now = time_ms();
        bool changed = buttons_read(&fire, &special, now);
        bool wifi_ready = (xEventGroupGetBits(wifi_events) & WIFI_READY) != 0;
        if (!wifi_ready) {
            controller_disconnect(&controller);
            if (connection >= 0) {
                close(connection);
                connection = -1;
            }
        } else if (connection < 0 && now >= retry_ms) {
            connection = socket(AF_INET, SOCK_DGRAM, IPPROTO_IP);
            struct sockaddr_in local = {.sin_family = AF_INET, .sin_port = 0,
                                        .sin_addr.s_addr = htonl(INADDR_ANY)};
            if (connection >= 0 && (bind(connection, (struct sockaddr *)&local, sizeof(local)) < 0 ||
                                     fcntl(connection, F_SETFL, O_NONBLOCK) < 0)) {
                close(connection);
                connection = -1;
            }
            retry_ms = now + 1000;
            changed = true;
        }
        controller_tick(&controller, now);
        if (connection >= 0) {
            // Bound work per tick so packet floods cannot delay the output lease.
            for (int count = 0; count < 8; count++) {
                char packet[PACKET_CAPACITY + 1];
                struct sockaddr_in source;
                socklen_t source_size = sizeof(source);
                int length = recvfrom(connection, packet, PACKET_CAPACITY, 0,
                                      (struct sockaddr *)&source, &source_size);
                if (length < 0) {
                    if (errno != EAGAIN && errno != EWOULDBLOCK) {
                        controller_disconnect(&controller);
                        close(connection);
                        connection = -1;
                    }
                    break;
                }
                if (length == 0 || length >= PACKET_CAPACITY ||
                    source.sin_addr.s_addr != laptop.sin_addr.s_addr || source.sin_port != laptop.sin_port) {
                    continue;
                }
                packet[length] = '\0';
                Command command = {0};
                if (command_parse(packet, (size_t)length, &command) &&
                    controller_command(&controller, &command, now, servo_enabled())) {
                    changed = true;
                }
            }
        }
        output_update(controller.laser, controller.pressing);
        if (connection >= 0 && (changed || now - last_send_ms >= HEARTBEAT_MS)) {
            unsigned buttons = (fire.pressed ? 1U : 0U) | (special.pressed ? 2U : 0U);
            input_send(connection, &laptop, boot, &seq, buttons, &controller);
            last_send_ms = now;
        }
        vTaskDelayUntil(&wake_tick, pdMS_TO_TICKS(LOOP_MS));
    }
}
