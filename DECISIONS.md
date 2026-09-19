# Accepted decisions

- Up to three players; ESP32 DevKit V1, classic ESP32/WROOM, 30-pin USB-C boards. All lasers are red. Two buttons per controller.
- Canoes always move forward and steer toward their laser. Near the aim point, or when tracking is ambiguous, retain heading. Ejected beavers are slower and hold fire for thrust.
- Button 1 fires/thrusts; button 2 activates a power-up. First hit ejects the beaver, second eliminates it. Last survivor wins a round; first to five wins the match. Brief invulnerability prevents one attack consuming both states.
- All requested destructibles, asteroids, barrels, turrets, death beams, laser weapons, jousters, and proximity mines stay in scope. Physical wall contact causes no damage.
- Three primary shots; the third starts a configurable complete-magazine recharge. No new weapon activation during recharge. Shot rate and timings are configurable.
- Drawings and physical objects update continuously. Reserve dark markings for physical ink/flat props and use bright projected art. Hands/shadows can be sensed as temporary barriers; arbitrary raised objects cannot be reconstructed in 3D.
- Physical ink remains solid until erased. Projected destructible objects are separate so damage survives camera updates.
- Continuous red-dot tracking plus periodic one-controller-at-a-time identification windows. Do not infer player identity from unsynchronized high-frequency PWM. Ambiguous aim retains last heading until confidently reacquired.
- Arducam-1080P-HDR is a USB UVC camera at /dev/video0; use configurable MJPEG 1280x720 at 30 fps. It also advertises 1080p/30 and manual controls. Advertised rates still need live validation.
- HY300PRO at configurable 10.31.171.250; built-in Miracast currently says waiting for connection. Use wireless first and HDMI when available. Preserve laptop MIT internet connectivity. AirScreen's bundled version is blocked by an update prompt.
- Hit feedback is a servo-actuated water mechanism inside each controller. Implement configurable press/return commands and duplicate protection now. User is handling power, cooling, and mechanical construction later; do not treat those as blockers to software delivery.
- One-time setup may use the laptop. Lobby, ready, pause/resume, calibration, and match restart must be possible from controllers.
- Root plus three workers implement in parallel. Bench assembly remains human work. Push runnable milestones to the user-configured origin.

## Shared Python contracts

Coordinates are logical projector pixels, origin at top-left, x right and y down. Walls are a bool NumPy array of shape (height, width), where True is solid. Freshness uses laptop monotonic seconds.

- `PlayerInput` and `FeedbackEvent` are defined in `beaver_battle/model.py`.
- `Game(config)` receives the entire configuration dictionary. `new_match(player_ids, walls=None)` resets a match. `update(dt, inputs, walls=None)` returns a list of FeedbackEvent. `draw(surface)` renders bright placeholder art and HUD. Public `phase` is playing, round_over, or match_over; public `scores` maps IDs to scores. Root handles lobby, calibration, and pause before calling update.
- `Vision(config)` receives the entire configuration. `start()` starts its worker; `stop()` releases the camera. `snapshot()` returns the latest VisionSnapshot. `set_identity(player_id, ready_since)` sets the sole illuminated controller or None; ready_since is the host time after acknowledgements and settling. `begin_calibration()` requests ArUco calibration from the projected marker screen. `draw_calibration(surface)` draws that marker screen. Camera failures are reported in the snapshot rather than terminating the app.
- VisionSnapshot contains timestamp, aims, confidence, walls, preview, calibrated, and error. Treat published arrays as immutable. No queued backlog of frames.
- Game owns its collision and geometry helpers. Vision owns calibration and detection helpers. Both add standalone assert-based checks runnable without hardware.
