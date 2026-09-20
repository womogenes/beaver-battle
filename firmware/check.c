#include <assert.h>
#include <stdio.h>

#include "main/controller.h"

int main(void)
{
    assert(!laser_bench_level(false, false, 0));
    assert(laser_bench_level(true, false, 250));
    assert(laser_bench_level(false, true, 0));
    assert(laser_bench_level(false, true, 249));
    assert(!laser_bench_level(false, true, 250));
    assert(!laser_bench_level(true, true, 499));
    assert(laser_bench_level(false, true, 500));
    assert(!laser_bench_level(false, false, 500));
    assert(servo_jog(1500, true, false, 544, 2400, 50) == 1450);
    assert(servo_jog(1500, false, true, 544, 2400, 50) == 1550);
    assert(servo_jog(560, true, false, 544, 2400, 50) == 544);
    assert(servo_jog(2380, false, true, 544, 2400, 50) == 2400);

    /* Identity blink: lit except for one gap per period, unique period per controller. */
    assert(laser_identity_period_ms(1) == 600);
    assert(laser_identity_period_ms(2) == 800);
    for (int id = 1; id <= 2; id++) {
        uint32_t period = laser_identity_period_ms(id);
        assert(!laser_identity_level(id, 133, 0));
        assert(!laser_identity_level(id, 133, 132));
        assert(laser_identity_level(id, 133, 133));
        assert(laser_identity_level(id, 133, period - 1));
        assert(!laser_identity_level(id, 133, period));
        assert(!laser_identity_level(id, 133, period * 5));
        /* The gap is a small part of each period, so tracking barely suffers. */
        int dark = 0;
        for (uint32_t ms = 0; ms < period; ms++) {
            if (!laser_identity_level(id, 133, ms)) {
                dark++;
            }
        }
        assert(dark == 133);
        assert(dark * 100 / (int)period <= 23);
    }
    /* The gap holds a constant share of the period, so no controller carries less
       signal than another. */
    /* Controller 1 never goes dark; that absence is what identifies it. */
    assert(laser_identity_gap_ms(1) == 0);
    for (uint32_t ms = 0; ms < 4000; ms++) {
        assert(laser_identity_level(1, laser_identity_gap_ms(1), ms));
    }
    assert(laser_identity_gap_ms(2) == 176);
    for (int id = 2; id <= 2; id++) {
        uint32_t period = laser_identity_period_ms(id);
        uint32_t gap = laser_identity_gap_ms(id);
        int dark = 0;
        for (uint32_t ms = 0; ms < period; ms++) {
            if (!laser_identity_level(id, gap, ms)) {
                dark++;
            }
        }
        assert(dark * 100 / (int)period == 22);
    }

    /* A zero or oversized gap leaves the laser simply on, never dark. */
    assert(laser_identity_level(1, 0, 0));
    assert(laser_identity_level(1, 600, 0));
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
    ServoSqueeze squeeze = {0};
    assert(!servo_squeeze_start(&squeeze, 0, 1600, 0));
    assert(!servo_squeeze_start(&squeeze, 1500, 1500, 0));
    assert(servo_squeeze_start(&squeeze, 1300, 1750, 10));
    assert(squeeze.pulse_us == 1300 && squeeze.stage == 1);
    assert(!servo_squeeze_start(&squeeze, 1300, 1750, 20));
    assert(servo_squeeze_tick(&squeeze, 509) == 1300);
    assert(servo_squeeze_tick(&squeeze, 510) == 1750 && squeeze.stage == 2);
    assert(servo_squeeze_tick(&squeeze, 1009) == 1750);
    assert(servo_squeeze_tick(&squeeze, 1010) == 1300 && squeeze.stage == 3);
    assert(servo_squeeze_tick(&squeeze, 1510) == 1300 && squeeze.stage == 0);
    assert(servo_squeeze_start(&squeeze, 1750, 1300, 2000));
    assert(servo_squeeze_tick(&squeeze, 9000) == 1300 && squeeze.stage == 2);
    assert(servo_squeeze_tick(&squeeze, 9001) == 1300); /* no skipped dwell on late tick */
    squeeze.stage = 0; /* cancellation holds the current pulse */
    assert(servo_squeeze_tick(&squeeze, 9501) == 1300);
    Controller radio = {0};
    ServoSqueeze hit = {0};
    Command event = {.session = 77, .seq = 1, .feedback_id = 11, .duration_ms = 200};
    assert(controller_squeeze_command(&radio, &hit, &event, 0, true, 990, 570));
    assert(!radio.pressing && !hit.stage);
    event.seq++; event.feedback_id++;
    assert(controller_squeeze_command(&radio, &hit, &event, 100, true, 990, 570));
    assert(hit.stage == 1 && hit.end_us == 675);
    uint32_t previous = 990, minimum = 990;
    for (uint64_t now = 120; now <= 1700; now += 20) {
        if (now == 700) { event.seq++; event.feedback_id++; }
        assert(controller_squeeze_command(&radio, &hit, &event, now, true, 990, 570));
        uint32_t pulse = controller_squeeze_tick(&radio, &hit, now, 990);
        assert(pulse >= 675 && pulse <= 990);
        assert((pulse > previous ? pulse - previous : previous - pulse) <= 10);
        if (pulse < minimum) minimum = pulse;
        previous = pulse;
    }
    assert(minimum == 675 && previous == 990 && !radio.pressing && !hit.stage);
    assert(radio.feedback_id == 13);
    event.seq++; event.feedback_id++;
    controller_squeeze_command(&radio, &hit, &event, 1800, true, 990, 570);
    assert(!radio.pressing); /* cooldown consumes, never queues */
    for (uint64_t now = 2000; now <= 4000; now += 200)
        controller_squeeze_command(&radio, &hit, &event, now, true, 990, 570);
    assert(!radio.pressing); /* duplicate does not fire after cooldown */
    event.seq++; event.feedback_id++;
    controller_squeeze_command(&radio, &hit, &event, 4100, true, 990, 570);
    assert(radio.pressing);
    controller_squeeze_tick(&radio, &hit, 4400, 990);
    assert(hit.pulse_us == 980); /* late tick never jumps to catch up */
    assert(controller_squeeze_tick(&radio, &hit, 4600, 990) == 990);
    assert(!radio.leased && !hit.stage);
    event.seq++; event.feedback_id++;
    controller_squeeze_command(&radio, &hit, &event, 4700, true, 990, 570);
    assert(!radio.pressing); /* reconnect does not replay */
    Controller uncalibrated = {0};
    ServoSqueeze idle = {0};
    controller_squeeze_command(&uncalibrated, &idle, &event, 0, false, 990, 570);
    event.seq++; event.feedback_id++;
    controller_squeeze_command(&uncalibrated, &idle, &event, 100, false, 990, 570);
    assert(!uncalibrated.pressing && !idle.stage);
    Controller reverse = {0};
    ServoSqueeze rising = {0};
    controller_squeeze_command(&reverse, &rising, &event, 0, true, 570, 990);
    event.seq++; event.feedback_id++;
    controller_squeeze_command(&reverse, &rising, &event, 100, true, 570, 990);
    assert(rising.end_us == 885);
    assert(controller_squeeze_tick(&reverse, &rising, 120, 570) == 580);
    event.session++;
    controller_squeeze_command(&reverse, &rising, &event, 200, true, 570, 990);
    assert(controller_squeeze_tick(&reverse, &rising, 200, 570) == 570 && !rising.stage);
    puts("Firmware logic checks passed: debounce, lease, order, cooldown, deduplication, reconnect, servo jogging and calibrated hit squeeze.");
    return 0;
}
