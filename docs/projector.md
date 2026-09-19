# HY300PRO HDMI setup

The user-identified projector is an HY300 PRO, shown as **HY300PRO** by its built-in Miracast receiver. Its exact Android/firmware build has not been established. Its current LAN address is `10.31.171.250`; change `[projector].ip` in ignored `config.local.toml` when it moves.

## HDMI quick start

HDMI is now the primary output. The cable has not arrived yet, so this path has been prepared in software but not physically verified. No casting app, projector IP, Wi-Fi Direct pairing, or screen-sharing permission is required for HDMI. Keep MIT Wi-Fi connected for internet and controller communication.

1. Connect a laptop HDMI output (or a compatible USB-C display adapter) to the projector's HDMI input. Select HDMI in the projector's input menu.
2. In the laptop display settings, enable the external display in **Extend** mode. Choose a resolution offered by the projector; start with 1280×720 if available. Keep laptop output active for diagnostics. Mirroring also works, but may expose only one logical display.
3. Identify the output and show the diagnostic pattern:

   ```sh
   uv run python -m beaver_battle --list-displays
   uv run python -m beaver_battle --display 1 --fullscreen --display-test
   ```

   Replace `1` with the projector's listed index. Confirm red/green/blue bars, a moving yellow dot, and all four white border edges on the **projector**. Press Esc to exit. An unavailable index produces an error instead of silently choosing the laptop. Display indices can change when reconnecting outputs. The compositor may control window placement; if it opens on the wrong monitor, move it through desktop window controls or make the projector primary, then rerun the test.
4. Run a camera-free game smoke test on the same output:

   ```sh
   uv run python -m beaver_battle --display 1 --fullscreen --simulate
   ```

5. Fix the camera and projector in position, then calibrate the physical board:

   ```sh
   uv run python -m beaver_battle --display 1 --fullscreen --calibrate
   ```

   All four calibration markers must be visible to the camera. Repeat calibration after changing the output, desktop resolution, scaling, camera/projector position, or keystone—even if the logical canvas stays 1280×720. Do not reuse a calibration from a different projection geometry.

To save a verified output choice, add to ignored `config.local.toml`:

```toml
[display]
index = 1
fullscreen = true
```

The game and calibration share the same scaled fullscreen canvas and output selection. The display test and display listing open no camera or controller sockets. Restart the game after plugging/unplugging HDMI; automatic hotplug relocation is not implemented. Stop a live match before disconnecting the cable.

## Previous wireless diagnosis

Wireless casting remains unverified and its sender has been stopped. Installing host `dnsmasq` fixed DHCP: HY300PRO obtained an address and requested playback, while MIT stayed connected. The hardware-encoded sender reported streaming at 1920×1080/30, but the user saw a white screen and did not see the RGB test. A software `x264enc` attempt timed out during Wi-Fi Direct pairing before video began. The following notes are retained for optional future troubleshooting.

## Wireless display while retaining internet

Wi-Fi Direct creates a peer-to-peer link between laptop and receiver. Miracast uses this link; it does not require manually joining the projector's hotspot. Keep the normal MIT connection active. This laptop's MediaTek mt7921e driver advertises concurrent station and P2P interfaces, so both links can coexist; successful discovery alone does not prove a stable display stream.

GNOME Network Displays 0.99.0 is installed as a user Flatpak. The host also needs `dnsmasq` so NetworkManager can assign the receiver an address on the Wi-Fi Direct link; it is now installed. NetworkManager starts its own instance, so a separate system-wide DHCP service is unnecessary. Launch with:

```sh
flatpak run org.gnome.NetworkDisplays
```

Open the projector's built-in Miracast/WiFiDisplay app, select HY300PRO in Network Displays, and choose the laptop screen in the desktop sharing portal. Accept the projector's invitation using its remote/device controls if prompted. The laptop's screen-sharing permission has already been granted during this session. Mirroring shares that selected screen, so run the game fullscreen once connected.

If the GTK sink row cannot be activated in a native Wayland window, the current diagnostic alternative is an XWayland launch:

```sh
flatpak run --socket=x11 --nosocket=wayland --env=GDK_BACKEND=x11 org.gnome.NetworkDisplays
```

Do not switch the laptop away from MIT merely to make discovery work. If this receiver and Linux sender cannot establish a usable stream, HDMI is the practical display fallback when a cable is available. Camera/game/controller development does not depend on resolving wireless casting first.

## Verification

Check the physical projected picture through the Arducam, not just the sender window. Confirm MIT internet still works, then assess latency by moving a bright target and observing the board. Recalibrate after changing display geometry, resolution, scaling, or projector keystone. Miracast compression and delay can affect steering; actual latency is still unmeasured.
