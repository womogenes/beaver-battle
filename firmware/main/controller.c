#include "controller.h"

bool laser_bench_level(bool steady, bool pulse, uint64_t elapsed_ms)
{
    return pulse ? elapsed_ms % 500 < 250 : steady;
}

uint32_t laser_identity_period_ms(int controller_id)
{
    /* Only controller 2 blinks, so this is the period of the one pattern there is. The
       value is kept for controller 1 as well, where it is unused because its gap is zero
       and the laser never goes dark. */
    return controller_id == 2 ? 800 : 600;
}

uint32_t laser_identity_gap_ms(int controller_id)
{
    /* Controller 1 never blinks. Blinking costs detection, since a dot that is dark
       cannot be tracked and a frame that missed it looks the same as a gap, so the one
       controller that needs no gap to be recognised is better off without one: it is the
       dot that never goes dark, and it keeps the whole of its light for tracking.

       Controller 2 takes a fixed share of its period rather than a fixed number of
       milliseconds, which is what kept the two patterns level when there were three of
       them: one fixed gap gave the longest period the smallest share and so the least
       signal to correlate. */
    if (controller_id != 2) {
        return 0;
    }
    return laser_identity_period_ms(controller_id) * 22 / 100;
}

bool laser_identity_level(int controller_id, uint32_t gap_ms, uint64_t elapsed_ms)
{
    /* Lit except for one short blanking gap per period. The camera identifies a
       controller from how often the gap comes round, not from how long the laser is on:
       a missed frame looks exactly like a gap, so any measure of duty is confounded by
       tracking dropouts, while random dropouts leave the period where it was. */
    uint32_t period = laser_identity_period_ms(controller_id);
    if (gap_ms == 0 || gap_ms >= period) {
        return true;
    }
    return (uint32_t)(elapsed_ms % period) >= gap_ms;
}

bool serial_newer(uint32_t candidate, uint32_t previous)
{
    uint32_t distance = candidate - previous;
    return distance != 0 && distance < UINT32_C(0x80000000);
}

bool button_update(Button *button, bool pressed, uint64_t now_ms)
{
    if (pressed != button->candidate) {
        button->candidate = pressed;
        button->changed_ms = now_ms;
    }
    if (button->pressed != button->candidate &&
        now_ms - button->changed_ms >= BUTTON_DEBOUNCE_MS) {
        button->pressed = button->candidate;
        return true;
    }
    return false;
}

void controller_disconnect(Controller *controller)
{
    controller->leased = false;
    controller->laser = false;
    controller->pressing = false;
    controller->press_until_ms = 0;
}

uint32_t servo_jog(uint32_t pulse_us, bool lower, bool higher,
                   uint32_t minimum_us, uint32_t maximum_us, uint32_t step_us)
{
    if (pulse_us < minimum_us) {
        pulse_us = minimum_us;
    } else if (pulse_us > maximum_us) {
        pulse_us = maximum_us;
    }
    if (lower == higher) {
        return pulse_us;
    }
    if (lower) {
        return pulse_us - minimum_us > step_us ? pulse_us - step_us : minimum_us;
    }
    return maximum_us - pulse_us > step_us ? pulse_us + step_us : maximum_us;
}

void controller_tick(Controller *controller, uint64_t now_ms)
{
    if (controller->leased && now_ms - controller->last_command_ms >= COMMAND_LEASE_MS) {
        controller_disconnect(controller);
    }
    if (controller->pressing && now_ms >= controller->press_until_ms) {
        controller->pressing = false;
    }
}

bool controller_command(Controller *controller, const Command *command,
                        uint64_t now_ms, bool servo_enabled)
{
    controller_tick(controller, now_ms);
    bool new_session = !controller->have_session || controller->session != command->session;
    if (!new_session && command->seq != controller->command_seq &&
        !serial_newer(command->seq, controller->command_seq)) {
        return false;
    }
    bool reconnecting = new_session || !controller->leased;
    if (new_session) {
        controller_disconnect(controller);
        controller->have_session = true;
        controller->session = command->session;
        controller->feedback_id = 0;
    }
    bool new_feedback = command->feedback_id != 0 &&
        (controller->feedback_id == 0 || serial_newer(command->feedback_id, controller->feedback_id));
    if (new_feedback) {
        // Consume events even while disabled or cooling down: never queue/replay them.
        controller->feedback_id = command->feedback_id;
        if (!reconnecting && servo_enabled && !controller->pressing &&
            now_ms >= controller->cooldown_until_ms && command->duration_ms > 0) {
            uint32_t duration = command->duration_ms > FEEDBACK_MAX_MS ?
                FEEDBACK_MAX_MS : command->duration_ms;
            controller->pressing = true;
            controller->press_until_ms = now_ms + duration;
            controller->cooldown_until_ms = controller->press_until_ms + FEEDBACK_COOLDOWN_MS;
        }
    }
    controller->command_seq = command->seq;
    controller->last_command_ms = now_ms;
    controller->leased = true;
    controller->laser = command->laser;
    return true;
}
