# ESP32 controller firmware

The controller targets the classic ESP32/WROOM 30-pin DevKit V1. It uses pure C,
ESP-IDF, and the built-in cJSON component. The PlatformIO environment pins
`espressif32@6.12.0`; a normal ESP-IDF project is also included.

## Signals

| GPIO | Function | Configuration |
| --- | --- | --- |
| 25 | Laser driver's logic input | LEDC channel 0, timer 0, 5 kHz, 10-bit duty; off at startup |
| 26 | Servo signal | LEDC channel 1, timer 1, 50 Hz; disabled by default |
| 27 | Fire/thrust button | Switch to GND, internal pull-up, active low |
| 32 | Special button | Switch to GND, internal pull-up, active low |

Each button must remain stable for 15 ms before its state changes. These are GPIO
numbers, not header positions. GPIO25 is a 3.3 V control signal for an external
laser driver/switch; do not connect the 5 V laser supply to a GPIO. Servo power,
laser power, battery selection, and the water mechanism remain separate bench
assembly work. Signal devices need the appropriate common ground reference.

## Configure and build

From the repository root:

```sh
uv tool run --python 3.12 --from platformio platformio run -d firmware -t menuconfig
```

Open **Beaver controller** and set the following for each board:

- Unique controller ID: `1`, `2`, or `3`.
- Wi-Fi SSID/password and the laptop's reachable IPv4 address; UDP port defaults
  to `4210`, matching the game.
- Leave **Enable calibrated servo output** off initially.
- Adjust laser duty only if required; it changes brightness, not identity coding.

The board uses 2.4 GHz station Wi-Fi. This configuration supports open and
password-based networks, not an enterprise-login flow or captive portal. The
laptop must accept inbound UDP 4210 and the Wi-Fi network must permit clients to
communicate. Set an explicit laptop IP; no broadcast discovery is assumed.

For scripted initial configuration, create ignored `firmware/sdkconfig.local`
before the first build, using Kconfig assignments:

```ini
CONFIG_BB_CONTROLLER_ID=1
CONFIG_BB_WIFI_SSID="your-network"
CONFIG_BB_WIFI_PASSWORD="your-password"
CONFIG_BB_LAPTOP_IP="192.168.1.2"
```

`sdkconfig.local` supplies defaults only when configuration is first generated;
afterward, use `menuconfig` to change the saved configuration. PlatformIO stores
it in `firmware/sdkconfig.esp32dev`; native ESP-IDF stores it in
`firmware/sdkconfig`. Local configuration, compiled images, and build output are
ignored because firmware embeds the configured credentials. Do not commit them.

```sh
uv tool run --python 3.12 --from platformio platformio run -d firmware
uv tool run --python 3.12 --from platformio platformio device list
uv tool run --python 3.12 --from platformio platformio run -d firmware -t upload --upload-port /dev/ttyUSB0
uv tool run --python 3.12 --from platformio platformio device monitor -b 115200 -p /dev/ttyUSB0
```

Select the actual serial port. If automatic flashing fails, hold BOOT while the
uploader connects, then release it. Configure and flash the three boards one at a
time with distinct IDs. Uploading identical ID settings to all three is not a
valid multiplayer setup. Stop a serial monitor before flashing the same port.

With an installed ESP-IDF environment instead of PlatformIO:

```sh
cd firmware
idf.py set-target esp32
idf.py menuconfig
idf.py build
idf.py -p /dev/ttyUSB0 flash monitor
```

## Runtime behavior

For a standalone laser wiring test, enable **Beaver controller → Laser button
bench test** (`CONFIG_BB_LASER_BUTTON_TEST=y`) and rebuild/flash. In this mode,
holding FIRE gives steady laser output; holding SPECIAL gives a 2 Hz pulse
(250 ms on / 250 ms off), starting with ON. SPECIAL takes priority if both are
held. Releasing both turns the laser off after the normal 15 ms debounce.
The current rewired bench board uses `CONFIG_BB_FIRE_GPIO=14` (D14 steady)
and `CONFIG_BB_SPECIAL_GPIO=27` (D27 pulse), with the laser gate still D25.
Both buttons connect their GPIO to GND. Set these options in the ignored local
sdkconfig; shared defaults remain GPIO27/GPIO32 for earlier controllers.
The servo is disabled unless the servo bench option is also enabled.
The test runs without starting Wi-Fi or UDP. Startup identifies **LASER BUTTON
TEST** and logs `LASER GPIO25 ON` / `LASER GPIO25 OFF` transitions alongside
button events. Every 500 ms, `BUTTON STATUS` also reports both raw GPIO levels
(0 means pressed, 1 released), debounced button states, and commanded laser
state. This heartbeat confirms the firmware is running even without button
transitions; laser state is a software command, not measured optical output.
Disable this option and rebuild/flash to restore normal game
operation and its laptop-controlled laser lease. The option defaults off in
the shared project; a board's ignored local sdkconfig can enable it for testing.

For a positional-servo direction test, enable **Servo button bench test** (`CONFIG_BB_SERVO_BUTTON_TEST=y`) and
rebuild/flash. GPIO33 produces 50 Hz pulses starting at 1500 microseconds. Hold
FIRE to lower the pulse; hold SPECIAL to raise it (currently D14 and D27). The default
step is 5 microseconds every 20 ms (250 microseconds/second);
`BB_SERVO_TEST_STEP_US` adjusts command speed from 1 to 100 microseconds per
20 ms. For example, a 25-microsecond step commands 1250 microseconds/second:
1000–2000 microseconds takes 0.8 seconds end to end, or 0.4 seconds from the
1500-microsecond center to either limit. Actual servo motion can lag the command.
Releasing both buttons
or holding both keeps the last commanded position. Default limits are
1200–1800 microseconds; `BB_SERVO_TEST_MIN_US` and `BB_SERVO_TEST_MAX_US` let you
adjust the limits during calibration. These are bounded pulse-width calibration
settings, not an angle mapping or a promise of full 180-degree travel. Actual
rotation direction depends on the
servo and mounting; this mode assumes a positional servo, not a continuous
rotation servo. The laser stays off unless the laser bench option is also enabled. Wi-Fi/UDP do not start. Serial output
identifies **SERVO BUTTON TEST**, reports button edges and the current pulse
width. Both options default off; enabling both combines their behavior. Disable
both bench options and rebuild/flash to return to normal game operation.

Current combined test settings: D14 gives steady laser and lowers the servo
pulse; D27 pulses the laser at 2 Hz and raises the servo pulse. D33 carries
the servo signal. Both buttons hold servo position while the laser pulses;
neither holds servo position with laser off. The local build uses 544–2400 us
limits and 50 us steps every 20 ms, twice the preceding 25 us jog rate.
This doubles commanded speed, not necessarily the motor’s physical speed.

The serial monitor at 115200 baud reports every debounced button transition,
including when Wi-Fi is unconfigured or disconnected. For example:

```text
I (1234) beaver: FIRE GPIO27 PRESSED
I (1567) beaver: FIRE GPIO27 RELEASED
I (2345) beaver: SPECIAL GPIO32 PRESSED
I (2678) beaver: SPECIAL GPIO32 RELEASED
```

The timestamp is milliseconds since boot. Holding a button produces one press
line; releasing it produces one release line. In normal game mode, with no
laptop connection the laser remains off, and the servo remains disabled with
the default settings.

The exact wire format is in [PROTOCOL.md](../PROTOCOL.md). The controller sends
button state every 20 ms and immediately after debounced changes. It also sends
an immediate acknowledgement after an accepted output command. Each boot has a
random 32-bit ID; packet sequence counters wrap using unsigned serial ordering.
The UDP socket binds an ephemeral port and accepts commands only from the
configured laptop IP and port. This is endpoint filtering, not authentication.

The 5 ms controller loop handles network messages, buttons, and outputs without
blocking on a press or laser identification window. It accepts repeated commands
to renew the lease and ignores older command sequence numbers. If Wi-Fi drops,
outputs return to their inactive state immediately; if commands stop arriving,
the 500 ms lease expires. Laser state acknowledgements report the applied driver
gate setting, not an optical measurement of the dot.

With servo output enabled, each new feedback event presses for the requested
duration, capped at 500 ms, then commands the configured rest pulse. A two-second
cooldown starts when the scheduled press ends. Events received during cooldown
are consumed, not queued. Duplicate feedback IDs do not extend a press or repeat
it. The first command after boot, a new host session, or lost command lease
establishes current state and consumes any included feedback ID without firing;
this prevents effects accumulated while disconnected from replaying. The laptop
must send a fresh event ID afterward to request a new press.

Servo output is **disabled by default**: GPIO33 remains low and no PWM pulses are
sent. This does not physically put an attached mechanism at rest. Once enabled,
startup and connection loss command `CONFIG_BB_SERVO_REST_US`; movement uses
`CONFIG_BB_SERVO_PRESS_US`. The defaults of 1500 and 1600 microseconds are
placeholders, not calibrated actuator positions. A commanded rest pulse or lost
PWM does not prove mechanical release.

## Bench checklist

1. Build the image and run the portable logic check below. These checks require
   no controller and do not count as hardware validation.
2. With outputs disconnected, verify each board boots with its own ID, joins
   Wi-Fi, and appears in the game's lobby. Confirm button press/release masks
   `1` (fire), `2` (special), and `3` (both), including brief switch bounce.
3. Verify GPIO25 starts low and follows host laser commands. Disconnect the host
   while the laser gate is on: it must turn off within 500 ms plus one loop tick.
   Check that Wi-Fi reconnection restores fresh input and output commands.
4. Calibrate the servo rest position and allowed travel with the mechanism
   unloaded. Start with rest and press pulse widths equal, enable servo output,
   then adjust press travel deliberately. Check press/return without binding
   before attaching the final mechanism.
5. Trigger a hit: one new event must cause one stroke. Repeated packets of that
   same event must not repeat it. A second event during cooldown must be dropped;
   a fresh event after cooldown can press again. Test command loss during a
   press, host restart, and controller restart.
6. Confirm all three controllers work simultaneously; verify laser identity and
   servo feedback with the complete camera/projector/game setup.

Portable logic check, from the repository root:

```sh
cc -std=c11 -Wall -Wextra -Werror -pedantic firmware/check.c firmware/main/controller.c -o /tmp/beaver-firmware-check
/tmp/beaver-firmware-check
```

The check exercises button bounce, exact lease expiry, out-of-order commands,
feedback deduplication, pulse-duration limits, cooldown, reconnect/session
handling, disabled feedback, sequence wrap, and servo jog direction/limits/hold.
Physical pin timing, radio
## ESP-NOW, with a third board as the receiver

`controller_espnow_1`, `_2` and `receiver_espnow` avoid the venue's network completely.
ESP-NOW is peer to peer on the Wi-Fi radio: no access point, no association, no DHCP and
nothing from the room in the path. That removes the failure this project has already hit,
where client isolation stopped controllers reaching the laptop while the internet worked.

A laptop cannot speak ESP-NOW, which is why the third board is needed. It listens and
writes each packet to USB serial as a line, and `beaver_battle.relay` turns those lines
back into the UDP datagrams the game already expects, so the bridge, the protocol and the
game are untouched and the radio can change without any of them noticing.

    uv run --with pyserial python -m beaver_battle.relay --port /dev/cu.usbserial-0001

The receiver's serial link runs at 460800, which `beaver_battle.relay` defaults to. It is
not a free choice. Two controllers at 50 Hz offer about 10 kB/s of JSON, and 115200 8N1
carries 11.5 kB/s, so the link sat at 86 percent and Arduino's `Serial.write` blocks once
the transmit buffer fills. That block happened inside the ESP-NOW receive callback, which
stalled the Wi-Fi task and made the driver drop frames it had already heard. It showed as
6.5 percent loss on controller 2 and none on controller 1, with clean sequence gaps and
almost no truncated lines, which is what makes it easy to misread as a radio or antenna
problem. It is neither: it is the USB cable behind the radio.

So the receive callback now only copies into a 64-slot ring and returns, `loop` is the
only writer to serial, and the heartbeat carries a `dropped` count that stays at zero
unless the ring overflows. 921600 was measured too and moves the loss, but the CH340 in
this cable then drops runs of bytes out of the middle of a line: six corrupted lines in
2062, against one in 3045 at 460800. 460800 leaves four times the headroom and keeps
framing intact, so it wins on both counts.

| baud | controller 1 loss | controller 2 loss | corrupted lines |
| --- | --- | --- | --- |
| 115200 | 0.00% | 6.53% | 1 / 1968 |
| 921600 | 0.00% | 0.39% | 6 / 2062 |
| 460800 | 0.00% | 0.79% | 1 / 3045 |

The sub-one-percent that remains is two controllers contending for one channel, and it
costs nothing: the protocol repeats button state every 20 ms rather than sending edges
once, so a lost packet delays a press by one frame instead of dropping it.

Both controllers measured together, receiver on USB and both on power banks: the bridge
saw ids 1 and 2 on separate endpoints, rejected no packets, and held both connected on
506 of 507 frames. Every button on both controllers then registered through the projected
prompt, 12 presses of 134 to 204 ms, one edge each and no bounce.

All three boards must sit on the same channel, fixed at 1 in the sketches rather than
inherited from an access point that does not exist. The payload is exactly the PROTOCOL.md
v1 input packet, and the controllers broadcast rather than unicast so no board needs the
receiver's MAC compiled into it.

Measured with controller 1 alone on a power bank and the receiver on USB: the radio
carried 601 packets in twelve seconds, exactly the 50 a second the controller sends, with
no loss. Button masks crossed intact, 61 packets carrying button one and 88 carrying button
two across 31 mask changes that followed the presses. Relayed into the real bridge, the
game saw the controller and read `PlayerInput.fire` on 54 frames and `PlayerInput.special`
on 75, which is button two arriving as player input with nothing of the venue network in
the path.

Two silences look alike here and are worth separating when this goes wrong. A controller
that sends nothing and a receiver that hears nothing both read as zero packets, which is
why the controllers report what their radio did; and ESP-NOW answers SEND_SUCCESS for a
broadcast once the frame leaves the antenna, with no peer acknowledging it, so that counter
proves transmission and never reception. The receiver's own count is the only evidence the
link works.

The relay gives every controller its own socket. The bridge treats a controller's endpoint
as its identity and drops packets that seem to come from somewhere new while the old
endpoint is still live, so one shared socket would make the two controllers look like
impostors of each other.

## Wireless controller from Arduino, over ordinary Wi-Fi

`firmware/arduino/controller_wifi_1` and `_2` speak PROTOCOL.md v1 over UDP, so button two
reaches the laptop from a machine that has no ESP-IDF. The ESP-IDF build in `main/` is the
real firmware and already sends both buttons; these exist only because Arduino cannot build
it. Copy `secrets.example.h` to `secrets.h` in the sketch folder, which is not committed.

They drive the laser locally from the button rather than from host commands, and always
report `command_seq` as 0. That makes a lost command unable to leave a player unable to
aim, at the cost of the host not being able to command the laser at all, so the identity
scheduler cannot drive solo windows while this build is in use. Identity comes from the
blink pattern instead, which is what it is for.

**Which radio in a crowded room.** Both Wi-Fi and Bluetooth sit in the same congested
2.4 GHz band, so choosing Bluetooth to dodge Wi-Fi congestion buys nothing. What buys
reliability is not depending on the venue's network: run the hotspot from the laptop and
point `secrets.h` at it. Venue Wi-Fi commonly isolates clients from one another, which
stops a controller reaching the laptop even while the internet works, and that has already
been seen on this project. ESP-NOW would be lower latency still but a laptop cannot speak
it without a third ESP32 acting as a receiver. If the room defeats the radio entirely, the
boards are already on USB for power and a serial link needs no radio at all.

## Identity blink bench test

`BB_LASER_IDENTITY_TEST` drives the laser continuously with this controller's identity
pattern and nothing else: no buttons, no Wi-Fi, servo disabled. The laser is lit except
for one gap of `BB_LASER_IDENTITY_GAP_MS` at the start of each period, and the period is
600, 800 or 1000 ms for controller 1, 2 or 3. Those are in the ratio 3:4:5 so that no
period is a harmonic of another.

The camera identifies a controller from how often the gap comes round, never from how
much of the time the laser is lit. A frame where tracking simply missed the dot looks
exactly like a gap, so any measure of duty is confounded by dropouts, while random
dropouts leave the period where it is and only lower the confidence in it.

Measured on the bench with a flashed controller held on the board for 16 seconds, at
30 fps: the dark runs came back at a median of exactly 133 ms, the programmed gap, and
autocorrelation recovered the period as exactly 600 ms. Correlation at controller 1's own
period was +0.70 against -0.21 and -0.18 at the other two candidates, so the right answer
is strongly positive while the wrong ones are negative rather than merely smaller.

Identifying the controller from a sliding window of that capture: 92 percent right from
one second, 97 from one and a half, 100 percent from three seconds with the winning
period beating the runner-up by 0.68. That is better than the simulation predicted.

The dot was seen on 72 percent of frames, against a programmed duty of 78, so a few
frames were lost beyond the gaps themselves and the lit runs came back at 333 ms rather
than 467. Identification was unaffected, which is the whole reason for keying on period
rather than duty: dropouts lower the correlation peak without moving it.

Controller 3, the case the fixed gap had made weakest, was then flashed and measured on
the board: the dot was found on 79 percent of frames against a ceiling of 78, so on every
frame it should have been lit; dark runs came back at a median of 200 ms against the
programmed 220; correlation at its own period was +0.91 against -0.23 and -0.20 at the
other two; and identification was correct on 100 percent of windows two seconds long.
Position held to 2.8 px median error with 3.0 px of jitter, better than controller 1.

Beware of measuring a window in which nobody was holding the button. Several runs while
setting this up read as a weak or failing laser, and the laser was simply not lit; a
measurement of laser strength is only meaningful alongside evidence the laser was on.

Controller 1 never blinks. Blinking costs detection, since a dot that is dark cannot be
tracked and a frame that missed it looks exactly like a gap, so the one controller that
needs no gap in order to be recognised is better off without one: it is the dot with no
periodic gaps, and it keeps all of its light for tracking. That also leaves only two blink
patterns to tell apart rather than three, and 800 against 1000 was already the easier pair.

With two players the question is only whether the gaps come round at all: steady is
controller 1, gaps every 800 ms is controller 2. Duty matters as well as periodicity,
because a controller whose gaps are being missed looks steady too, and only its lower
share of lit frames tells it apart from a laser that never blinks.

For controllers 2 and 3 the gap is 22 percent of the period, so 176 and 220 ms, and
`BB_LASER_IDENTITY_GAP_MS` overrides it only if set above zero. A single fixed gap gave the
longest period the smallest share and therefore the least signal to correlate: simulated at
the dropout rate measured on the bench, a fixed 133 ms identified controller 3 correctly on
88 percent of three second windows where controller 1 managed 100. Holding the share
constant brings all three to 99 or 100 with equal margins, at no extra cost to controller 1,
whose gap is unchanged.

Three sketches, one per controller, live under `firmware/arduino/`. They differ in the
controller number and nothing else, which `checks.check_blink` enforces alongside comparing
all three against the compiled firmware functions.

The original 133 ms was four frames at 30 fps. Simulating the measured tracking reliability,
four frames identified the right controller from a three second window on 99 percent of
trials while the dot was held steady, against 90 percent for a three frame gap; at the 44
percent detection measured while sweeping the dot quickly, neither is dependable at any
window length, so identity should be taken while a player is reasonably still and then
carried by ordinary motion tracking.

## Checks

## Bringing up a replacement controller board

Flash with `compile --upload`, never `upload` alone:

    arduino-cli compile --upload -p /dev/cu.usbserial-0001 --fqbn esp32:esp32:esp32 \
        firmware/arduino/controller_espnow_2

`arduino-cli upload` on its own re-flashes whatever is in the build cache and silently
ignores edits to the sketch. That cost a debugging cycle here: a baud change was made,
uploaded, and the board came back still running the old value, which reads as corruption
rather than as a stale binary.

Check the banner before trusting the board. It names the controller, the channel it
actually joined, and the button pins:

    controller 2 over ESP-NOW on channel 1 (asked for 1); fire GPIO14, special GPIO27

Then check the laser before wiring anything else to it, because a laser that is wired to
the wrong pad looks exactly like a dim laser from the camera's side. With the board on USB,
`l` forces the laser solid, `o` forces it off, `b` returns it to the FIRE button, `?`
reports state, and `p<n>` moves the laser to GPIO<n>. Force it solid and confirm a bright
red dot. If it stays dark, the wire is not on GPIO25.

`p<n>` refuses GPIO 0, 2, 12 and 15 and the flash pins 6 to 11. This is not a formality.
Driving GPIO12, which is MTDI and selects flash voltage at reset, left one board booting to
`invalid header: 0xffffffff` about thirty times a second. It could not be recovered: with
BOOT held it still reported boot mode `0x1f`, meaning GPIO0 never went low, and the one
download-mode window that was reached answered "failed to communicate with the flash chip".
Eighteen further attempts never reached download mode again. Treat the strapping pins as
off limits rather than as something to be careful around.

Boot mode `0x1f` on its own is not a fault. It prints as `SPI_FAST_FLASH_BOOT` exactly as
the healthy `0x13` does; the difference is only floating pins once the wiring is off. The
fault to look for is `invalid header: 0xffffffff`, which says the flash did not read back.
