# Controller protocol v1

UDP JSON on laptop port 4210. Configure the laptop IPv4 address, Wi-Fi SSID, password, and controller ID in firmware build settings; do not commit credentials. MIT may assign different subnets, so use explicit laptop IP instead of assuming broadcast discovery. Controllers bind an ephemeral UDP port; laptop replies to the source endpoint. IDs are 1 and 2.

Controller sends immediately after a debounced button change and every 20 ms:

```json
{"v":1,"type":"input","id":1,"boot":123,"seq":1,"buttons":0,"command_seq":0,"laser":false}
```

`boot` is a random unsigned 32-bit boot ID. `seq` increases per packet. Button mask bit 0 is fire, bit 1 is special. `command_seq` acknowledges the latest applied laptop command; `laser` is actual gate state. Reject malformed IDs, versions and button masks. A new boot ID resets sequence tracking; duplicate/older sequences within a boot do not create input edges. Ignore packets from a competing live endpoint claiming the same ID.

Laptop sends desired output state at least every 100 ms:

```json
{"v":1,"type":"command","session":456,"seq":1,"laser":true,"feedback_id":0,"duration_ms":200}
```

The laptop uses a random 32-bit session ID per run. `seq` increases when desired output state changes; repeated commands renew a 500 ms lease. A newer nonzero `feedback_id` requests one servo press-and-return cycle; duplicates never repeat it. A new session resets event state. Do not replay feedback while disconnected or queue multiple presses. Release buttons on a 500 ms input timeout.

Controller starts laser off and servo at configured rest. After 500 ms without a valid command: laser off, servo commanded rest, queued effects discarded. Servo press time is clamped to 500 ms, followed by rest; enforce a 2 s cooldown. Disable servo actuation by firmware setting until rest/press positions and mechanism are calibrated. Losing PWM is not proof of mechanical release.

Laser identity is controlled by explicit output state, with acknowledgements and camera settling. LEDC may drive laser brightness, but its carrier is not the camera identity code. Servo and laser must use separate LEDC timers because their frequencies differ.

## Ownership

Integrator owns this protocol. Firmware and Python bridge must use these exact keys and semantics. Extend by optional fields with defaults; coordinate incompatible changes and bump v.

## ESP-NOW telemetry identification

The Arduino ESP-NOW controllers broadcast inputs through a USB receiver and the serial
relay; this transport has no laptop-to-controller command path. `command_seq` remains
zero and must not be treated as an acknowledgement. With `camera.identity_mode =
"telemetry"`, vision uses fresh reported `laser` gate states, matched to camera time,
to identify a sole illuminated controller after `identity_settle`. Controller 1 stays
steady while FIRE is held; controller 2 has a 176 ms dark gap every 800 ms. With both
lit, a separately detected second dot can acquire the remaining identity only when the
first dot is already confidently tracked. Missing, overlapping, stale, or ambiguous
observations do not establish identity. This transport does not deliver servo feedback.
The original bidirectional UDP firmware uses `identity_mode = "acknowledged"`.
