# pi5-beta — device profile and invariants

## Hardware / software

| Item | Value |
|---|---|
| Board | Raspberry Pi 5 Model B Rev 1.1 (aarch64) |
| OS | Debian bookworm, kernel 6.12.75-v8+ |
| Volumio | system version 4.204 (built 2026-09-14) |
| Node | v20.5.1 |
| Volume arch id | `VOLUMIO_ARCH="arm"` (from `/etc/os-release`) |
| Display | 1920×480 strip, driven by a Chromium kiosk |
| Backend checkout on device | `/volumio` (git) |

## Layout that matters

| Path | Meaning |
|---|---|
| `/data/plugins/<category>/<name>/` | installed plugin tree (the scan path) |
| `/data/configuration/<category>/<name>/config.json` | the plugin's **live config** — not part of the package |
| `/data/INTERNAL/NowPlayingPlugin/` | Now Playing user data (backgrounds, fonts, settings backups) |
| `/opt/volumiokiosk.sh` | kiosk launcher (waits for port 3000, then loops Chromium) |
| `/lib/systemd/system/volumio-kiosk.service` | kiosk unit (`startx` → Xsession → `/opt/volumiokiosk.sh`) |

## Display stack (as observed)

```
volumio-kiosk.service
  └─ /usr/bin/startx /etc/X11/Xsession /opt/volumiokiosk.sh -- -nocursor
       ├─ Xorg :0 -nocursor
       └─ /opt/volumiokiosk.sh
            ├─ wait for TCP 127.0.0.1:3000 (Volumio backend)
            ├─ clean stale Chromium Singleton* files in /data/volumiokiosk
            ├─ openbox-session &
            └─ while true; do chromium --kiosk --user-data-dir=/data/volumiokiosk \
                 ... http://localhost:4004; done
```

The kiosk renders **port 4004**, i.e. the *Now Playing plugin's own app*, not the
Volumio UI on 3000. 3000 is only used as the readiness gate.

The unit as shipped has **no `Restart=` policy** and is not enabled via a `.wants`
symlink. The backend toggles it from `saveHDMISettings`
(`app/plugins/system_controller/system/index.js`), which runs
`systemctl <restart|stop> volumio-kiosk.service && systemctl <enable|disable> ...`.
Consequence: if X or Chromium exits for any reason, nothing brings it back — the
screen stays dead until a manual power cycle. See
`2026-09-19-now-playing-1.1.1.md` §2 for the mitigation we applied.

## Invariants (learned the hard way)

1. **Do not install a plugin over an existing directory.** The installer refuses
   with *"Plugin `<name>` already exists"* — that guard is a **config-key check**
   (`app/pluginmanager.js:1612`), not a directory check — and the plugin scan
   (`app/pluginmanager.js:1392-1399`) treats **every subdirectory** under
   `/data/plugins/<category>/` as an installed plugin. To upgrade: move the old
   tree **out** of the scan path, then place the new tree in its place.
2. **Never touch `/data/configuration/<category>/<name>/config.json`** during a
   plugin upgrade. It is the user's live configuration and is not shipped in the
   package.
3. **Run a plugin's `install.sh` the way Volumio does**: as root, from inside the
   plugin directory. Several plugins invoke `su <user>` internally, which fails
   from a non-root context.
4. **`/var/log` is a 20 MB tmpfs.** Do not point persistent journald at it
   (`No space left on device`). The runtime journal lives in `/run` (volatile,
   capped at 30 MB) — so evidence from a crash is lost on reboot unless a
   different location is chosen deliberately.
5. **Power health is a first-class suspect** for display failures: this board has
   reported repeated undervoltage events, and a brownout during a restart is the
   leading hypothesis for the kiosk dying.
