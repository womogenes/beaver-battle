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
#include "driver/uart.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_now.h"
#include "esp_random.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "lwip/inet.h"
#include "lwip/sockets.h"
#include "nvs_flash.h"
#include "sdkconfig.h"

#include "controller.h"

enum { LASER_GPIO = 25, SERVO_GPIO = 33, FIRE_GPIO = CONFIG_BB_FIRE_GPIO,
       SPECIAL_GPIO = CONFIG_BB_SPECIAL_GPIO,
       WIFI_READY = BIT0, HEARTBEAT_MS = 20, LOOP_MS = 5, PACKET_CAPACITY = 512 };

static const char *log_tag = "beaver";
static EventGroupHandle_t wifi_events;

static void nvs_initialize(void)
{
    esp_err_t result = nvs_flash_init();
    if (result == ESP_ERR_NVS_NO_FREE_PAGES || result == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        result = nvs_flash_init();
    }
    ESP_ERROR_CHECK(result);
}

static uint64_t time_ms(void)
{
    return (uint64_t)esp_timer_get_time() / 1000;
}

static bool servo_enabled(void)
{
#if defined(CONFIG_BB_SERVO_BUTTON_TEST) || (!defined(CONFIG_BB_LASER_BUTTON_TEST) && defined(CONFIG_BB_SERVO_ENABLED))
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
        (void)initial_pulse_us;
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
            #ifdef CONFIG_BB_ESPNOW_CONTROLLER
            .duty = 0, /* No holding torque at boot or between game effects. */
#else
            .duty = (uint32_t)((UINT64_C(65536) * initial_pulse_us) / 20000),
#endif
        };
        ESP_ERROR_CHECK(ledc_channel_config(&servo_channel));
#ifdef CONFIG_BB_SERVO_MIRROR_PINS
        const int servo_pins[] = {15, 5, 18, 19, 21};
        for (unsigned index = 0; index < sizeof(servo_pins) / sizeof(servo_pins[0]); index++) {
            ESP_ERROR_CHECK(ledc_set_pin(servo_pins[index], LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_1));
        }
        ESP_LOGI(log_tag, "Servo PWM mirrored to GPIO15/5/18/19/21; GPIO33 remains active");
#endif
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
#ifdef CONFIG_BB_SERVO_BUTTON_TEST
    uint32_t pulse_us = 1500;
    uint64_t next_step_ms = time_ms() + 20;
    ESP_LOGI(log_tag, "SERVO GPIO%d: GPIO%d lowers, GPIO%d raises; both/neither hold; limits %d..%d us; step %d us / 20 ms",
             SERVO_GPIO, FIRE_GPIO, SPECIAL_GPIO, CONFIG_BB_SERVO_TEST_MIN_US,
             CONFIG_BB_SERVO_TEST_MAX_US, CONFIG_BB_SERVO_TEST_STEP_US);
#endif
    TickType_t wake_tick = xTaskGetTickCount();
    ESP_LOGI(log_tag, "LASER BUTTON TEST: GPIO%d steady; GPIO%d 2 Hz pulse (wins if both held); release both OFF; no Wi-Fi", FIRE_GPIO, SPECIAL_GPIO);
    ESP_LOGI(log_tag, "LASER GPIO%d OFF", LASER_GPIO);
    while (true) {
        uint64_t now = time_ms();
        buttons_read(&fire, &special, now);
#ifdef CONFIG_BB_SERVO_BUTTON_TEST
        if (now >= next_step_ms) {
            next_step_ms = now + 20;
            uint32_t requested_pulse = servo_jog(pulse_us, fire.pressed, special.pressed,
                CONFIG_BB_SERVO_TEST_MIN_US, CONFIG_BB_SERVO_TEST_MAX_US, CONFIG_BB_SERVO_TEST_STEP_US);
            if (requested_pulse != pulse_us) {
                pulse_us = requested_pulse;
                servo_pulse(pulse_us);
                ESP_LOGI(log_tag, "SERVO GPIO%d %" PRIu32 " us", SERVO_GPIO, pulse_us);
            }
        }
#endif
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
    Button fire = {0};
    Button special = {0};
    bool laser = false;
    uint64_t pressed_at = 0;
    uint64_t next_status_ms = 0;
    TickType_t wake_tick = xTaskGetTickCount();
    ESP_LOGI(log_tag, "LASER IDENTITY TEST: controller %d, %" PRIu32 " ms period, %d ms gap; hold GPIO%d to fire; no Wi-Fi",
             CONFIG_BB_CONTROLLER_ID, laser_identity_period_ms(CONFIG_BB_CONTROLLER_ID),
             laser_identity_gap_ms(CONFIG_BB_CONTROLLER_ID), FIRE_GPIO);
    ESP_LOGI(log_tag, "LASER GPIO%d OFF", LASER_GPIO);
    while (true) {
        uint64_t now = time_ms();
        /* Nothing lights the laser but a held button, at power-up or ever. */
        if (buttons_read(&fire, &special, now) && fire.pressed) {
            pressed_at = now;   /* the pattern starts where the press does */
        }
        uint32_t gap = CONFIG_BB_LASER_IDENTITY_GAP_MS > 0
            ? (uint32_t)CONFIG_BB_LASER_IDENTITY_GAP_MS
            : laser_identity_gap_ms(CONFIG_BB_CONTROLLER_ID);
        bool requested = fire.pressed &&
            laser_identity_level(CONFIG_BB_CONTROLLER_ID, gap, now - pressed_at);
        if (requested != laser) {
            output_update(requested, false);
            laser = requested;
        }
        if (now >= next_status_ms) {
            ESP_LOGI(log_tag, "IDENTITY BLINK: controller %d period %" PRIu32 " ms gap %" PRIu32 " ms, FIRE %s, laser %s",
                     CONFIG_BB_CONTROLLER_ID, laser_identity_period_ms(CONFIG_BB_CONTROLLER_ID),
                     gap, fire.pressed ? "HELD" : "released", laser ? "ON" : "OFF");
            next_status_ms = now + 2000;
        }
        vTaskDelayUntil(&wake_tick, pdMS_TO_TICKS(LOOP_MS));
    }
}
#endif

#ifdef CONFIG_BB_SERVO_BUTTON_TEST
static void servo_button_test(void)
{
    Button fire = {0}, special = {0};
    ServoSqueeze squeeze = {0};
    uint32_t pulse_us = 1500, start_us = 0, end_us = 0;
    uint64_t next_step_ms = time_ms() + 20, next_status_ms = 0;
    TickType_t wake_tick = xTaskGetTickCount();
    fcntl(STDIN_FILENO, F_SETFL, O_NONBLOCK);
    ESP_LOGI(log_tag, "SERVO CALIBRATION D33: D14 decreases, D27 increases; both/neither hold; laser off");
    ESP_LOGI(log_tag, "Serial: r=save start, e=save end, s=squeeze, x=cancel, ?=status; endpoints reset on reboot");
    while (true) {
        uint64_t now = time_ms();
        bool changed = buttons_read(&fire, &special, now);
        char command;
        while (read(STDIN_FILENO, &command, 1) == 1) {
            if (command == 'r' && !squeeze.stage) start_us = pulse_us;
            if (command == 'e' && !squeeze.stage) end_us = pulse_us;
            if (command == 's') {
                if (!fire.pressed && !special.pressed && servo_squeeze_start(&squeeze, start_us, end_us, now)) {
                    pulse_us = squeeze.pulse_us;
                    servo_pulse(pulse_us);
                    ESP_LOGI(log_tag, "SQUEEZE start -> end -> start");
                } else ESP_LOGW(log_tag, "Squeeze refused: save two distinct endpoints first, or wait until idle");
            }
            if (command == 'x') squeeze.stage = 0;
            next_status_ms = 0;
        }
        if (changed && (fire.pressed || special.pressed)) squeeze.stage = 0;
        if (now >= next_step_ms) {
            next_step_ms = now + 20;
            uint32_t requested = squeeze.stage ? servo_squeeze_tick(&squeeze, now) :
                servo_jog(pulse_us, fire.pressed, special.pressed,
                    CONFIG_BB_SERVO_TEST_MIN_US, CONFIG_BB_SERVO_TEST_MAX_US, CONFIG_BB_SERVO_TEST_STEP_US);
            if (requested != pulse_us) {
                pulse_us = requested;
                servo_pulse(pulse_us);
                next_status_ms = 0;
            }
        }
        if (now >= next_status_ms) {
            ESP_LOGI(log_tag, "SERVO D33 pulse=%" PRIu32 " us angle~%" PRIu32 " deg | start=%" PRIu32 " end=%" PRIu32 " | phase=%u | D14=%d D27=%d",
                pulse_us, (pulse_us - 544) * 180 / (2400 - 544), start_us, end_us,
                squeeze.stage, fire.pressed, special.pressed);
            next_status_ms = now + 500;
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

#if defined(CONFIG_BB_ESPNOW_CONTROLLER) || defined(CONFIG_BB_ESPNOW_RECEIVER)
enum { RADIO_BYTES = 250, RADIO_SLOTS = 64, RECEIVER_BAUD = 460800 };
typedef struct { uint16_t length; char data[RADIO_BYTES + 1]; } RadioPacket;
static QueueHandle_t radio_queue;
static volatile uint32_t radio_dropped;
static const uint8_t broadcast[6] = {255, 255, 255, 255, 255, 255};

static void radio_receive(const esp_now_recv_info_t *info, const uint8_t *data, int length)
{
    (void)info;
    if (length <= 0 || length > RADIO_BYTES) return;
    RadioPacket packet = {.length = (uint16_t)length};
    memcpy(packet.data, data, (size_t)length);
    packet.data[length] = '\0';
    /* Wi-Fi callback only copies bounded data; never parses JSON or writes serial. */
    if (xQueueSend(radio_queue, &packet, 0) != pdTRUE) radio_dropped++;
}

static void radio_init(void)
{
    radio_queue = xQueueCreate(RADIO_SLOTS, sizeof(RadioPacket));
    configASSERT(radio_queue != NULL);
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    wifi_init_config_t config = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&config));
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_start());
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));
    ESP_ERROR_CHECK(esp_wifi_set_channel(CONFIG_BB_ESPNOW_CHANNEL, WIFI_SECOND_CHAN_NONE));
    ESP_ERROR_CHECK(esp_now_init());
    esp_now_peer_info_t peer = {.channel = CONFIG_BB_ESPNOW_CHANNEL,
                                .ifidx = WIFI_IF_STA, .encrypt = false};
    memcpy(peer.peer_addr, broadcast, sizeof(broadcast));
    ESP_ERROR_CHECK(esp_now_add_peer(&peer));
    ESP_ERROR_CHECK(esp_now_register_recv_cb(radio_receive));
}

static bool packet_id(const char *packet, size_t length, const char *kind, uint32_t *id)
{
    if (memchr(packet, '\0', length) != NULL) return false;
    cJSON *json = cJSON_ParseWithLengthOpts(packet, length + 1, NULL, true);
    const cJSON *type = cJSON_GetObjectItemCaseSensitive(json, "type");
    uint32_t version = 0;
    bool valid = cJSON_IsObject(json) && json_uint(json, "v", &version) && version == 1 &&
        cJSON_IsString(type) && strcmp(type->valuestring, kind) == 0 &&
        json_uint(json, "id", id) && *id >= 1 && *id <= 2;
    cJSON_Delete(json);
    return valid;
}
#endif

#ifdef CONFIG_BB_ESPNOW_CONTROLLER
static void espnow_controller_run(void)
{
    radio_init();
    Controller controller = {0};
    ServoSqueeze squeeze = {.pulse_us = CONFIG_BB_SERVO_REST_US};
    Button fire = {0}, special = {0};
    uint32_t boot = esp_random(), seq = 0, failed = 0;
    uint32_t previous_pulse = 0;
    uint64_t release_at = 0;
    uint64_t pressed_at = 0, last_send = 0, status_at = 0;
    bool previous_fire = false, previous_laser = false;
    TickType_t wake = xTaskGetTickCount();
    ESP_LOGI(log_tag, "ESP-NOW controller %d channel %d; servo %s, start=%d end=%d us",
             CONFIG_BB_CONTROLLER_ID, CONFIG_BB_ESPNOW_CHANNEL,
             servo_enabled() ? "enabled" : "disabled", CONFIG_BB_SERVO_REST_US, CONFIG_BB_SERVO_PRESS_US);
    while (true) {
        uint64_t now = time_ms();
        bool changed = buttons_read(&fire, &special, now);
        if (fire.pressed && !previous_fire) pressed_at = now;
        previous_fire = fire.pressed;
        /* Every radio operation and phase transition is nonblocking. */
        RadioPacket packet;
        for (unsigned count = 0; count < 8 && xQueueReceive(radio_queue, &packet, 0) == pdTRUE; count++) {
            uint32_t id = 0;
            Command command = {0};
            if (packet_id(packet.data, packet.length, "command", &id) && id == CONFIG_BB_CONTROLLER_ID &&
                command_parse(packet.data, packet.length, &command) &&
                controller_squeeze_command(&controller, &squeeze, &command, now, servo_enabled(),
                                           CONFIG_BB_SERVO_REST_US, CONFIG_BB_SERVO_PRESS_US)) changed = true;
        }
        uint32_t pulse = controller_squeeze_tick(&controller, &squeeze, now, CONFIG_BB_SERVO_REST_US);
        if (controller.pressing) release_at = now + 200;
        if (!controller.pressing && now >= release_at) pulse = 0;
        if (servo_enabled() && pulse != previous_pulse) {
            servo_pulse(pulse);
            previous_pulse = pulse;
            ESP_LOGI(log_tag, "HIT servo=%" PRIu32 " us phase=%u event=%" PRIu32,
                     pulse, squeeze.stage, controller.feedback_id);
        }
        /* Local FIRE/identity pattern owns the laser; laptop laser commands are ignored. */
        bool laser = fire.pressed && laser_identity_level(CONFIG_BB_CONTROLLER_ID,
                            laser_identity_gap_ms(CONFIG_BB_CONTROLLER_ID), now - pressed_at);
        output_update(laser, false);
        changed = changed || laser != previous_laser;
        previous_laser = laser;
        if (changed || now - last_send >= HEARTBEAT_MS) {
            char input[RADIO_BYTES + 1];
            int length = snprintf(input, sizeof(input),
                "{\"v\":1,\"type\":\"input\",\"id\":%d,\"boot\":%" PRIu32
                ",\"seq\":%" PRIu32 ",\"buttons\":%u,\"command_seq\":%" PRIu32 ",\"laser\":%s}",
                CONFIG_BB_CONTROLLER_ID, boot, ++seq, (fire.pressed ? 1U : 0U) | (special.pressed ? 2U : 0U),
                controller.command_seq, laser ? "true" : "false");
            if (length > 0 && length <= RADIO_BYTES &&
                esp_now_send(broadcast, (const uint8_t *)input, (size_t)length) != ESP_OK) failed++;
            last_send = now;
        }
        if (now >= status_at) {
            ESP_LOGI(log_tag, "ESP-NOW id=%d packets=%" PRIu32 " failed=%" PRIu32 " dropped=%" PRIu32
                     " ack=%" PRIu32 " lease=%d servo_phase=%u", CONFIG_BB_CONTROLLER_ID, seq, failed,
                     radio_dropped, controller.command_seq, controller.leased, squeeze.stage);
            status_at = now + 2000;
        }
        vTaskDelayUntil(&wake, pdMS_TO_TICKS(LOOP_MS));
    }
}
#endif

#ifdef CONFIG_BB_ESPNOW_RECEIVER
static void espnow_receiver_run(void)
{
    esp_log_level_set("*", ESP_LOG_NONE);
    nvs_initialize();
    ESP_ERROR_CHECK(uart_driver_install(UART_NUM_0, 4096, 8192, 0, NULL, 0));
    ESP_ERROR_CHECK(uart_set_baudrate(UART_NUM_0, RECEIVER_BAUD));
    radio_init();
    const char *ready = "{\"receiver\":\"ready\",\"baud\":460800,\"bidirectional\":true}\n";
    uart_write_bytes(UART_NUM_0, ready, strlen(ready));
    char line[RADIO_BYTES + 1];
    size_t used = 0;
    bool overflow = false;
    uint32_t received = 0, commands = 0, failed = 0;
    uint64_t status_at = 0;
    while (true) {
        uint8_t bytes[256];
        int count = uart_read_bytes(UART_NUM_0, bytes, sizeof(bytes), 0);
        for (int index = 0; index < count; index++) {
            if (bytes[index] == '\n') {
                line[used] = '\0';
                uint32_t id = 0;
                Command command = {0};
                if (!overflow && used && packet_id(line, used, "command", &id) &&
                    command_parse(line, used, &command)) {
                    if (esp_now_send(broadcast, (const uint8_t *)line, used) == ESP_OK) commands++;
                    else failed++;
                }
                used = 0;
                overflow = false;
            } else if (!overflow) {
                if (used < RADIO_BYTES) line[used++] = (char)bytes[index];
                else overflow = true;
            }
        }
        RadioPacket packet;
        for (unsigned index = 0; index < 32 && xQueueReceive(radio_queue, &packet, 0) == pdTRUE; index++) {
            uint32_t id = 0;
            if (packet_id(packet.data, packet.length, "input", &id)) {
                uart_write_bytes(UART_NUM_0, packet.data, packet.length);
                uart_write_bytes(UART_NUM_0, "\n", 1);
                received++;
            }
        }
        uint64_t now = time_ms();
        if (now >= status_at) {
            char status[200];
            int length = snprintf(status, sizeof(status), "{\"receiver\":\"alive\",\"channel\":%d,"
                "\"packets\":%" PRIu32 ",\"commands\":%" PRIu32 ",\"dropped\":%" PRIu32
                ",\"failed\":%" PRIu32 ",\"bidirectional\":true}\n",
                CONFIG_BB_ESPNOW_CHANNEL, received, commands, radio_dropped, failed);
            uart_write_bytes(UART_NUM_0, status, length);
            status_at = now + 2000;
        }
        vTaskDelay(pdMS_TO_TICKS(1));
    }
}
#endif

void app_main(void)
{
#ifdef CONFIG_BB_ESPNOW_RECEIVER
    espnow_receiver_run();
#endif
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
    nvs_initialize();
#ifdef CONFIG_BB_ESPNOW_CONTROLLER
    espnow_controller_run();
#endif
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
