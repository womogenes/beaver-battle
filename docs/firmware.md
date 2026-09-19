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

The serial monitor at 115200 baud reports every debounced button transition,
including when Wi-Fi is unconfigured or disconnected. For example:

```text
I (1234) beaver: FIRE GPIO27 PRESSED
I (1567) beaver: FIRE GPIO27 RELEASED
I (2345) beaver: SPECIAL GPIO32 PRESSED
I (2678) beaver: SPECIAL GPIO32 RELEASED
```

The timestamp is milliseconds since boot. Holding a button produces one press
line; releasing it produces one release line. With no laptop connection the
laser remains off, and the servo remains disabled with the default settings.

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

Servo output is **disabled by default**: GPIO26 remains low and no PWM pulses are
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
handling, disabled feedback, and sequence wrap. Physical pin timing, radio
latency, power, and mechanical return still require the bench checks above.
