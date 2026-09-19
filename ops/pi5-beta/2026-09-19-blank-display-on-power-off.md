# 2026-09-19 — pi5-beta: blank the panel as part of a power-down

**Device:** `pi5-beta` · **Follows:** `2026-09-19-poe-wifi-single-network-mode.md` ·
**Status:** APPLIED + mechanism verified; end-to-end power-down not yet observed

## Symptom (operator-reported)

Powering off through the Volumio UI (web or touchscreen) **never turns the screen
off** — the panel keeps showing a frozen frame — and restoring power too soon
afterwards can make the board fail to come up, unless you wait a substantial time.

## Cause

Volumio's UI power-off is a **software stop, not a power cut**
(`app/platformSpecific.js:17,24`):

```js
exec('/usr/bin/sudo systemctl poweroff', ...)        // fallback: /sbin/<...> -h now
```

The OS goes down, but the **5 V input is never removed**, so the panel stays
energised and holds its last frame. The same fact explains the second half of the
symptom: a board that is still energised and only partially reset is exactly what
makes an immediate re-power unreliable.

## Fix

Darken the panel as part of the kiosk's stop, so a powered-down machine looks off.

| Piece | Path | Notes |
|---|---|---|
| Script | `/home/volumio/blank-display.sh` | committed here byte-for-byte (`scripts/blank-display.sh`) |
| Drop-in | `/etc/systemd/system/volumio-kiosk.service.d/20-blank-on-stop.conf` | `ExecStop=/home/volumio/blank-display.sh` |

The vendor unit is untouched (drop-in only).

### Design decisions worth keeping

- **The X display number is resolved at runtime** from the kiosk browser's own
  environment. It has been observed on **both `:0` and `:1` on this device in one
  afternoon**, so any hard-coded value would eventually blank nothing.
- **Guarded against false positives:** it only blanks when `systemctl
  is-system-running` reports `stopping` / `maintenance` / `offline`, so an ordinary
  kiosk restart (used for page reloads) does not leave the panel dark.
  `BLANK_FORCE=1` overrides the guard for testing.
- **No privileges required:** `xset` talks to the kiosk's own X session, so the hook
  works as the `volumio` user. This matters — the sudo allowlist names `vcgencmd` only
  at the legacy `/opt/vc/bin/` path, which does not exist on this image, so a
  sudo-based firmware call would be an unreliable dependency.
- **Fallback:** `vcgencmd display_power 0`, best-effort, for the case where X is
  already gone by the time the stop runs.
- **Evidence:** every action appends to `/data/display-blank.log`, so a failed blank is
  diagnosable after the fact.

## Verification

| Check | Result |
|---|---|
| Forced run (`BLANK_FORCE=1`) | `xset` reported `Monitor is On` → **`Monitor is Off`** |
| Does it hold while the kiosk repaints? | Yes — still `Off` at +5 s, +15 s, +30 s with Chromium continuously redrawing the clock |
| Is the app harmed? | No — the plugin app kept serving `200`; only the panel was off |
| Physical result | **Operator-confirmed: "black now, no backlight"** — i.e. DPMS controls this panel's backlight, not just the video signal |
| Restore | `/home/volumio/blank-display.sh --unblank` → `Monitor is On`, app `200`, screen back |
| Real power-down (ExecStop firing during an actual UI power-off) | **NOT yet observed** — same code path, but the integrated behaviour should be confirmed the next time the operator powers down |

## Rollback

```sh
sudo rm /etc/systemd/system/volumio-kiosk.service.d/20-blank-on-stop.conf
sudo systemctl daemon-reload
```

The script can remain in place harmlessly (it does nothing unless invoked).

## Scope note

This makes a power-down **look** off. It does not change the electrical reality: the
5 V input stays live, so **breaking the supply is still the only true "off"**, and the
wait-before-restoring rule still applies.
