# HY300PRO projection setup

The user-identified projector is an HY300 PRO, shown as **HY300PRO** by its built-in Miracast receiver. Its exact Android/firmware build has not been established. Its current LAN address is `10.31.171.250`; change `[projector].ip` in ignored `config.local.toml` when it moves.

The camera showed the bundled AirScreen app blocked by an update prompt. The built-in Miracast/WiFiDisplay app is discovered by GNOME Network Displays. On September 19, the sender established P2P and reached `WAIT_SOCKET`; the camera then showed an **Invitation to connect** dialog on the projector. Without receiver-side acceptance, the attempt timed out after about 45 seconds (`supplicant-timeout`). The sender was returned to its available-receivers screen. Acceptance remains outstanding because the user is away and no remote input interface is available. Video streaming and physical calibration are not yet verified. MIT remained connected, with an HTTPS request returning 200 during pairing. Bluetooth does not provide the desktop video path. No USB display mode has been verified for this unit.

## Wireless display while retaining internet

Wi-Fi Direct creates a peer-to-peer link between laptop and receiver. Miracast uses this link; it does not require manually joining the projector's hotspot. Keep the normal MIT connection active. This laptop's MediaTek mt7921e driver advertises concurrent station and P2P interfaces, so both links can coexist; successful discovery alone does not prove a stable display stream.

GNOME Network Displays 0.99.0 is installed as a user Flatpak:

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
