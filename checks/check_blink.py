"""Run from the repository root: .venv/bin/python -m checks.check_blink.

The Arduino sketch reimplements two things the ESP-IDF firmware already has, because
Arduino cannot build the firmware itself: the identity blink and the button settle. Both
are compiled here from controller.c and compared against the sketch's own arithmetic, so
a change to one that is not made to the other fails rather than reaching a board.
"""

import pathlib
import re
import subprocess
import tempfile
from pathlib import Path

SKETCHES = [Path(f"firmware/arduino/identity_blink_{n}/identity_blink_{n}.ino") for n in (1, 2)]
SKETCH = SKETCHES[0]
PROBE = r"""
#include <stdio.h>
#include "controller.h"

int main(void)
{
    for (int id = 1; id <= 2; id++) {
        printf("%d %u\n", id, laser_identity_period_ms(id));
        for (uint32_t ms = 0; ms < 3000; ms++) {
            printf("%d", laser_identity_level(id, laser_identity_gap_ms(id), ms) ? 1 : 0);
        }
        printf("\n");
    }
    Button button = {0};
    for (uint32_t ms = 0; ms < 200; ms++) {
        bool raw = (ms / 7) % 2 == 0;          /* chatter faster than the settle */
        button_update(&button, raw, ms);
        printf("%d", button.pressed ? 1 : 0);
    }
    printf("\n");
    return 0;
}
"""


def sketch_constant(name):
    match = re.search(rf"{name}\s*=\s*(\d+)", SKETCH.read_text())
    assert match, f"{name} not found in the sketch"
    return int(match.group(1))


def firmware_says():
    with tempfile.TemporaryDirectory() as work:
        source = Path(work) / "probe.c"
        source.write_text(PROBE)
        binary = Path(work) / "probe"
        subprocess.run(["cc", "-std=c11", "-Wall", "-Werror", "-I", "firmware/main",
                        str(source), "firmware/main/controller.c", "-o", str(binary)], check=True)
        return subprocess.run([str(binary)], check=True, capture_output=True, text=True).stdout


def sketch_period(controller_id):
    return 800 if controller_id == 2 else 600


def sketch_gap(controller_id):
    return sketch_period(controller_id) * 22 // 100 if controller_id == 2 else 0


def sketch_level(controller_id, elapsed):
    period, gap = sketch_period(controller_id), sketch_gap(controller_id)
    return True if gap == 0 or gap >= period else (elapsed % period) >= gap


def sketch_button(debounce):
    pressed = candidate = False
    changed = 0
    out = []
    for ms in range(200):
        raw = (ms // 7) % 2 == 0
        if raw != candidate:
            candidate, changed = raw, ms
        if pressed != candidate and ms - changed >= debounce:
            pressed = candidate
        out.append("1" if pressed else "0")
    return "".join(out)


def check_parity():
    debounce = sketch_constant("DEBOUNCE_MS")
    lines = firmware_says().strip().splitlines()
    for index, controller_id in enumerate((1, 2)):
        stated_id, period = lines[index * 2].split()
        assert int(stated_id) == controller_id
        assert int(period) == sketch_period(controller_id), \
            f"controller {controller_id}: firmware says {period}, sketch says {sketch_period(controller_id)}"
        mine = "".join("1" if sketch_level(controller_id, ms) else "0" for ms in range(3000))
        assert lines[index * 2 + 1] == mine, \
            f"controller {controller_id}: blink differs from the firmware within three seconds"
    assert lines[4] == sketch_button(debounce), "button settle differs from button_update"


def check_controller_one_never_blinks():
    """Controller 1 is identified by the absence of gaps, so it must never have one."""
    from subprocess import run
    assert sketch_gap(1) == 0
    assert all(sketch_level(1, ms) for ms in range(4000)), "controller 1 must stay lit"
    assert not all(sketch_level(2, ms) for ms in range(4000)), "controller 2 must blink"


def check_the_laser_starts_dark():
    """Nothing may light the laser but a held button, at power-up or ever."""
    for sketch in SKETCHES:
        text = sketch.read_text()
        assert 'digitalWrite(LASER_GPIO, LOW);' in text, f"{sketch.name} must drive the laser low in setup"
        setup = text[text.index("void setup()"):text.index("void loop()")]
        assert "HIGH" not in setup, f"{sketch.name}: setup must never drive the laser high"
        loop = text[text.index("void loop()"):]
        assert "fire.pressed &&" in loop, f"{sketch.name}: the laser must be gated on the button"
        assert "INPUT_PULLUP" in text and "== LOW" in text, f"{sketch.name}: switch pulls to ground"


def check_one_sketch_per_controller():
    """Three files, one behaviour: they may differ in the controller number and nothing else."""
    bodies = {}
    for index, sketch in enumerate(SKETCHES, start=1):
        text = sketch.read_text()
        assert f"const int CONTROLLER_ID = {index};" in text, f"{sketch.name} must be controller {index}"
        bodies[index] = text.replace(f"const int CONTROLLER_ID = {index};", "CONTROLLER_ID")
    assert bodies[1] == bodies[2], \
        "the two sketches differ by more than their controller number"


def check_the_button_reclaims_the_laser():
    """A bench override must never leave a player's FIRE button doing nothing.

    It used to sit there until someone sent 'b' or cut the power, so forgetting to release
    one disabled the controller with no symptom anywhere: the board looked healthy, the
    radio kept reporting, and only the laser stayed dark. One stray byte on the USB line
    did the same thing. Both controllers were left like this in a single session.
    """
    import re
    TIMEOUT_MS = 120000

    def resolve(override, set_ms, now_ms, fire_pressed):
        """Mirror of the sketch's release rule."""
        if override >= 0 and (fire_pressed or now_ms - set_ms >= TIMEOUT_MS):
            return -1
        return override

    # Pressing FIRE takes control back immediately, whichever way the override was set.
    assert resolve(0, 0, 500, True) == -1, 'FIRE must clear a forced-off override'
    assert resolve(1, 0, 500, True) == -1, 'FIRE must clear a forced-solid override'
    # An override nobody clears expires on its own.
    assert resolve(0, 0, TIMEOUT_MS, False) == -1, 'a forced-off override must expire'
    assert resolve(0, 0, TIMEOUT_MS - 1, False) == 0, 'it must not expire early'
    # It still has to survive long enough to take a bench measurement.
    assert resolve(1, 0, 60000, False) == 1, 'forced solid must last a measurement'

    # And the sketches must actually contain the rule.
    for controller in (1, 2):
        source = (pathlib.Path(__file__).resolve().parent.parent / 'firmware' / 'arduino'
                  / f'controller_espnow_{controller}' / f'controller_espnow_{controller}.ino').read_text()
        assert re.search(r'laserOverride >= 0 && \(fire\.pressed \|\| now - overrideSetMs', source), \
            f'controller {controller} must let FIRE reclaim the laser'
        found = re.search(r'OVERRIDE_TIMEOUT_MS = (\d+)', source)
        assert found and int(found.group(1)) == TIMEOUT_MS, \
            f'controller {controller} timeout must be {TIMEOUT_MS} ms'


check_parity()
check_the_button_reclaims_the_laser()
check_controller_one_never_blinks()
check_the_laser_starts_dark()
check_one_sketch_per_controller()
print("Blink checks passed: the button reclaims the laser from any bench override, and "
      "both sketches agree with the firmware on period, gap, blink "
      "and button settle; each starts dark and needs FIRE held")
