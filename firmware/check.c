#include <assert.h>
#include <stdio.h>

#include "main/controller.h"

int main(void)
{
    Button button = {0};
    assert(!button_update(&button, true, 0));
    assert(!button_update(&button, false, 3));
    assert(!button_update(&button, true, 7));
    assert(!button_update(&button, true, 21));
    assert(button_update(&button, true, 22));
    assert(button.pressed);
    assert(!button_update(&button, false, 30));
    assert(button_update(&button, false, 45));
    assert(!button.pressed);

    Controller controller = {0};
    Command command = {.session = 5, .seq = 1, .laser = true, .duration_ms = 200};
    assert(!controller.laser && !controller.pressing);
    assert(controller_command(&controller, &command, 100, true));
    assert(controller.laser && controller.command_seq == 1);
    command.seq = 0;
    command.laser = false;
    assert(!controller_command(&controller, &command, 200, true));
    assert(controller.laser);
    command.seq = 1;
    command.laser = true;
    assert(controller_command(&controller, &command, 400, true));
    controller_tick(&controller, 899);
    assert(controller.laser);
    controller_tick(&controller, 900);
    assert(!controller.laser && !controller.leased);

    // First feedback after lease loss is consumed, not replayed.
    command.seq = 2;
    command.feedback_id = 1;
    assert(controller_command(&controller, &command, 1000, true));
    assert(controller.laser && !controller.pressing && controller.feedback_id == 1);
    command.seq = 3;
    command.feedback_id = 2;
    command.duration_ms = 900;
    assert(controller_command(&controller, &command, 1100, true));
    assert(controller.pressing && controller.press_until_ms == 1600);
    assert(controller.cooldown_until_ms == 3600);
    assert(controller_command(&controller, &command, 1450, true));
    assert(controller.press_until_ms == 1600);
    controller_tick(&controller, 1600);
    assert(!controller.pressing && controller.laser);
    command.seq = 4;
    command.feedback_id = 3;
    assert(controller_command(&controller, &command, 1700, true));
    assert(!controller.pressing && controller.feedback_id == 3);
    for (uint64_t now = 2000; now <= 3600; now += 400) {
        assert(controller_command(&controller, &command, now, true));
        assert(!controller.pressing);
    }
    command.seq = 5;
    command.feedback_id = 4;
    command.duration_ms = 100;
    assert(controller_command(&controller, &command, 3700, true));
    assert(controller.pressing);
    controller_disconnect(&controller);
    assert(!controller.pressing && !controller.laser);

    // Session changes never replay a first event, or clear mechanical cooldown.
    command.session = 6;
    command.seq = 1;
    command.feedback_id = 1;
    assert(controller_command(&controller, &command, 3800, true));
    assert(!controller.pressing && controller.cooldown_until_ms == 5800);
    command.seq = 2;
    command.feedback_id = 2;
    assert(controller_command(&controller, &command, 3900, true));
    assert(!controller.pressing);

    // Disabled feedback is also consumed, so enabling later cannot replay it.
    Controller disabled = {0};
    command.feedback_id = 0;
    assert(controller_command(&disabled, &command, 0, false));
    command.seq++;
    command.feedback_id = 1;
    assert(controller_command(&disabled, &command, 100, false));
    assert(!disabled.pressing && disabled.feedback_id == 1);
    assert(controller_command(&disabled, &command, 200, true));
    assert(!disabled.pressing);
    assert(serial_newer(0, UINT32_MAX));
    assert(!serial_newer(UINT32_MAX, 0));
    assert(!serial_newer(2, 2));
    assert(servo_jog(1500, false, false, 1200, 1800, 5) == 1500);
    assert(servo_jog(1500, true, true, 1200, 1800, 5) == 1500);
    assert(servo_jog(1500, true, false, 1200, 1800, 5) == 1495);
    assert(servo_jog(1500, false, true, 1200, 1800, 5) == 1505);
    assert(servo_jog(1202, true, false, 1200, 1800, 5) == 1200);
    assert(servo_jog(1798, false, true, 1200, 1800, 5) == 1800);
    assert(servo_jog(1200, true, false, 1200, 1800, 5) == 1200);
    assert(servo_jog(1800, false, true, 1200, 1800, 5) == 1800);
    assert(servo_jog(1500, true, false, 1000, 2000, 25) == 1475);
    assert(servo_jog(1500, false, true, 1000, 2000, 25) == 1525);
    assert(servo_jog(1010, true, false, 1000, 2000, 25) == 1000);
    assert(servo_jog(1990, false, true, 1000, 2000, 25) == 2000);
    puts("Firmware logic checks passed: debounce, lease, order, cooldown, deduplication, reconnect, servo jogging.");
    return 0;
}
