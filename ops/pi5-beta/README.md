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

## Capturing the display (for verification)

The kiosk's X server is **not on `:0`**. Read the real values from the running
Chromium's environment instead of guessing:

```sh
CPID=$(pgrep -f 'chromium.*localhost:4004' | head -1)
XA=$(tr '\0' '\n' < /proc/$CPID/environ | sed -n 's/^XAUTHORITY=//p')
DISP=$(tr '\0' '\n' < /proc/$CPID/environ | sed -n 's/^DISPLAY=//p')
XAUTHORITY="$XA" DISPLAY="$DISP" scrot -o /tmp/screen.png
```

Observed live values: `DISPLAY=:1`, `XAUTHORITY=/home/<user>/.Xauthority`.
Guessing `:0` fails with `Can't open X display` — which looks like a dead display but
is only the wrong display number. Check `scrot`'s exit code too: it fails silently if
its output is being piped, and a chained `&& echo` will not run.

## Invariants (learned the hard way)

1. **Replace a plugin by swapping its tree — never install over it.** Two separate
   mechanisms are involved:
   - The store installer refuses with *"Plugin `<name>` already exists"* because
     `checkPluginDoesntExist` rejects when the `<plugin_type>.<name>` key is already
     in Volumio's plugin config (`app/pluginmanager.js:1600-1612`). That is a
     **registry** check, not a filesystem check.
   - `pluginFolderCleanup` (`app/pluginmanager.js:1384`) walks every subdirectory
     under a plugin path as a plugin folder. Configured ones are left untouched;
     unconfigured ones are **removed only when it is called with `cleanup === true`**
     (the uninstall path) — the unconditional delete is commented out in the source,
     noted there as having once deleted plugins when new ones were installed. The
     same routine does remove stray non-directory entries and empty category
     directories.

   So move the old tree **out** of `/data/plugins/<category>/` and put the new tree
   in its place: a tree left behind is walked on every cleanup pass and can be
   deleted by a later uninstall.
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
5. **Power health is a first-class suspect** for display failures. Measured
   2026-09-19: **292 undervoltage events in ~12 h** — the first 24 s after power-on,
   then 11–55 per hour, all day — each followed by `Voltage normalised` within
   seconds, i.e. transient 5 V dips rather than a sustained brownout.
   `vcgencmd get_throttled` reports sticky `0x50000`. The rail feeds an **NVMe SSD**
   (238 GB, PCIe) plus the strip display, with no USB peripherals attached. A Pi 5
   wants 5 V / 5 A (27 W) at its USB-C input.
   - **PoE as an alternative supply:** the PoE budget has to cover the same load, and
     an under-sized class (e.g. 802.3af, 15.4 W) tends to show up as subsystems
     failing to initialise rather than as a clean boot — Wi-Fi is typically the first
     casualty. Check the PoE class/budget and the splitter's 5 V regulation before
     concluding that Wi-Fi itself is at fault.
   - **Observed setup and its expected behaviour:** 802.3at (25.5 W at the port) via a
     PoE HAT, with the PoE router carrying **no data**. Two consequences:
     1. A dead-end uplink should *not* steal connectivity: eth0 is configured
        `iface eth0 inet dhcp` with `noipv4ll`, so with no DHCP server it takes no
        address and adds no route — Wi-Fi keeps the default route. Volumio's network
        plugin has no "disable wireless when wired" path either (`wireless_enabled` is
        only written by the explicit `wirelessEnable`/`wirelessDisable` methods), so
        Wi-Fi disappearing is **not** explained by the network configuration.
     2. 802.3at at the port is *less* than a healthy 5 V/5 A USB-C supply once the
        HAT's conversion losses are counted, so PoE is a cabling convenience rather
        than a power upgrade — and this board already logs undervoltage on its
        current supply.
   - **Settling it with evidence:** `scripts/poe-diag.sh` (+ the `poe-diag.service`
     one-shot on the device) writes throttled flags, core volts, the undervoltage
     timeline, the Wi-Fi driver bring-up lines, interface/route/rfkill state and the
     eth0 link state to `/data` **at boot** — before Wi-Fi fails and the box becomes
     unreachable. Arm it, power over PoE, switch back, then read the report.
