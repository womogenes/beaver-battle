# Controller wiring

Use the GPIO labels printed on the classic ESP32 DevKit V1, not physical header positions. All three controllers share this wiring; assign distinct firmware IDs.

| Signal | Connection |
| --- | --- |
| Fire/thrust | GPIO27 → normally open button → GND |
| Special | GPIO32 → normally open button → GND |
| Laser control | GPIO25 → 330 Ω gate resistor → N-channel MOSFET gate |
| Servo signal | GPIO26 → servo signal input |
| Common ground | ESP32 GND, MOSFET source, servo ground, external supply negative |

Buttons use internal pull-ups and 15 ms debounce. They do not connect to 5 V. For four-legged switches, use contacts that are open when released and short together when pressed.

The servo's positive power wire goes to its rated external supply, not GPIO26 or the laser switch. Firmware leaves servo pulses disabled until its rest/press positions are configured and enabled. Use USB for ESP32 bench power; battery regulation is a separate design task. Do not connect raw 9 V to GPIO, 3V3, or the shown 5 V rail.

## Laser current limit

The reported laser specification is 5 V and at most 20 mA. This does not establish whether it is a bare diode or a module with a driver, nor its actual diode forward voltage. Identify the part before treating 5 V as a direct-drive voltage. A purpose-built laser current driver should keep operating current below the rated maximum, including tolerance and transients.

With the available resistors, **330 Ω ±5%, ¼ W in series with the laser supply** is a conservative steady-current limit for a regulated 5 V bench test. This is a second resistor, separate from the MOSFET gate resistor:

```text
regulated +5 V → 330 Ω current-limit resistor → laser (+)
laser (−) → MOSFET drain
MOSFET source → common GND
GPIO25 → 330 Ω gate resistor → MOSFET gate
MOSFET gate → 100 kΩ pull-down → MOSFET source/GND
```

Even with zero laser/switch voltage drop, current is at most `5.25 V / (330 Ω × 0.95) = 16.75 mA`, allowing a 5% high supply and 5% low resistor. Maximum resistor dissipation under that assumption is about 0.088 W. The resistor may leave insufficient voltage for the laser to operate; it is not a constant-current driver. This bound assumes the supply never exceeds 5.25 V, correct polarity, and the resistor is actually in series.

Once diode and switch drops are known, the nominal relation is `R = (Vsupply − Vlaser − Vswitch) / I`, with component and supply tolerances included. A 5 V-rated module that actually needs nearly 5 V cannot also receive a large series voltage drop from a 5 V source. Do not remove current limiting merely because it does not light. See [Wavelength Electronics' current-limit calculation](https://www.teamwavelength.com/faq/faq1079/).

The available 2N7000 is a switch, not a current regulator. Its low on-resistance is not guaranteed at 3.3 V gate drive; verify voltage drop at the intended current or use a switch specified at that gate voltage. Check the exact manufacturer's drain/gate/source lead diagram. Do not connect laser negative directly to ground as well, which would bypass the MOSFET. See the [onsemi 2N7000 datasheet](https://www.onsemi.com/download/data-sheet/pdf/2n7000ta-d.pdf).

## Button monitor

Flash the controller build, then open a 115200-baud serial monitor. Each press/release reports `FIRE GPIO27 PRESSED/RELEASED` or `SPECIAL GPIO32 PRESSED/RELEASED`. This works with empty Wi-Fi credentials. The laser stays off without host commands, and servo output is disabled by default. Close the monitor before the next firmware upload.
