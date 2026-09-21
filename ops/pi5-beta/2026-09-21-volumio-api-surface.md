# 2026-09-21 — Volumio control surfaces on `pi5-beta` (live-verified reference)

**Device:** `pi5-beta` (Raspberry Pi 5, Volumio **4.204**, builddate 2026-09-14, Debian 12 bookworm)
**Status:** reference record. Every path, endpoint and verb below was read from the
running device or exercised against it — **not** transcribed from the developer docs,
which are incomplete and in places wrong (corrections in §6).

Purpose: one place to look before writing anything that talks to this device.

## 1. The four surfaces

| Surface | Reached how | Auth | Use for |
|---|---|---|---|
| **REST** | `localhost:3000/api/v1/…` | **none** | scripts, one-shot control, health |
| **WebSocket** (socket.io) | `:3000/socket.io/` | **none** | UI, live state push, plugin methods |
| **CLI** | `volumio <verb>` over SSH | POSIX (SSH) | operator/sysadmin, boot config |
| **Plugin `callMethod`** | WebSocket `callMethod` | none | anything not in the REST subset |

> [!IMPORTANT]
> The REST and WebSocket surfaces are **unauthenticated by design** — upstream states
> there is "no ACL or any security feature". Port 3000 is therefore treated as a
> device-local port: it is never published beyond the device's own subnet, and any
> operator or automation access goes through the device's normal shell channel.
> Nothing in this repository requires that port to be exposed.

`mpdemulation` is also installed: a subset of the MPD protocol is emulated on
**port 6600**, which is why MPD-protocol clients can drive Volumio without knowing
its own API.

## 2. REST API — 20 endpoints, read from the live router

Source of truth: `/volumio/app/plugins/user_interface/rest_api/index.js` (route table),
plus `browse.js`, `playback.js`, `system.js`.

Base: `http://<device>:3000/api/v1/`. On the device itself the backend binds `localhost:3000`.

### Browse / library
| Method | Path | Notes |
|---|---|---|
| GET | `/browse?uri=` | empty uri = root; also `limit`, `offset` (adds `count` + `filters`; may add `navigation.prev.uri`) |
| GET | `/search?query=` | ≥3 chars, else slow |
| GET | `/superSearch` | cross-service |
| GET | `/listplaylists` | |
| GET | `/collectionstats` | `{artists, albums, songs, playtime}` — live: 2023 / 1132 / 10966 / `763:41:4` |
| GET | `/getzones` | multiroom peers incl. `isSelf` |

### Playback
| Method | Path | Notes |
|---|---|---|
| GET | `/commands?cmd=…` | **the whole transport surface, see §2.1** |
| GET | `/getState` | see §2.2 |
| GET | `/getQueue` | `{queue:[…]}` |
| POST | `/addToQueue` | JSON body |
| POST | `/addPlay` | JSON body |
| POST | `/replaceAndPlay` | JSON body, **requires `Content-Type: application/json`** (handler rejects otherwise) |

### System
| Method | Path | Notes |
|---|---|---|
| GET | `/ping` | returns `pong` |
| GET | `/getSystemVersion` | live: `{"systemversion":"4.204","builddate":"Mon Sep 14 07:39:23 UTC 2026","variant":"volumio","hardware":"pi","os":"12"}` |
| GET | `/getSystemInfo` | id, host, name, type, serviceName, state, version, builddate, variant, hardware |
| GET | `/getInstalledPlugins` | live: 8 entries |
| GET | `/enableHDMIDisplayStandby` · `/disableHDMIDisplayStandby` | |
| GET | `/oauth` | redirects to plugin_url — plugin plumbing, not for us |

### Plugin plumbing
| Method | Path | Notes |
|---|---|---|
| GET/POST | `/pluginEndpoint` | namespaced plugin REST. **Not part of any routine automation path** — see §6 |
| GET/POST/DELETE | `/pushNotificationUrls` | register a URL to receive status-change POSTs |

### 2.1 `/commands` — full verb table (read from `playback.js`)

**GET only.** A POST returns `Cannot POST /api/v1/commands/`. All parameters go in the
query string.

| Verb | Parameters | Effect |
|---|---|---|
| `play` | `N` (optional, **0-based** queue index) | play; omitted → resume current position |
| `pause` / `stop` / `toggle` | — | |
| `next` / `prev` | — | |
| `clearQueue` | — | |
| `volume` | `volume=<0-100>` \| `plus` \| `minus` \| `mute` \| `unmute` \| `toggle` | `plus`/`minus` step one click |
| `seek` | `position=<seconds>` \| `plus` \| `minus` | plus/minus step 10 s |
| `repeat` | `value=true` \| `false`; omitted = toggle | |
| `random` | `value=true` \| `false`; omitted = toggle | |
| `playplaylist` | `name=<playlist>` | |
| `startAirplayPlayback` / `stopAirplayPlayback` / `airplayActive` / `airplayInactive` | — | AirPlay state signalling |
| `usbAudioAttach` / `usbAudioDetach` | — | USB DAC hotplug signalling |

An unrecognised `cmd` falls through to `{"Error":"command not recognized"}` with
HTTP 200 — useful as a **side-effect-free liveness probe** of the command router.

### 2.2 `/getState` field map

`status`, `position`, `title`, `artist`, `album`, `albumart`, `uri`, `trackType`,
`seek`, `duration`, `samplerate`, `bitdepth`, `channels`, `random`, `repeat`,
`repeatSingle`, `consume`, `volume`, `disableVolumeControl`, `mute`, `stream`,
`updatedb`, `volatile`, `service`.

`service` is the owning controller — `mpd`, `spop`, `webradio`. `volatile: true`
means volatile mode (analogue/Spotify Connect style external source). These two
fields are the ones to gate on when reasoning about who currently owns the DAC.

## 3. WebSocket surface

Transport: **socket.io** on the same `:3000`. Emitting `getState` yields a
`pushState` event; the same request/push pairing applies throughout.

The live plugin emits **~105 distinct `push*` / event names** (full list read from
`/volumio/app/plugins/user_interface/websocket/index.js`). The ones that matter for
automation:

- **State/queue push:** `pushState`, `pushQueue`, `pushMultiRoomDevices`
- **Library:** `pushBrowseLibrary`, `pushBrowseSources`, `pushListPlaylist`,
  `pushPlaylistContent`, `pushMyCollectionStats`
- **Plugin control:** `pushInstalledPlugins`, `pushAvailablePlugins`,
  `pushEnablePlugin`, `pushDisablePlugin`, `pushUnInstallPlugin`
- **UI/feedback:** `pushToastMessage`, `openModal`, `closeModals`, `pushUiConfig`
- **Audio output:** `pushOutputDevices`, `pushAudioOutputs`, `pushDSPUiConfig`
- **System:** `pushSystemInfo`, `pushSystemVersion`, `pushShutdownOrStandbyMode`,
  `pushUpdaterChannel`, `pushBackgrounds`

Inbound commands documented upstream include `play`, `pause`, `stop`, `prev`,
`next`, `seek`, `setRandom`, `setRepeat`, `getState`, `getQueue`, `volume`,
`mute`, `unmute`, `search`, `browseLibrary`, `getMultiRoomDevices`,
`removeFromQueue`, `addToQueue`, `moveQueue`, `addPlayCue`, `getBrowseFilters`,
`getBrowseSources`, the playlist verbs (`createPlaylist`, `deletePlaylist`,
`listPlaylist`, `addToPlaylist`, `removeFromPlaylist`, `playPlaylist`, `enqueue`),
the favourites verbs, the sleep/alarm verbs, and:

```json
{"endpoint":"music_service/spop","method":"someMethod","data":{}}
```

emitted as `callMethod`, which returns via `pushMethod`. **This is the universal
escape hatch**, and the reason it is treated as an administrative action. Note the
upstream constraint: neither the plugin name nor the method may contain `-`, because
the frontend translates `/` to `-` when parsing.

> [!NOTE]
> I enumerated the outbound `push*` surface from the live source, but did **not**
> machine-extract the inbound `.on()` handler list — that plugin registers its
> handlers through a dispatch pattern rather than literal `socket.on('name')`
> calls, so a grep under-reports. Do not treat §3's inbound list as exhaustive;
> treat `callMethod` as the general route.

## 4. CLI surface

`/usr/local/bin/volumio` → `/volumio/app/plugins/system_controller/volumio_command_line_client/volumio.sh`.
Dynamic subcommands live in the sibling `commands/` directory: `status.js`,
`getvolume.js`, `setvolume.js`, `playback.js`, `internet.sh`, `kernelsource.sh`,
`pull.sh`, `init-edit.sh`, `devmode.sh`.

**Live verb list on this device** (more than the docs list — `livelog`, `filetrace`
and `scanaudioinputs` are undocumented):

```
status volume seek repeat random play pause toggle next previous stop clear
vstart vstop vrestart pull dev kernelsource plugin logdump init-edit
updater internet endpointstest livelog filetrace scanaudioinputs
startairplayplayback stopairplayplayback airplayactive airplayinactive
usbattach usbdetach
```

Worth flagging:

- `livelog` and `filetrace` — the practical way to watch the backend without
  guessing at journald units.
- `scanaudioinputs` — rescan audio inputs after a hardware change.
- `plugin` sub-verbs: `init`, `refresh`, `package`, `submit`, `install`, `update`.
- `updater restorevolumio` — deletes **all** manually edited files under `/volumio`,
  i.e. it reverts the overlay. This would revert our `mpd/index.js` patch (but not
  the `/data` ones). Treat as destructive.
- `updater factory` / `updater userdata` — wipe user data.

## 5. On-disk layout and persistence model

| Path | Nature | Consequence |
|---|---|---|
| `/volumio` | overlay-backed, must never be recursively overwritten | **Our `mpd/index.js` patch lives here and survives reboot via the overlay** |
| `/data` | the recommended persistent location | `/data/plugins/…` — our `spop` and FusionDSP patches live here |
| `/data/configuration/<category>/<name>/config.json` | live plugin config, **not** part of the plugin package | editing the package does not touch config |
| `/tmp` | **RAM (tmpfs)** | lost on reboot; also the place daemons are told to write logs |
| `/boot/config.txt` | base boot config | contains the Volumio I2S stanza: `dtoverlay=hifiberry-dac` |
| `/boot/volumioconfig.txt` | Volumio's own additions | included **before** userconfig |
| `/boot/userconfig.txt` | user overrides | included **after** the other two, so it wins |

The overlay consequence matters: a file edited under `/volumio` is mirrored onto the
overlay and **supersedes** the squashfs original across OTA updates. Our `mpd`
patch therefore persists through `updater forceupdate` but is erased by
`updater restorevolumio`.

### Plugin inventory (live)

Built-in (`/volumio/app/plugins/`):
- `audio_interface`: `alsa_controller`, `outputs`, `upnp`
- `miscellanea`: `alarm-clock`, `albumart`, `appearance`, `my_music`, `novaui`, `wizard`
- `music_service`: `airplay_emulation`, `example_plugin`, `inputs`, `last_100`, `mpd`, `upnp_browser`, `webradio`
- `system_controller`: `i2s_dacs`, `network`, `networkfs`, `services`, `system`, `updater_comm`, `volumio5onboarding`, `volumio_command_line_client`, `volumiodiscovery`
- `user_interface`: `mpdemulation`, `rest_api`, `websocket`

Installed on this device (`/data/plugins/`):
- `audio_interface`: **`fusiondsp`** (owns `camilladsp`, therefore owns the DAC)
- `music_service`: **`spop`** (Spotify Connect / go-librespot)
- `system_controller`: `rpi_eeprom_config`, `rpi_eeprom_updater`
- `user_interface`: `now_playing`, `peppy_screensaver`, `Systeminfo`, `touch_display`

**Ownership rule learned the hard way:** the DAC (card 1, `snd_rpi_hifiberry_dac`) is
opened by **camilladsp**, not by MPD or go-librespot. Players write into the
FusionDSP FIFO; FusionDSP holds the hardware device. That is why the observed
hardware format is `S32_LE` rather than the source file's format.

> The plugin `callMethod` route and `/pluginEndpoint` are **not** part of any
> routine automation path: they can restart services and rewrite configuration.
> If a future task needs one, treat it as an administrative change with its own
> record, not as a convenience call.

## 6. Corrections to the public developer documentation

Recorded because they cost real time and the docs are the first place anyone looks:

| Upstream doc | Reality on 4.204 |
|---|---|
| Presents `/commands` commands without stating the method | `/commands` is **GET-only**; POST returns `Cannot POST` |
| "`volumio.local/api/v1/…`", "`localhost:3000`" | both correct, but the docs never state the port for the WebSocket transport (it is the same `:3000`) |
| REST doc lists `addToQueue` twice, dubiously | live router is authoritative — §2 |
| Architecture page describes systemd units, startup order, shairport/upnpd | **not documented at all** — the page stops at the Node backend |
| Filesystem page | correct but minimal; the `/volumio` overlay-supersedes-squashfs caveat is the important half |
| I2S DACs page: "enable your DAC … adding the appropriate dt-overlay to `/boot/userconfig.txt`" | correct, and the live config confirms `dtoverlay=hifiberry-dac` in `config.txt` with `i2s_dac: "Generic I2S DAC"`, `i2s_id: "hifiberry-dac"` in `/data/configuration/system_controller/i2s_dacs/config.json` |
| `dacs.json` described as the DAC compatibility list | confirmed: 21,681 bytes at `/volumio/app/plugins/system_controller/i2s_dacs/dacs.json`; mandatory Pi fields are `overlay` and (since v3) `alsacard` |

## 7. Evidence floor

Read from the device at 2026-09-21 ~16:26–16:45: the REST route table and all four
handler files; the WebSocket plugin's emit list; the CLI script and `commands/`
directory; both plugin trees; `/proc/asound/*`; `dacs.json` size and the active
I2S settings. Endpoint behaviours marked "live" were exercised with real calls
(reads only — no mutating call was fired, so no operator-set value was altered).
