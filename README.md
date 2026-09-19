# Beaver Battle

A projection-mapped canoe battle for two or three red-laser controllers. Python runs the game, USB camera, calibration, and UDP networking. ESP-IDF C runs the two buttons, laser output, and servo feedback on each ESP32 DevKit V1.

## Run now, without hardware

Install Python 3.12 and [uv](https://docs.astral.sh/uv/), then run from the repository root:

```sh
uv sync --python 3.12
uv run python -m beaver_battle --simulate
uv run python -m beaver_battle --simulate --mouse --players 2
```

The first command after setup runs three bots through complete matches. With `--mouse`, player 1 steers toward the mouse, left click fires/thrusts, and right click activates a pickup. `R` restarts, `Esc` pauses, and Enter/Down operate menus. Use `--fullscreen` for the projected game. Placeholder polygons can be replaced with `assets/canoe.png` and `assets/beaver.png`; keep artwork bright enough for camera segmentation.

For a reproducible check without opening a window:

```sh
uv run python -m beaver_battle --headless --seconds 60 --screenshot /tmp/beaver-battle.png --report /tmp/beaver-battle.json
```

## Connect the physical game

1. Connect the laptop to the HY300PRO HDMI input and select that input on the projector. In desktop display settings, enable the projector as an extended display. Run `uv run python -m beaver_battle --list-displays`, then `uv run python -m beaver_battle --display 1 --fullscreen --display-test` (replace `1` with the projector index). See [HDMI setup](docs/projector.md).
2. Fix the Arducam and projector in position so the camera sees the complete board. Run `uv run python -m beaver_battle --list-cameras`; `camera.device` is an index, and the built-in laptop camera normally holds index 0, so the Arducam is usually a later one. Save the working index in `config.local.toml` or pass `--camera N`. `camera.backend` picks AVFoundation on macOS and V4L2 on Linux by itself. On macOS, grant Camera permission to the terminal application in System Settings before this succeeds. See [camera and calibration](docs/vision.md).
3. Assemble the buttons and output connections using [controller wiring](docs/wiring.md). Configure and flash each controller with a unique ID, Wi-Fi credentials, and the laptop's reachable IPv4 address using [firmware instructions](docs/firmware.md). Allow inbound UDP 4210 on the laptop. Wireless client isolation can prevent controllers from connecting even when internet works.
4. Run `uv run python -m beaver_battle --display 1 --fullscreen --calibrate` (use the verified projector and camera indices). Four projected ArUco markers establish the camera mapping. While it waits, the projection itself says what is wrong: which markers are blocked, or that the ones it can see are too bunched together. Nine markers are projected and only a well-spread few are needed, so ink or objects covering one or two do not stop calibration. If it still refuses, erase or move whatever covers the named spots; once calibration succeeds you may draw anywhere, including over the markers. Calibration saves locally and returns to the lobby with `Calibration saved.` Repeat whenever the camera/projector moves or keystone changes.
5. Draw on the board in black marker before or after calibrating. Calibration reads the board from the marker screen, so anything drawn is solid as soon as calibration finishes; closed outlines fill in as logs and rocks, and open strokes stay as solid sticks. Drawings continue to update during play at `camera.wall_update_hz`, and so do hands, shadows and objects held in the beam: anything that takes away light the projector sent becomes solid while it is there and clears when it is removed. Draw in **green or black**, which fill in as solid bodies. Outlines do not have to close: a gap up to `game.shape_closure` of a shape's own width is bridged, so pen lifts and dry-marker dashes still fill, while a shape left properly open stays a barrier line rather than flooding. Blue works as a barrier line but will not reliably fill a closed outline. **Red, orange and pink cannot be detected at all** — they reflect red light just as the laser dots do, so they are excluded on purpose rather than left ambiguous. See [camera and calibration](docs/vision.md) for the measurements.
6. To watch the whole projected sequence without controllers, run `uv run python -m beaver_battle --display 1 --fullscreen --calibrate --bench 3`. The bench uses the real camera, calibration, and board drawings, but drives the canoes with bots and skips controller networking, carrying the projector from markers through the countdown into a live match on its own. Menus stay on the keyboard. It is a projection and board-reading demo, not a substitute for a controller test.
7. Choose a two- or three-player match; each player presses button 2 to ready. After the countdown, steer by pointing at the board. The game periodically isolates one laser to identify it; brief blinking is expected.

Calibration markers are read through local contrast equalization and gathered over `camera.marker_memory_seconds`, so hold the camera, projector, and board still while the marker screen is up. If calibration keeps failing, the projector is almost certainly losing to room light: turn off the lights above the board and take the projector out of any eco/low-brightness mode. Camera exposure and wall/laser thresholds need tuning on the actual projected surface. Synthetic checks do not establish physical tracking accuracy. Start with dark ink on a bright board, keep hands out of view while calibrating, and avoid small red projected artwork. Physical ink remains solid until erased; virtual destructible props are separate.

## Controls

| Context | Button 1 | Button 2 | Hold both for one second |
| --- | --- | --- | --- |
| Canoe | Fire primary rock | Use collected weapon | Pause |
| Ejected beaver | Thrust | — | Pause |
| Lobby/pause | Next option | Confirm | — |
| Ready | — | Ready | Cancel to lobby |
| Calibration/countdown | — | — | Cancel to lobby |
| Match finished | — | Return to lobby | Pause |

The lowest connected controller ID operates menus. A disconnected controller or stale camera pauses physical play. Reconnect and choose Resume. Keyboard equivalents are Down/Tab, Enter/Space, Esc, and `C` for calibration. `F2` shows camera preview in the lobby/pause screen.

Canoes always move forward; aim near the canoe or uncertain tracking retains heading. Three shots empty the primary reserve and trigger a full recharge; new weapon activations are blocked during recharge. First damage ejects the beaver; second damage eliminates it. Wall collisions bounce or slide without damage. Last survivor wins the round; first to five rounds wins the match. Barrels/asteroids drop lasers, jousters, and mines; turrets and cycling death beams add hazards.

## Configuration

Defaults are in `config.toml`. Put machine-specific overrides in ignored `config.local.toml`, for example:

```toml
[projector]
ip = "10.31.171.250"

[camera]
device = 0
wall_threshold = 75

[game]
players = 3
shot_interval = 0.30
reload_seconds = 2.5
```

`--bench N` runs the real camera path with N bot canoes and no controllers. `--camera N` selects the capture index and `--list-cameras` probes the available ones without opening a window. `--display N` selects the output; `[display].index` saves the choice in local configuration. `--list-displays` and `--display-test` do not start the camera or controller networking. Fullscreen scales the logical canvas to the selected desktop. `projector.ip` records the configurable LAN address for wireless diagnostics and is unused by HDMI. Miracast discovers the receiver through Wi-Fi Direct; changing this setting does not initiate casting. `--players`, `--fullscreen`, and `--config` override launcher behavior. Keep credentials and calibration captures out of git.

## Checks and architecture

```sh
uv run python -m checks.check_game
uv run python -m checks.check_shapes
uv run python -m checks.check_vision
uv run python -m checks.check_network
uv run python -m checks.check_app
uv run python -m checks.check_display
cc -std=c11 -Wall -Wextra -Werror -pedantic firmware/check.c firmware/main/controller.c -o /tmp/beaver-firmware-check
/tmp/beaver-firmware-check
```

The networking check opens localhost UDP sockets. The camera checks generate synthetic images without opening a device. The game and menu checks run without a display. Firmware build and flashing are separate from its portable logic check.

Validated on September 19, 2026: game/vision/network/menu checks, a 180-second integrated simulation ending in a 5–1–3 match with 41 hit events, portable C logic checks, and an ESP32 firmware build with ESP-IDF 5.5.0. One physical ESP32 was flashed with hash verification and booted controller 1. The user subsequently confirmed the buttons and laser work with the standalone laser-button firmware test. A five-second physical launcher run opened the desktop window and UDP listener, received live Arducam frames, and released the camera on exit. After receiver acceptance and installing the missing host DHCP component (`dnsmasq`), the projector obtained an address and requested playback; the sender reports streaming at 1920×1080/30 while MIT internet remains available. The user reported a white projected screen even while the sender reported streaming; working projection is not yet established. The software-encoder retry timed out during pairing. The user chose HDMI; output selection and a standalone display test are ready, but the cable and physical picture remain untested. Physical laser tracking and calibrated water feedback remain bench work. Standalone laser and servo test modes are documented in [firmware instructions](docs/firmware.md).

`app.py` owns menus and the fixed-step loop; `game.py` owns combat; `vision.py` publishes immutable latest-frame observations; `network.py` handles UDP and laser identification. [PROTOCOL.md](PROTOCOL.md) is the shared firmware contract. Another game can consume `PlayerInput` and wall masks and emit `FeedbackEvent` without replacing camera or controller code. See [vision notes](docs/vision.md), [game notes](docs/game.md), and [physical game ideas](docs/physical-ideas.md).

[PLAN.md](PLAN.md) is the original brief; [DECISIONS.md](DECISIONS.md) records subsequent decisions. Read [AGENTS.md](AGENTS.md) before contributing. Work on a topic branch, respect subsystem ownership, run relevant checks, fetch before pushing, and never force-push shared work. Controller power, cooling, and the physical water mechanism are deferred to the team's bench assembly. Servo output stays disabled until its travel is calibrated.
