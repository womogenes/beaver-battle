"""Run from the repository root: .venv/bin/python -m checks.check_blink.

The Arduino sketch reimplements two things the ESP-IDF firmware already has, because
Arduino cannot build the firmware itself: the identity blink and the button settle. Both
are compiled here from controller.c and compared against the sketch's own arithmetic, so
a change to one that is not made to the other fails rather than reaching a board.
"""

import re
import subprocess
import tempfile
from pathlib import Path

SKETCH = Path("firmware/arduino/identity_blink/identity_blink.ino")
PROBE = r"""
#include <stdio.h>
#include "controller.h"

int main(void)
{
    for (int id = 1; id <= 3; id++) {
        printf("%d %u\n", id, laser_identity_period_ms(id));
        for (uint32_t ms = 0; ms < 3000; ms++) {
            printf("%d", laser_identity_level(id, GAP_MS, ms) ? 1 : 0);
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


def firmware_says(gap):
    with tempfile.TemporaryDirectory() as work:
        source = Path(work) / "probe.c"
        source.write_text(PROBE.replace("GAP_MS", str(gap)))
        binary = Path(work) / "probe"
        subprocess.run(["cc", "-std=c11", "-Wall", "-Werror", "-I", "firmware/main",
                        str(source), "firmware/main/controller.c", "-o", str(binary)], check=True)
        return subprocess.run([str(binary)], check=True, capture_output=True, text=True).stdout


def sketch_period(controller_id):
    return {2: 800, 3: 1000}.get(controller_id, 600)


def sketch_level(controller_id, gap, elapsed):
    period = sketch_period(controller_id)
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
    gap = sketch_constant("GAP_MS")
    debounce = sketch_constant("DEBOUNCE_MS")
    lines = firmware_says(gap).strip().splitlines()
    for index, controller_id in enumerate((1, 2, 3)):
        stated_id, period = lines[index * 2].split()
        assert int(stated_id) == controller_id
        assert int(period) == sketch_period(controller_id), \
            f"controller {controller_id}: firmware says {period}, sketch says {sketch_period(controller_id)}"
        mine = "".join("1" if sketch_level(controller_id, gap, ms) else "0" for ms in range(3000))
        assert lines[index * 2 + 1] == mine, \
            f"controller {controller_id}: blink differs from the firmware within three seconds"
    assert lines[6] == sketch_button(debounce), "button settle differs from button_update"


def check_the_laser_starts_dark():
    """Nothing may light the laser but a held button, at power-up or ever."""
    text = SKETCH.read_text()
    assert 'digitalWrite(LASER_GPIO, LOW);' in text, "the sketch must drive the laser low in setup"
    setup = text[text.index("void setup()"):text.index("void loop()")]
    assert "HIGH" not in setup, "setup must never drive the laser high"
    loop = text[text.index("void loop()"):]
    assert "fire.pressed &&" in loop, "the laser must be gated on the button being held"
    assert "INPUT_PULLUP" in text and "== LOW" in text, "the switch pulls the pin to ground"


check_parity()
check_the_laser_starts_dark()
print("Blink checks passed: sketch and firmware agree on period, blink and button settle; "
      "laser starts dark and needs FIRE held")
