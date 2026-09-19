# systemd drop-ins for pi5-beta

Live artifacts, committed byte-for-byte as they exist on the device.

## `volumio-kiosk.service.d/10-restart.conf`

Gives the display kiosk a restart policy. Without it, the unit — which ships with
no `Restart=` and no boot enablement — stays dead after any X/Chromium exit, i.e.
the screen stays black until someone power-cycles the board.

A **drop-in** is used on purpose: the vendor unit at
`/lib/systemd/system/volumio-kiosk.service` would be overwritten by a system update.

### Install

```sh
sudo install -d /etc/systemd/system/volumio-kiosk.service.d
sudo install -m 0644 10-restart.conf \
     /etc/systemd/system/volumio-kiosk.service.d/10-restart.conf
sudo systemctl daemon-reload
systemctl show volumio-kiosk.service -p Restart -p RestartUSec   # expect always / 10s
```

No restart of the kiosk is required to pick this up — it applies the next time the
unit starts.

### Uninstall

```sh
sudo rm /etc/systemd/system/volumio-kiosk.service.d/10-restart.conf
sudo systemctl daemon-reload
```

### Notes

- `Restart=always` restarts the X session after Chromium or Xorg exits. The
  launcher also clears stale Chromium `Singleton*` files on each start, which is
  the other half of the black-screen problem.
- `RestartSec=10` keeps restarts from spinning if the display is in a bad state.
- This does **not** address brownouts/undervoltage, which remain the leading
  hypothesis for the kiosk dying in the first place.
