# HY300PRO projection setup

The user-identified projector is an HY300 PRO, shown as **HY300PRO** by its built-in Miracast receiver. Its exact Android/firmware build has not been established. Its current LAN address is `10.31.171.250`; change `[projector].ip` in ignored `config.local.toml` when it moves.

The camera showed the bundled AirScreen app blocked by an update prompt. Use the built-in Miracast/WiFiDisplay app. On September 19, the user accepted the receiver invitation and Wi-Fi Direct connected, but streaming initially stalled because the laptop lacked `dnsmasq` for DHCP. After installing it and reconnecting, the receiver obtained a DHCP lease, completed RTSP negotiation, and sent PLAY; GNOME Network Displays entered **STREAMING**, selecting 1920×1080 at 30 fps. MIT remained connected concurrently. This confirms sender-side streaming only: the user reported a white projected screen, and an animated RGB test appeared on the laptop but not on the projector. Working projection is therefore **not established**. The original sender used `vah264enc`; a software `x264enc` retry is prepared, but its receiver connection has not yet completed. Stability, latency, and calibration remain unverified. Bluetooth does not provide the desktop video path. No USB display mode has been verified for this unit.

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
