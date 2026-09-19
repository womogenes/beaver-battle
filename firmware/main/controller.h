#ifndef BEAVER_CONTROLLER_H
#define BEAVER_CONTROLLER_H

#include <stdbool.h>
#include <stdint.h>

enum { COMMAND_LEASE_MS = 500, FEEDBACK_MAX_MS = 500, FEEDBACK_COOLDOWN_MS = 2000,
       BUTTON_DEBOUNCE_MS = 15 };

typedef struct {
    uint32_t session;
    uint32_t seq;
    uint32_t feedback_id;
    uint32_t duration_ms;
    bool laser;
} Command;

typedef struct {
    uint32_t session;
    uint32_t command_seq;
    uint32_t feedback_id;
    uint64_t last_command_ms;
    uint64_t press_until_ms;
    uint64_t cooldown_until_ms;
    bool have_session;
    bool leased;
    bool laser;
    bool pressing;
} Controller;

typedef struct {
    uint64_t changed_ms;
    bool candidate;
    bool pressed;
} Button;

bool serial_newer(uint32_t candidate, uint32_t previous);
bool button_update(Button *button, bool pressed, uint64_t now_ms);
uint32_t servo_jog(uint32_t pulse_us, bool lower, bool higher,
                   uint32_t minimum_us, uint32_t maximum_us, uint32_t step_us);
void controller_tick(Controller *controller, uint64_t now_ms);
void controller_disconnect(Controller *controller);
bool controller_command(Controller *controller, const Command *command,
                        uint64_t now_ms, bool servo_enabled);

#endif
