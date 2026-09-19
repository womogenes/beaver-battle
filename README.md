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

1. Establish projector mirroring, keeping the laptop connected to the internet. The current HY300PRO is configured at `10.31.171.250`; see [projector setup](docs/projector.md). USB alone has not been verified as a video connection.
2. Fix the Arducam and projector in position so the camera sees the complete board. The current USB UVC camera is `/dev/video0`, configured for MJPEG 1280×720 at 30 fps.
3. Assemble the buttons and output connections using [controller wiring](docs/wiring.md). Configure and flash each controller with a unique ID, Wi-Fi credentials, and the laptop's reachable IPv4 address using [firmware instructions](docs/firmware.md). Allow inbound UDP 4210 on the laptop. Wireless client isolation can prevent controllers from connecting even when internet works.
4. Run `uv run python -m beaver_battle --fullscreen --calibrate`. Four projected ArUco markers establish the camera mapping. Calibration saves locally and returns to the lobby. Repeat whenever the camera/projector moves or keystone changes.
5. Choose a two- or three-player match; each player presses button 2 to ready. After the countdown, steer by pointing at the board. The game periodically isolates one laser to identify it; brief blinking is expected.

Camera exposure and wall/laser thresholds need tuning on the actual projected surface. Synthetic checks do not establish physical tracking accuracy. Start with dark ink on a bright board, keep hands out of view while calibrating, and avoid small red projected artwork. Physical ink remains solid until erased; virtual destructible props are separate.

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

`projector.ip` records the configurable LAN address for setup/diagnostics. Miracast discovers the receiver through Wi-Fi Direct; changing this setting does not initiate casting. `--players`, `--fullscreen`, and `--config` override launcher behavior. Keep credentials and calibration captures out of git.

## Checks and architecture

```sh
uv run python -m checks.check_game
uv run python -m checks.check_vision
uv run python -m checks.check_network
uv run python -m checks.check_app
cc -std=c11 -Wall -Wextra -Werror -pedantic firmware/check.c firmware/main/controller.c -o /tmp/beaver-firmware-check
/tmp/beaver-firmware-check
```

The networking check opens localhost UDP sockets. The camera checks generate synthetic images without opening a device. The game and menu checks run without a display. Firmware build and flashing are separate from its portable logic check.

Validated on September 19, 2026: game/vision/network/menu checks, a 180-second integrated simulation ending in a 5–1–3 match with 41 hit events, portable C logic checks, and an ESP32 firmware build with ESP-IDF 5.5.0. One physical ESP32 was flashed with hash verification and booted controller 1. The user subsequently confirmed the buttons and laser work with the standalone laser-button firmware test. A five-second physical launcher run opened the desktop window and UDP listener, received live Arducam frames, and released the camera on exit. After receiver acceptance and installing the missing host DHCP component (`dnsmasq`), the projector obtained an address and requested playback; the sender reports streaming at 1920×1080/30 while MIT internet remains available. The physical projected picture and latency remain unverified. Physical laser tracking and calibrated water feedback remain bench work. Standalone laser and servo test modes are documented in [firmware instructions](docs/firmware.md).

`app.py` owns menus and the fixed-step loop; `game.py` owns combat; `vision.py` publishes immutable latest-frame observations; `network.py` handles UDP and laser identification. [PROTOCOL.md](PROTOCOL.md) is the shared firmware contract. Another game can consume `PlayerInput` and wall masks and emit `FeedbackEvent` without replacing camera or controller code. See [vision notes](docs/vision.md), [game notes](docs/game.md), and [physical game ideas](docs/physical-ideas.md).

[PLAN.md](PLAN.md) is the original brief; [DECISIONS.md](DECISIONS.md) records subsequent decisions. Read [AGENTS.md](AGENTS.md) before contributing. Work on a topic branch, respect subsystem ownership, run relevant checks, fetch before pushing, and never force-push shared work. Controller power, cooling, and the physical water mechanism are deferred to the team's bench assembly. Servo output stays disabled until its travel is calibrated.
