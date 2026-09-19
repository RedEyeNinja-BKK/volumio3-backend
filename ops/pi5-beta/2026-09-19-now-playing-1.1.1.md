# 2026-09-19 — pi5-beta: Now Playing 1.1.1, display self-heal, journald revert

**Device:** `pi5-beta` (Raspberry Pi 5, Volumio 4.204, bookworm)
**Scope:** user-interface plugin upgrade + display-stack resilience
**Status:** changes 1 and 2 applied; change 3 attempted and reverted

## Change summary

| # | Change | State |
|---|---|---|
| 1 | Now Playing plugin `1.0.6` → `1.1.1` | **APPLIED + VERIFIED** |
| 2 | `volumio-kiosk.service` drop-in: `Restart=always`, `RestartSec=10` | **APPLIED + VERIFIED** |
| 3 | journald persistent-journal drop-in | **ATTEMPTED → REVERTED** |

---

## 1. Now Playing `1.0.6` → `1.1.1`

### Why the UI installer was not used

Installing `1.1.1` from the plugin store fails with *"Plugin now_playing already
exists"*: `checkPluginDoesntExist` rejects when the `<plugin_type>.<name>` key is
already in Volumio's plugin config (`app/pluginmanager.js:1600-1612`) — a registry
check, not a filesystem check. So the upgrade was done as an explicit tree swap,
which is also how a version downgrade would have to work.

The old tree is moved **out** of `/data/plugins/user_interface/` rather than left
beside the new one, because `pluginFolderCleanup` (`app/pluginmanager.js:1384`)
walks every subdirectory there as a plugin folder and removes unconfigured ones when
it runs with `cleanup === true` (the uninstall path).

### Procedure

1. Backup the live tree and the live config (see *Rollback*).
2. Move the old tree **out of the scan path** (`/data/.../user_interface/`), so it
   cannot be seen as a second plugin.
3. Place the official package into `/data/plugins/user_interface/now_playing`.
   Package source: the plugin-store archive for
   `now_playing/1.1.1/bookworm/armhf`. It ships pre-built `dist/` plus
   `node_modules/`; it does not bundle `geo-tz`.
4. Run the package's own `install.sh` as root from inside the plugin directory —
   the same way the backend runs it. It installs `geo-tz` as a runtime dependency,
   drops `geo-tz` from the dev dependencies, and ensures the user-data directories
   under `/data/INTERNAL/NowPlayingPlugin/` exist. It ends with
   `plugininstallend`.
5. Let the backend re-load the plugin (a restart of the backend or a boot).

### Verified

| Observation | Result |
|---|---|
| `pluginInfo.version` served by the plugin app | `1.1.1` |
| Plugin app port | `4004`, HTTP 200 on `/` |
| Journal on load | `[now-playing] ConfigUpdater: config is up to date`, `App is listening on port 4004` |
| Client bundle served | `/static/js/main.70c84897.js` — matches the `1.1.1` package build |
| Idle screen (1920×480 capture) | clock + date correct, **weather rendered via Open-Meteo** (current + 7-day forecast), Unsplash background loaded |
| Config schema change `1.0.6` → `1.1.1` | **none** — the `UIConfig.json` key sets are identical, so the user's stored config needed no migration |

Weather provider note: `1.1.x` moved from OpenWeatherMap to **Open-Meteo**, which
needs no API key (`src/lib/api/open-meteo/`). The user's stored
`weather.openWeatherMapApiKey` is simply no longer read; leaving it in place is
harmless and it was **not** removed.

Rest of the upgrade: the live config (idle-screen layout, localisation,
backgrounds, metadata service settings) was preserved untouched throughout.

### Rollback

| Artifact | Contents |
|---|---|
| `np-backups/now_playing-1.0.6-pre-upgrade-<date>.tar.gz` | the complete `1.0.6` plugin tree |
| `np-backups/now_playing-1.0.6-live/` | the `1.0.6` tree as moved aside |
| `np-backups/config-1.0.6.json` | a copy of the live config at upgrade time |

Rollback = stop the backend, move the `1.1.1` tree out of the scan path, move the
`1.0.6` tree back, restore the config copy if it was changed, start the backend.

---

## 2. Display self-heal: `volumio-kiosk.service` drop-in

### The failure we were seeing

Twice, restarting the device/backend ended in a **permanent black screen that only
a power cycle cleared**. Facts established:

- The kiosk renders the Now Playing app on port `4004`; the launcher's readiness
  gate is port `3000` (`/opt/volumiokiosk.sh`).
- The shipped unit has **no `Restart=` policy**, and the unit is not enabled via a
  `.wants` symlink. If `startx`/X/Chromium exits, the unit stops and stays stopped.
- Stale Chromium `Singleton*` files are cleaned **only at launcher start**, so a
  crash that leaves them behind will block the next Chromium launch.
- journald was **volatile** with a 30 MB cap, on a board that has been reporting
  undervoltage events — so the evidence from both incidents was gone by the time we
  looked.

### What we changed

`/etc/systemd/system/volumio-kiosk.service.d/10-restart.conf`:

```ini
[Service]
Restart=always
RestartSec=10
```

Effects: if the X session or Chromium dies, the unit restarts after 10 s, which
re-runs the launcher and therefore also clears the stale `Singleton*` files. This
converts "black screen until someone power-cycles the board" into a ~10 s gap.

Deliberately a **drop-in**, not an edit of `/lib/systemd/system/volumio-kiosk.service`
(a vendor file that a system update would overwrite).

### Verified

Two tests, both run on the live device:

| Test | Result |
|---|---|
| Manual unit restart (`systemctl restart volumio-kiosk`) | unit active again in 1 s, new X/Chromium within 2 s, display painted within 21 s |
| **Simulated crash** — `SIGKILL` on `Xorg`, i.e. killing the unit's own process tree | systemd restarted the unit **by itself**: `NRestarts` went `0 → 1`, new `Xorg` after exactly 10 s (matching `RestartSec`), rendered screen after 30 s |

The crash test is the one that matters, and `NRestarts` incrementing is what proves it:
the restart was **automatic**, not a manual recovery. Afterwards the Now Playing app
was still serving (`200`) and the idle screen rendered normally — so an X/Chromium
death now costs ~30 s of black screen instead of requiring a power cycle.

### Not fixed by this change

The underlying **power problem**, which is now measured rather than assumed:
**292 undervoltage events in ~12 h since boot** — the first 24 s after boot, then
between 11 and 55 every hour, all day. Each event is followed by
`Voltage normalised` within seconds, so these are transient 5 V dips rather than a
sustained brownout. `vcgencmd get_throttled` reports sticky `0x50000`
(*under-voltage has occurred* + *throttling has occurred*).

The board runs an **NVMe SSD** (238 GB, PCIe) plus the strip display; `lsusb` shows
no peripherals, only root hubs. A 5 V rail dipping this often under load remains the
leading hypothesis for the kiosk dying in the first place, and for the earlier
reboots. This is a hardware/supply question, not a software one.

---

## 3. journald persistent logging — attempted, reverted

**Attempted:** a drop-in setting `Storage=persistent` with `SystemMaxUse=200M`, so
that the next display failure would leave evidence instead of vanishing.

**Reverted, because it does not work on this image:** `/var/log` is a **20 MB
tmpfs** (17 MB already used). journald returned
`Failed to open system journal: No space left on device` and fell back to
`/run/log/journal` anyway — while the drop-in would have risked filling a 20 MB
tmpfs that other services also write to. The drop-in and the created directory were
removed and `systemd-journald` restarted; the vendor default
(`Storage=volatile`, `RuntimeMaxUse=30M`) is in force again.

**Option if we want crash-forensics later:** place the journal on the large data
partition instead (e.g. mount/symlink a journal directory there) rather than on
`/var/log`. Not applied — needs its own decision, since it moves log data onto the
music volume's filesystem.

---

## Open items

1. **Power/undervoltage** — recurring events on a Pi 5 with a strip display; the
   most likely root cause of the two black-screen incidents. Hardware action.
2. **Exercise the kiosk self-heal** — one deliberate `systemctl restart
   volumio-kiosk` on a quiet moment, then re-capture the screen, to convert §2 from
   "applied" to "verified".
3. **Kiosk boot-start path is unexplained** — the unit runs at boot despite having
   no `.wants` symlink and being `disabled`, and the only in-repo start path is the
   HDMI-settings save. Worth understanding before relying on it.
4. **Crash forensics** — no persistent journal; see §3.
5. Pre-existing, unrelated: MPD errors logged at boot, and a Spotify token refresh
   failure. Not investigated, not caused by these changes.
