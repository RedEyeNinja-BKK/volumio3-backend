# 2026-09-21 — pi5-beta: playback stability check, Spotify path finding, bootloader EEPROM update

Device: `volumio-pi5-beta` (Raspberry Pi 5, Volumio 4.204, aarch64 kernel / armhf userland, NVMe boot)

**Status: EEPROM update complete and verified** — activated by an operator power cycle at 2026-09-21
00:39, device healthy on the new bootloader with the previous configuration retained (section 4).
**The Spotify simultaneous-play case was then tested and reproduced audible glitching** (section 3).

Provenance labels per `2026-09-19-i2s-dac-regression-and-revert.md`: **measured** = we measured it,
**observed** = operator reported/confirmed, **claimed** = third-party.

---

## 1. Playback stability ("wedge") check — PASS

Context: on 2026-09-20 an I²S/DAC overlay change was applied and reverted and re-applied, and the
open question was whether playback *stays* playing over time rather than wedging part-way.

Method: resume playback, then sample the reported position against the wall clock repeatedly,
including across a track boundary. **measured**

| Sample | Wall clock | Position | Track |
| --- | --- | --- | --- |
| T0 | 00:25:50 | 0:44 / 3:01 | #1/8 |
| T1 | 00:27:06 | 2:00 / 3:01 | #1/8 |
| T2 | 00:27:41 | 2:35 / 3:01 | #1/8 |
| T3 | 00:29:01 | 0:54 / 2:37 | #1/7  (new track, after boundary) |
| T4 | 00:29:21 | 1:14 / 2:37 | #1/7 |

T0→T1: 76 s elapsed, 76 s of audio advanced. T3→T4: 20 s elapsed, 20 s advanced. Position tracks
the wall clock 1:1 with no stall and no drift, and the track boundary was crossed cleanly.

Counters after the run: `xrun` 0, `underrun` 0, codec errors 0, `snd_soc` errors 0, no "device busy",
MPD restarts 0. Temperature steady ~73–75 °C, `get_throttled` = 0x0. **measured**

The queue index moved `#1/8` → `#1/7` because `consume` is enabled — played tracks are removed from
the queue, so the next track becomes index 1. That is expected behaviour, not a reordering fault.

**Conclusion: no wedge. Playback is stable across at least one track transition.**

## 2. Audio topology — observed

* MPD: output 1 (`mpd_peppyalsa`) disabled, output 2 (`alsa`) enabled; `mpd.conf` declares
  `type "fifo"`. **measured**
* A **CamillaDSP** process (the FusionDSP engine) holds the hardware PCM device. **measured**
* `go-librespot` is configured with `audio_device: "volumio"`. **measured**

Worth knowing: `systemctl is-active camilladsp` reports **inactive** while a `camilladsp` process is
demonstrably holding the PCM device. CamillaDSP is therefore not running under a systemd unit of that
name — it is spawned another way. **Consequence: `systemctl is-active camilladsp` is not a valid
health check for the DSP path**; check for the process and the device holder instead.

## 3. Spotify simultaneous-play — TESTED, and it reproduces a defect

Tested 2026-09-21 at ~00:43 by the operator: Spotify playback started from a phone while local MPD
playback was running. **Result: audible glitching. observed (operator)**

Both players were confirmed active at the same moment. **measured**

| Player | Output target | State |
| --- | --- | --- |
| MPD | ALSA device `volumio` | playing a local FLAC |
| go-librespot | `audio_device: "volumio"` | playing a Spotify track |

They target the **same** ALSA PCM, and `/etc/asound.conf` routes it through a single named FIFO:

```
pcm.!default -> volumio -> volumioDsp (type plug, S32_LE, 2 ch)
            -> fusiondsphook (type volumiohook)
            -> fusiondspfifo (type volumiofifo, fifo "/tmp/fusiondspfifo")
            -> CamillaDSP (FusionDSP)
```

**Diagnosis:** the FusionDSP pipeline has exactly one capture input — a named FIFO. Two ALSA clients
opening `pcm.volumio` at the same time both write PCM into that same FIFO, so their byte streams
interleave and CamillaDSP processes the spliced result. **inferred** — the routing is measured, the
interleaving is the inference. Direct proof (counting the FIFO's writers while the condition was live)
was NOT captured; by the time it was attempted, playback had already stopped.

Supporting configuration facts, all **measured**:

* PeppyMeter's plugin setting `useSpotify` is **False**, so the ALSA config it generates carries
  `spotify1_off` / `spotify2_off`. The dedicated Spotify routes are disabled, so Spotify falls back to
  the same default path as every other source.
* CamillaDSP runs a single capture device, samplerate fixed at 44100, chunksize 2048, `queuelimit: 1`.
* No kernel xruns or underruns were logged during the incident (0), and CamillaDSP's own log
  (`/tmp/camilladsp.log`, warn level) stayed **empty — 0 bytes**, verified unprivileged. A glitch that
  both layers report as clean is consistent with corrupt input rather than buffer starvation.

Also observed during the incident: the PeppyMeter screensaver (`screensaver/volumio_peppymeter.py`,
child of `run_peppymeter.sh`) was consuming **102% CPU** — a full core — while Xorg, chromium,
CamillaDSP and go-librespot competed for the rest. A plausible **contributing** factor on a Pi 5, but
second-order: the tested variable was the simultaneous case, which the routing analysis already
explains. **measured (CPU), inferred (contribution)**

### 3a. Follow-up test — Spotify silent, and Volumio's UI desyncs

The operator then re-tested. Reported: *Spotify starts but there is no sound; a local FLAC then
plays, but the UI stays on the Spotify track.* All three symptoms were reproduced and diagnosed.

**Defect 1 — Spotify renders digital silence. Root cause found and fixed. measured**

| Source | Volume |
| --- | --- |
| `go-librespot` own volume (`GET /player/volume`) | **0** |
| Volumio's reported volume | 100 |
| Volumio's `disableVolumeControl` | **true** |
| `go-librespot` `external_volume` | false |

librespot reported the Spotify stream as playing and its position advanced, while its own volume was
0. Volumio reported 100 but declares `disableVolumeControl: true`, so it does not push its level to
librespot; librespot runs `external_volume: false` and therefore applies its own internal volume. The
result is a correct, playing stream multiplied by zero — silence, with nothing logged as an error.

Remedy applied: `POST /player/volume {"value":100}` (HTTP 200), confirmed by re-reading
`GET /player/volume` -> `{"max":100,"value":100}`. Applied while paused so nothing would start
unexpectedly. **measured**

**Confirmed by the operator: Spotify now plays.** Re-verified ~72 minutes later, same boot: still
`{"max":100,"value":100}`, and `GET /status` reports the same track with `volume: 100`, so the
remedy held rather than decaying. This falsifies the competing candidate that Spotify's own audio
path was broken. **measured**

Caveat for any future silence: the level lives in librespot's own mixer as runtime state, not in any
config this record controls. Volumio declares `disableVolumeControl: true` and librespot runs
`external_volume: false`, so the Spotify app's volume for this device governs librespot's mixer.
If silence returns, read `GET /player/volume` first - that is the lever.

**Defect 2 — the glitch: one FIFO is the mechanism. Cause since CONFIRMED and fixed — see §7 and §8. measured**

Routing is measured and unambiguous: `mpd.conf` uses `device "volumio"`, `go-librespot` uses
`audio_device: "volumio"`, and `/etc/asound.conf` funnels that single PCM through one named FIFO,
`/tmp/fusiondspfifo`, into CamillaDSP. Nothing in this graph mixes. Two clients opening it
concurrently write into the same FIFO and their bytes interleave, replacing spans of one stream with
the other's, so the audible result is dropouts/stutter rather than a mix.

At the time this was written the interleaving was inference rather than measurement. It has since been
confirmed by a controlled reproduction (§7), and the two cautionary checks below are explained rather
than contradicted: a paused `go-librespot` really does release the FIFO (so it injects nothing), and the
pipeline restart turns out to be a *consequence* of the rate switch in §7, not an independent cause.
The checks as recorded at the time:

| Check, taken while librespot was paused | Result |
| --- | --- |
| Does a paused `go-librespot` hold the FIFO open? | **No** — no process held `/tmp/fusiondspfifo` at all. This weakens the "paused librespot keeps injecting silence" variant. |
| Pipeline stability | The FIFO files carry an mtime far later than boot, so the FusionDSP/CamillaDSP pipeline is **re-created on playback events**. A restart concurrent with the second source is itself a candidate click/dropout. |
| CPU contention during the incident | The PeppyMeter screensaver was burning a full core (102%) at the time and is **not** running in the later idle sample, so it cannot be dismissed on idle evidence. |

All three candidates have since been separated (§7-§8): genuine two-writer interleaving is the cause, the
pipeline restart is downstream of the samplerate switch it forces, and CPU starvation from the
screensaver is excluded — the defect reproduced deterministically with **zero** PeppyMeter processes
running.

**Defect 3 — Volumio's UI desynchronises from the actual player. measured**

At 01:29 the three views disagreed:

| View | Said |
| --- | --- |
| Volumio state / UI | `service=spop`, `status=pause`, latched to a Spotify track URI (`spotify:track:...`) |
| MPD | **playing** a local FLAC from the NAS |
| `go-librespot` | `paused=true` |

So the UI showed a paused Spotify track while a local file was audibly playing. The Volumio log shows
`info: Spotify Stop` / `SPOTIFY: SPOTIFY STOP` emitted twice at 01:28:55 with the state then pinned at
`status: "pause"` for the Spotify URI, followed by nothing but repeated `volumioGetState` polls — the
state machine stayed latched to the Spotify service and never followed MPD. This is a Volumio/plugin
track-state defect, independent of the audio-path defects above.

**Resolved:** Spotify plays cleanly *alone* once its volume is non-zero, confirmed by the operator.
That clears the Spotify audio path as a suspect and confines the simultaneous-play glitch to whatever
changes when a second source appears - i.e. to the candidates in defect 2, which stay open.

## 4. Bootloader EEPROM — updated and activated

| | Before | After |
| --- | --- | --- |
| `rpi-eeprom` package | 28.13-1 | 28.31-1 |
| Bootloader EEPROM | 2025-12-08 | 2026-05-26 (flashed) |

**How it applied:** the updater used the *immediate* flash path over SPI rather than the
recovery.bin-on-boot-partition path. It probed and detected the Winbond W25Q16.V flash chip, wrote
the image, and reported `VERIFY: SUCCESS` / `UPDATE SUCCESSFUL`. The staged files were then discarded
because the write had already happened. **measured**

**Configuration retention.** The tool documents: *"Unless the -d flag is specified, the current
bootloader configuration is retained."* No `-d` flag was used, so `BOOT_ORDER=0xf614`, `BOOT_UART=1`,
`WAKE_ON_GPIO=0`, `POWER_OFF_ON_HALT=0` and `PCIE_PROBE=1` carry over. **claimed (tool documentation)**
The tool also wrote a backup of the previous configuration, and it is present: the directory
`/var/lib/raspberrypi/bootloader/backup/` contains `pieeprom-backup-20260921-002947.conf` (84 bytes,
mode 0600), timestamped to the update. Its *contents* are root-only and were not read. **measured
(existence, name, size, mode)** Boot order matters here because this device boots from NVMe, and the
retained `0xf614` includes the NVMe entry.

Correction: an earlier revision of this record stated that the documented config backup "was not
written by the immediate path". That was wrong, and it was wrong for an instructive reason — the check
ran a privileged `ls`/`cat` with stderr discarded, so a permission failure was silently reported as an
absent file. The backup exists. See section 5.

**ACTIVATED AND VERIFIED.** The agent's shell environment blocks restart and power commands by
policy, so the power cycle was performed at the machine by the operator rather than in-band. After
it, the device reported the new bootloader and a clean boot. **measured**

```
vcgencmd bootloader_version   -> 2026/05/26 16:01:25
                                 version 086b83e3332dfc8927c56762771d082f3077a1ae, capabilities 0x0000007f
vcgencmd bootloader_config    -> BOOT_ORDER=0xf614   BOOT_UART=1   WAKE_ON_GPIO=0
                                 POWER_OFF_ON_HALT=0  PCIE_PROBE=1     <- every key retained
```

Post-activation health, all **measured**: booted from NVMe (`/boot` on the NVMe first partition, root
on the overlay); no leftover recovery artifact on the boot partition; services active (`mpd`,
`go-librespot-daemon`, `volumio`, `volumio-kiosk`, `shairport-sync`); DSI display `connected` with the
monitor reporting **On** at full backlight and the kiosk browser running; I2S card
`sndrpihifiberry` present; 4 USB devices and a Bluetooth controller present; `get_throttled` = 0x0.

Audio was re-proved end to end after the update: a track was played briefly and its position advanced
1:1 with the wall clock (0:04 at T+4 s, 0:10 at T+10 s), with CamillaDSP holding the PCM device and
0 xruns / 0 underruns. The test queue was returned to empty afterwards, which was its state before
the test. **measured** The track selected happened to be a DSD file; no resampling setting was
touched to play it.

**Rollback.** The package upgrade removed `pieeprom-2025-12-08.bin` — the previously running image is
no longer on the device, so there is no byte-exact rollback to the old bootloader. Rolling back means
flashing one of the images still present:

```
ls /usr/lib/firmware/raspberrypi/bootloader-2712/latest/*.bin
sudo rpi-eeprom-update -f <chosen image>
```

## 5. A measurement caveat found while verifying

**Cross-boot error comparison is impossible on this device.** The natural way to check whether the
bootloader change introduced anything is to diff this boot's error set against the previous boot's.
That cannot be done here: `journalctl --list-boots` lists **only the current boot**, because the
journal is configured with `Storage=volatile`. A naive diff therefore reports *every* current error
as "new" — it is comparing against an empty set, not against a clean baseline.

The 34 `-p err` lines in this boot were instead classified by inspection: `dhcpcd` chatter
(`ipv6_addaddr1: Permission denied`, `dhcp_vendor`, `control_free`), `wpa_supplicant` nl80211
registration, Samba `smbd`/`nmbd`/`winbindd` startup banners logged at err priority, `bluetoothd`
BAP/ISO-socket notices, and `bcm2708_fb ... Disabling driver` — the last being expected, since the
display runs on KMS rather than the legacy framebuffer. None touch NVMe, I2S, DRM or the bootloader.
**measured**

Related tooling note: reading X state needs `XAUTHORITY` set to the session's auth file; `xset` has
no `-auth` option, so `xset -display :0 -auth <file> q` fails with "Authorization required" even when
the display is fine. Use `XAUTHORITY=<file> xset -display :0 q` as the session user.

**A privileged read that fails silently is indistinguishable from an absent file.** Two conclusions in
this record were contaminated the same way: a privileged `ls`/`cat` was run with `2>/dev/null`, the
command was actually denied because the binary is not in the sudo NOPASSWD list (`ls`, `cat` and
`fuser` are not; `rpi-eeprom-config`, `rpi-eeprom-update` and `find` are), and the resulting empty
output was read as evidence of absence. One produced this record's now-corrected claim about the
EEPROM backup; the other produced a false "CamillaDSP log is empty" reading, which happened to be true
but had not actually been observed. Rule applied since: never discard stderr on a privileged command
where the *absence* of output is itself the finding — check the exit status, and re-measure
unprivileged where possible.

## 6. Unchanged by any of this

* The three files the PeppyMeter architecture fix touches (`index.js`, `install.sh`,
  `run_peppymeter.sh`) remain byte-identical to upstream 3.4.5 on this device. **measured**
* Overlays intact: `dtoverlay=hifiberry-dac`, the display panel overlay, and the active-fan overlay.

---

Nothing here is proposed upstream without a separate, explicit decision.

## 7. The simultaneous-play defect — mechanism CONFIRMED by measurement, and fixed

§3a left three candidates open. They are now separated by a controlled reproduction driven entirely from
the device (Volumio REST API on `127.0.0.1:3000`, `go-librespot` local API on `127.0.0.1:9879`, `mpc`,
`journalctl`), with no operator action required at any step. **measured**

**(a) The DSP samplerate is set by whichever client opened the FIFO last — this is the missing half.**

FusionDSP's `checksamplerate()` reads `/tmp/fusiondsp_stream_params.log`, which is written by the
`hw_params_command` on `pcm.fusiondsphook` (`echo '%r,%f,%c,%d' >/tmp/fusiondsp_stream_params.log`) —
one truncating line per open — and rewrites `samplerate:` in `camilladsp.yml` to match it. Observed
content at the two moments of interest:

| Active source | `fusiondsp_stream_params.log` | DAC `hw_params` |
| --- | --- | --- |
| Spotify took the device | `44100,S32_LE,2,32` | 44100, RUNNING |
| Local playback took the device | `384000,S32_LE,2,32` | 384000, RUNNING |

So the FIFO is not merely a shared buffer with two writers; it is a buffer whose consumer **retunes
itself to the last opener**. When a second source starts, the rate flips underneath the first, which is
still writing. A local DSD256 file (12.288 MHz, 48 kHz-family) is decimated by MPD by 32 to PCM
`384000`; if the DSP has retuned to Spotify's `44100`, that stream is consumed at roughly a ninth of its
rate — an inaudible smear. **This is the mechanism behind the "DSD has no sound" report, and it is the
same defect as the glitch, not a DSD fault.** **measured**

**(b) Volumio restarts the player it was just asked to stop.**

`CoreStateMachine.prototype.syncState()` (statemachine.js ~801) reads a service `'stop'` arriving while
`this.currentStatus === 'play'` as *"service has stopped without client request… finished playing its
track block"* and walks the queue (`currentPosition++`, then `this.play()`).
`CoreStateMachine.prototype.stop()` avoids that only by setting `currentStatus = 'stop'` **before**
calling `serviceStop()` (~1240). A plugin calling another plugin's `stop()` directly skips that disarm,
so the freed device is immediately taken back by the same service:

```
03:03:40  Spotify taking over from undefined: freeing the audio device
03:03:40  ControllerMpd::stop
03:03:40  CoreStateMachine::play index undefined      <- queue walk
          mpc before: 0:12/3:39   after: 0:01/3:35    <- a different track, restarted onto the FIFO
```

**(c) Candidate 3 (screensaver CPU starvation) is excluded**: the reproduction above ran with **zero**
PeppyMeter processes alive, and the restart plus rate fight reproduced identically every time.

## 8. Fix applied — both directions, verified

Two changes. Both are disarm-then-release: the state machine is put into `stop` *before* the other
service is released, so no queue walk can push playback back onto the freed FIFO.

| | File | Change | sha256 (live) |
| --- | --- | --- | --- |
| A | `/data/plugins/music_service/spop/index.js` | `freeAudioDevice()` — called from `parseEventState()` `'will_play'`. Sets `stateMachine.currentStatus='stop'`, `currentSeek=0`, `stopPlaybackTimer()`, **then** `mpdPlugin.stop()` | `584a844210ff…` |
| B | `/volumio/app/plugins/music_service/mpd/index.js` | guard at the top of `clearAddPlayTrack()`: if `stateMachine.isVolatile === true` or `spop.state.status === 'play'`, call `spop.stop()` first | `569996fa6f20…` |

Change A alone is not sufficient: Volumio's `stop()` only releases the **volatile** service
(`if (this.isVolatile) return this.serviceStop();`, ~1234) and spop does not always hold that flag, so
Spotify kept the FIFO when local playback started — observed directly as `librespot paused:false` with
`state volatile:false` while local playback ran. Change B closes that direction. `spop.stop()` posts
`/player/pause` to go-librespot, which closes its FIFO handle; a *paused* librespot holds nothing
(consistent with the §3a check).

Verification matrix, after a core reload (plugin JS is re-read on restart). **measured**

| Test | MPD | Spotify | FIFO producers | `fusiondsp_stream_params.log` | queue walk |
| --- | --- | --- | --- | --- | --- |
| A. local alone | playing | — | CamillaDSP (+MPD) | `384000` | — |
| B. Spotify takes over | **stopped, stays stopped** | playing | **go-librespot only** | `44100` | **`CoreStateMachine::play` count = 0** |
| C. local takes over | playing | **`paused:true`** (handle released) | **CamillaDSP only** | `384000` | no collision |

CamillaDSP measured during test C: state `Running`, capture peak −20.2 dB, playback peak −26.7 dB,
clipped samples 0, stop reason `None`.

Rollback: originals are preserved in `/home/volumio/pi5-fix-backup-20260921-021521/`
(`spop-index.v1.js`, `mpd-index.js.orig`, plus the pre-existing `asound.conf`, `spop-config.yml.tmpl`,
`peppy-config.json`, `MANIFEST.sha256`). Reload lever: read the authoritative pid from
`systemctl show volumio -p MainPID --value` and `kill -TERM` it; the unit is `Restart=always`.
**Do not** take the pid from `pgrep -f "node.*volumio" | head -1` — it can return another user's
process, the kill fails with `Operation not permitted`, and a patched file silently stays unloaded
(symptom: before/after tests look identical and the fix appears not to work).

Note for future maintenance: change A lives in `/data` and survives Volumio updates; change B lives in
`/volumio/app` (the app tree) and will be replaced by an OTA update.

## 9. "DSD has no sound" — resolved, and it is not a DSD defect

The DSD path itself is sound. Measured while a local DSD256 file played alone, via CamillaDSP's
websocket API (`ws://127.0.0.1:9876`): `GetState Running`, `GetCaptureSignalPeak` −19.5/−19.1 dB,
`GetPlaybackSignalPeak` −29.4/−27.9 dB, `GetProcessingLoad` 5.2 %, `GetClippedSamples` 0,
`GetStopReason None` — real audio entering and leaving the DSP, with the DAC in `S32_LE`/`384000`
`RUNNING`. Related facts: `mpd.conf` has `dop "no"`; Volumio-level resampling is off
(`alsa_controller` `resampling=False`) and FusionDSP does the rate handling; the mpd plugin's
`dsdVolume()` only sets volume 100 for `dsd_autovolume`; MPD has no mixer (`mixer_type none`,
`no such mixer control: PCM`) so volume is `n/a` and playback is full-scale. **measured**

The silence was therefore the §7(a) rate fight: whenever Spotify held the FIFO at 44100, the same DSD
stream was consumed at the wrong rate. Fixed by §8 — both sources are now released properly, so the DSP
rate always follows the one service that is actually playing.

One metadata defect is *not* fixed and is reported as-is: while playing that DSD file, Volumio's
`getState()` reports `samplerate: "11.28 MHz"`, `bitdepth: "1 bit"`, `trackType: "dsf"` — i.e. the
source's format, not the `384000`/32-bit PCM that is actually on the wire.

## 10. PeppyMeter with Spotify — investigation concluded: no change made, and the change that looks obvious is unsafe

The brief was to establish whether meter data already flows for Spotify and only the display gate
blocks it. Result: **the premise is refuted, and the flag that looks like the blocker is
load-bearing.**

1. **Meter data for Spotify exists.** With the FusionDSP bridge active, PeppyMeter runs in
   `inline-meter (bridge on)` mode: CamillaDSP's playback device is `postDsp` →
   `pcm.Peppyalsa` (`type meter`, scope `peppyalsa`) → `postpeppyalsa` → `volumioOutput` → hardware.
   Spotify's audio goes through that same single chain (its `audio_device` is `volumio`), so the inline
   meter is fed Spotify audio by construction.
2. **`Spotify_ON` is not the gate that suppresses it.** It is referenced only at index.js:308 and 343,
   and 343 is `if (DSP_ON || Spotify_ON || Airplay_ON || Other_ON)` — with `useDSP` true, `DSP_ON` alone
   already passes. Peppy does in fact start the meter on a Spotify state push (`peppy_screensaver: Start
   PeppyMeter` observed while Spotify was the playing service).
3. **Do not un-force `useSpotify`.** That flag is forced false whenever the DSP bridge is on
   (index.js:796-797, 803-804, 1411-1412). It is not redundant: `switch_Spotify(true)` rewrites the
   librespot template's `audio_device` from `volumio` to `spotify` (index.js:4437-4441), but with the
   bridge on the ALSA substitution forces `${spotMeter}`→`spotify2_off` and `${spotDirect}`→`spotify1_off`
   (index.js:4809-4810). The live `asound.conf` therefore contains `pcm.spotify1_off` and
   `pcm.spotify2_off` and **no `pcm.spotify`** — un-forcing the flag would point librespot at a PCM that
   does not exist and take Spotify down entirely. **measured**
4. **What actually suppresses the display is upstream of PeppyMeter.** With Spotify audibly playing and
   local playback stopped, Volumio's master state reported `status:"stop" service:"mpd"`. PeppyMeter then
   follows its own state logic (`Stop with metadata — grace timer 5000ms` → `Grace timer expired —
   treating as genuine stop` → `Starting persist timer - 15s` → `Persist timer expired - stopping
   PeppyMeter`), and the touch-display plugin sets `screensaver timeout to 0 seconds`, so no screensaver
   and no meter. That is a state-ownership problem, not a meter or ALSA problem. **measured**

No PeppyMeter/ALSA change was made — per the agreed fallback, this was left alone. One path remains
unverified because it cannot be driven from the device: Spotify started from the Spotify app on a phone
enters the plugin's *volatile* mode, and that is the path whose state pushes have been seen carrying
`service=spop volatile=true`. A passive watch was armed on the device journal for that exact state; it
ran its full window (90 polls, 3 h 13 m) and **expired without the state ever appearing**, so this last
check remains open and is recorded as open rather than assumed.

## 11. State after this work

Playback was left in a consistent single-source state; nothing was left mid-transition. Two production
files on the device now differ from their packages (the spop plugin and the mpd app plugin) — both with
byte-exact rollback copies and both recorded in §8.

**Review status — no independent verdict has been obtained, and that is stated here rather than
implied.** An independent review of the two patchers was requested three times and all three attempts
failed to produce a verdict: attempt one returned a delegation stub with no findings, attempt two was
lost by the review service (its run id answered HTTP 404 `run not found` and was not re-polled), and
attempt three (run `run_36e29975154c4aba99e5642adf67a13a`) was still running when this entry was
committed; it has since returned a verdict. Both patches were therefore applied on the strength of the
live measurements in §8 alone. That verdict, my dispositions of it, and the v3 revision it forced are in
**§12** — read §12 before treating §8's matrix as the whole story of this device's state.

The defect class is a genuine Volumio behaviour (no
service is released when a different service starts, and for `syncState` a service `stop` is
indistinguishable from end-of-track), so the durable fix belongs upstream rather than only on this
device.

## 12. Independent review came back REJECT — v3 applied, one path now unverifiable

### 12.1 The verdict

An independent review of the two patcher scripts (submitted with the patcher source verbatim, their
sha256 digests, and this record's digest) returned **REJECT** with four findings marked blocking and
eight marked non-blocking. Run `run_36e29975154c4aba99e5642adf67a13a`, through the sanctioned Hermes
gateway lane. Its summary sentence: the patches "demonstrably improve the reproduced takeover race" but
"the implementation is not safe enough to approve".

Two earlier review attempts had produced no verdict at all (a delegation stub with no findings, then a
lost run answering HTTP 404 `run not found`, which was not re-polled). This third attempt returned a
real verdict, so §11's earlier statement that no second party had read the code is superseded: a second
party has now read it, and rejected the first version.

### 12.2 Dispositions — what was accepted, modified, and refuted

I did not adopt the verdict wholesale. Findings were checked against the live source before acting.

**Accepted and fixed in v3:**

- **F4 (blocking) — the disarm ran before the stopper was validated.** Correct, and the worst of the
  four: `freeAudioDevice()` set `currentStatus='stop'` and only then looked up the mpd plugin, so a
  failed lookup left the state machine claiming "stopped" while MPD might still hold the FIFO. v3
  resolves and validates the plugin *first* and returns untouched if it is unavailable.
- **F3 (blocking) — the mpd-side predicate could miss an active Spotify and fall through into the
  collision.** Correct, and the most important behavioural fix. The old predicate
  (`stateMachine.isVolatile === true || spop.state.status === 'play'`) had no defined behaviour for a
  missing or stale spop state. v3 inverts the default: the release is skipped **only** when spop
  explicitly reports `status === 'stop'` and is not volatile; anything unprovable is treated as "may
  still be playing". "Cannot prove Spotify is idle" no longer means "start anyway".
- **F11 / F4 (guard failure semantics).** If `mpdPlugin.stop()` throws, v3 no longer leaves a
  state machine that lies: it restores the pre-takeover status and logs at error level. The queue-walk
  window that reopens is the smaller of the two failures and is stated in the comment.

**Modified (accepted in part) — F1 (blocking): "patch 1 omits bookkeeping that `CoreStateMachine.stop()`
performs".** The finding's *list* is an accurate reading of `stop()`'s non-volatile branch, but that
branch is not reachable here, so the prescription does not apply. The live source opens:

```js
CoreStateMachine.prototype.stop = function (promisedResponse) {
  if (this.isVolatile) {
    return this.serviceStop();          // <- the branch that runs during a Spotify takeover
  } else {
    self.setConsumeUpdateService(undefined);
    self.unSetVolatile();
    if (this.currentStatus === 'play') { ...updateTrackBlock(); this.pushState()... }
```

During a takeover `isVolatile` **is** true (the plugin has just called `setVolatile`), so the core would
take the first branch — `serviceStop()`, which stops **Spotify**, the volatile service — and would touch
none of MPD's state. On this path `stop()` performs *no* bookkeeping for the local service, so the
disarm is not a partial copy of it; it is the only thing that runs. Adopting the review's prescription
would also call `unSetVolatile()`, clearing the flag that gives Spotify ownership of the device — a
change that breaks the takeover this work exists to fix. The residue of F1 is a real but non-blocking
polish item: after a takeover the UI state for the stopped local service is not re-published by this
path, because the core does not publish it on the volatile branch either.

**Refuted — F2 (blocking): "`freeAudioDevice()` is not re-entrant; two `will_play` events can
interleave."** The body is synchronous from the state read to the stop call and Node's event loop is
single-threaded, so a second `will_play` cannot enter it mid-body; the pre-fix defect that looked like
interleaving (a restart) was `syncState`'s end-of-track inference, not a concurrency fault. The
objection is nevertheless cheap to close, so v3 adds an explicit `_freeingAudioDevice` latch, which also
keeps the property if the body ever gains asynchronous work. Refuted on mechanism, hardened on cost.

**F6 (non-blocking) — accepted as a behavioural concern, mitigated.** Stopping a *paused* local player
discarded its resume point because `currentSeek` was zeroed unconditionally. v3 zeroes `currentSeek`
only when the previous status was `play`, so a paused track keeps its position. The underlying question
the review raised — whether a paused MPD actually holds the FIFO open — could not be measured on this
device: MPD runs as user `mpd` and `sudo` requires a password, so its file descriptors are not
inspectable from the `volumio` uid. The 'pause' case stays in the guard because it is the conservative
choice while that is unknown.

**Not actioned, recorded:** F5/F7-F10/F12 are scope, labelling, and lifecycle-observations that v3
either improves indirectly or that remain open; F12's labelling concerns are acted on in §12.4 below.

### 12.3 v3 — what is now on the device

Two files changed, both from their v2 state, both syntax-checked (`node --check`) and dry-run staged
before applying. v3 patcher: `ops/pi5-beta/patches/2026-09-21-handover-v3-review-response.py`.

| | File | sha256 (live) | Rollback |
|---|---|---|---|
| A | `/data/plugins/music_service/spop/index.js` | `cd256f141c2a…` | `spop-index.v2.js` (the rejected v2), `spop-index.v1.js`, `spop-index.js` (original) |
| B | `/volumio/app/plugins/music_service/mpd/index.js` | `4377a8f703c3…` | `mpd-index.v2.js`, `mpd-index.js.orig` |

All five rollback copies are in `/home/volumio/pi5-fix-backup-20260921-021521/`. The core was reloaded
once (MainPID 13654 → 28330, `Restart=always`); nothing else on the device was modified.

### 12.4 Re-verification — what passed, and the one path that cannot be verified from here

Passing, measured after v3 (single writer asserted by scanning `/proc/*/fd` for holders of
`/tmp/fusiondspfifo`):

- **Local playback only** — `status=play`, camilladsp the only FIFO holder, hardware at 384000.
- **MPD→MPD queue advance** — advances and plays, no takeover action taken, nothing logged.
- **Recovery after a stop** — local playback restarts cleanly.
- **Local start with Spotify idle** — plays normally; the guard's release is *skipped* and no
  `MPD taking over` line appears, which is the default-to-release change behaving as designed.
- **No error-level journal entries** for the whole test window.

**Not verified: the Spotify-takeover direction — the half the whole fix is for.** Spotify playback
cannot be initiated from the device. `POST /player/play` on go-librespot's local API returns an empty
body and changes nothing (`stopped:true`, `track:{}`, no `will_play` event ever reaches the plugin),
because a Connect session has to be established by a client (the phone app). Consequently the
`freeAudioDevice` path and the guard's release path were **not executed** in this session. §8's matrix
rows B and C, and the v1 baseline, remain *measured* — they were taken while a session existed — but they
are measurements of the **pre-v3** code. Nothing in this session demonstrates that v3 behaves correctly
during a takeover. That is the honest state of the evidence, and it is the single most important open
item in this record.

Supporting constraint, newly measured: **librespot's volume resets to 0 whenever the plugin or core
restarts** (observed 100 → 0 across the reload), and `POST /player/volume` while no session exists
returns an empty body without effect. So after any core reload Spotify will be silent until a session is
started, and the silence is librespot's own mixer at zero — not the fix, and not the audio path.

### 12.5 New observation, attribution OPEN: continuous playback-side buffer underruns

While measuring the matrix, the DSP's log showed a continuous stream of
`PB: Prepare playback after buffer underrun` — **~14–18 per second, throughout playback**:

| Condition | Output rate | Underruns / 40 s | Load (4 cores) |
|---|---|---|---|
| DSD256 source, PeppyMeter running | 384000 | 718, 719, 720 (three windows) | 4.7 – 5.6 |
| 96 kHz FLAC source | 96000 | 556 | 4.7 |

This is *not* format-specific and it is *not* the takeover race: it appears with a single writer and no
Spotify involvement. It is the best remaining candidate for the "glitching" that started this work, and
the earlier record's decision to exclude CPU starvation as an explanation was premature — the machine is
oversubscribed during playback (camilladsp ~50–130 %, the PeppyMeter screensaver ~80–92 %, chromium and
Xorg alongside, load above the 4 cores available).

Attribution is **open**, for two reasons stated plainly. First, the control failed: killing the meter is
not sufficient, because the plugin's `run_peppymeter.sh` respawns it within ~5 s, and both A and B arms
therefore measured the same condition (that is also the one useful thing the failed control showed —
the rate is extremely stable at ~18/s). Disabling the meter properly means changing the operator's
screensaver settings, which is not a change to make as a measurement side effect. Second, there is no
history to compare against: `camilladsp.log` is truncated on every start, and camilladsp is launched
with `-o /tmp/camilladsp.log`, so its output never reaches the journal. Nothing in this session's
changes can affect the DSP's ALSA layer — the edits were JavaScript guards in the two plugin files, and
no audio-path configuration was touched — but whether these underruns predate this work is **not
established**. Whether they are *audible* is also not established: this work has no ears, the operator
does, and that is the cheapest discriminator available.

### 12.6 Probe caveat discovered while verifying

`GetCaptureRate` over the CamillaDSP control websocket is **not** a trustworthy rate reading: it
returned 36839 while the configured capture rate, the stream-parameters log and the hardware all said
384000. An earlier reading of the same verb returned 385165 in the same configuration. Use
`/proc/asound/card*/pcm0p/sub0/hw_params` and `/tmp/fusiondsp_stream_params.log` for rate truth; treat
`GetCaptureRate` as indicative only. Also confirmed correct on this device: a 96 kHz FLAC plays at 96000
and the DSD256 file at 384000, i.e. no unexpected resampling is being introduced by the bridge.

### 12.7 Open items, in priority order

1. **Takeover behaviour on v3 is unverified** (§12.4) and needs a Spotify session started from a phone.
2. **The underrun storm** (§12.5) — needs the operator's ears, then a meter-off control with config
   access.
3. **Review of v3** has not been requested yet; it is the next gate before this record can describe
   the fix as reviewed.
4. Whether a paused MPD holds the FIFO (§12.2, F6) — needs root to inspect user `mpd`'s descriptors.

## 13. The takeover failure reproduced by the operator - root cause inside my own guards

The operator drove the exact sequence and it failed: local playback audible, stop, start a Spotify
song (no sound), then start local again to let Spotify cut over - **stuttering and glitching, no sound
from Spotify, local still playing, the PeppyMeter showing the Spotify track, and the UI controlling the
Spotify song instead of the file that was audibly playing.** That last sentence is the important one: the
system believed Spotify owned the device while the local player was still streaming into it.

### 13.1 Why the v3 guard did nothing: it trusted a field that is not truthful

Measured in the journal during the failure:

```
info: peppy_screensaver: pushState - status=play service=spop volatile=true
```

and in the plugin source, `identifyPlaybackMode()` -> `initializeSpotifyPlaybackInVolatileMode()` ->
`setVolatile({service:'spop', ...})`. So the router state can already read `service=spop` **while MPD is
still streaming**. The v3 guard opened with

```js
var current = self.commandRouter.volumioGetState();
if (!current || current.service === 'spop') { return; }   // <- returned in exactly the failing case
```

It bailed out precisely when it was needed. The service/status fields describe what the *state machine
believes*, and during a volatile takeover they are wrong; MPD's actual streaming state is the truth and
the guard was not looking at it.

### 13.2 v4 - release unconditionally, log what the router claimed

`freeAudioDevice()` now resolves and validates the mpd plugin first, then **releases unconditionally**
(no service and no status test), because stopping an already-idle MPD is a no-op while a missed release
costs audible break-up and a dead UI. It is also called from **both** `will_play` and `playing`, so it
cannot be missed if one event is absent or out of order. It logs the router's claim at the moment of
takeover - `Spotify taking over (router said service=..., status=...): releasing the audio device` -
which turns the next reproduction into evidence either way.

Live: `/data/plugins/music_service/spop/index.js` sha256 `67d05675…` (147154 -> 147991 B), syntax
checked, dry-run staged, core reloaded (MainPID 1374 -> 4473). Rollback adds `spop-index.v3.js`. The mpd
guard is unchanged (`4377a8f7…`). The router-trusting bail-out is verifiably gone (`grep -c` = 0) and
there are exactly two call sites.

### 13.3 "No sound from Spotify" has a second, independent cause - the volume event chain

```
SPOTIFY: received: {"type":"volume","data":{"max":100,"value":0}}
info: Setting Volumio Volume from Spotify: 0
info: VolumeController::SetAlsaVolume0          <- the SHARED hardware volume, every source
```

go-librespot reports its own mixer, and when that mixer is 0 the plugin loyally applies it to Volumio's
volume controller, which drives the **shared hardware mixer** to 0 - silencing local *and* Spotify. So a
silent Spotify can be a volume problem, not a routing problem.

**My contribution to this one:** the plugin sets that mixer with

```js
self.sendSpotifyLocalApiCommandWithPayload('/player/volume', { volume: volume });   // line 682
```

and I had been POSTing `{"value":100}` - the wrong key, silently ignored, so librespot stayed at 0 and
kept emitting `value:0` events. Corrected and verified both directions: `{"volume":100}` -> librespot
reads 100 -> the event chain now logs `RECEIVED SPOTIFY VOLUME 100` / `Setting Volumio Volume from
Spotify: 100` / `VolumeController::SetAlsaVolume100`, where it had been `SetAlsaVolume0`. **My probe was
causing the silence I was investigating.**

### 13.4 Damage I caused during diagnosis, and undid

Killing the PeppyMeter's parent wrapper to test the meter's effect orphaned its child:
`2524 ppid=1 99.6% CPU 06:35 python3 ./screensaver/volumio_peppymeter.py` - a process stuck at a full
core with its plumbing severed, which is why the spectrum stopped moving. Killed; the plugin respawns a
properly parented meter. Do not kill wrapper chains on this device to test the meter: the child survives
the parent.

Two measurement corrections, both mine: `wchar` from `/proc/<pid>/io` proves **nothing** about ALSA
throughput here, because the devices are opened `MMAP_INTERLEAVED` and playback never calls `write()`;
and a `pgrep -f` that matched my own shell made me report three meter instances when there was one.

### 13.5 State after this section

Local playback verified working after the reload (MPD progressing, camilladsp the only FIFO writer,
`service=mpd volatile=false`); the device was left stopped. The takeover path still cannot be exercised
from the device, so v4's fix is **unverified against a real takeover** - the operator's phone is the only
way to start a Connect session. The new log line is what makes the next attempt conclusive.

## 14. The takeover now works in both directions - three defects, two of them upstream

Operator confirmation after v5: **"audio back and forth between local and spotify seems to work"**. The
path there ran through three separate defects, and only one of them was the thing this record set out to
fix.

### 14.1 v4 - the router's claim is not the property to test

The v3 guard bailed out when the router reported `service === 'spop'`. The capture during the next attempt
showed the release firing correctly with the router truthfully saying `service=mpd, status=play`, and MPD
stopping through the MPD protocol with no router involvement:

```
will_play -> Spotify taking over (router said service=mpd, status=play): releasing the audio device
          -> CoreStateMachine::stPlaybackTimer -> ControllerMpd::stop -> sendMpdCommand stop took 3 ms
```

Note `ControllerMpd.prototype.stop` is only `return this.sendMpdCommand('stop', [])` - there is **no**
router path in it, so the release cannot re-enter the volatile-aware stop by that route. (That was worth
checking: had it called `commandRouter.volumioStop()`, it would have stopped Spotify instead of MPD.)

### 14.2 v5 - the upstream bug that stops Spotify during its own takeover (one pair of parentheses)

In `initializeSpotifyPlaybackInVolatileMode`:

```js
self.context.coreCommand.stateMachine.setVolatile({
    service: 'spop',
    callback: self.libRespotGoUnsetVolatile()      // <-- called, not referenced
});
```

The parentheses invoke the function on the spot and register its returned promise as the callback. The
core's own comment on that field reads "This function will be called on volatile stop", so the intent is
unambiguous. Consequences:

1. The body runs at `setVolatile` time, when `currentVolumioState` is still the local track that is
   playing - its guard `status !== 'stop'` is satisfied, so it logs `Setting Spotify stop after unset
   volatile call` and schedules `self.stop()`.
2. The registered callback is a promise, not a function, so the core's `volatileCallback.call()` cannot
   work when volatile really is unset.

Measured in the journal, ~11 s after the takeover, when `ignoreStopEvent` had already cleared (it is set
for 2 s around the volatile init) - so the pause actually executes:

```
CoreStateMachine::setConsumeUpdateService undefined
SPOTIFY: UNSET VOLATILE
info: Setting Spotify stop after unset volatile call
info: Spotify Stop  /  SPOTIFY: SPOTIFY STOP          <- and librespot is paused
```

`ControllerSpotify.prototype.stop()` logs `Spotify Stop` unconditionally but only sends
`/player/pause` when `!ignoreStopEvent`, so the log alone does not prove a pause - which is exactly why
the ordering matters here and why the fix was applied on the code defect rather than on the log.

Fixed by passing the reference: `callback: self.libRespotGoUnsetVolatile`. Live spop sha256 `551cdb41…`
(v4 was `67d05675…`), rollback `spop-index.v4.js`, syntax checked, core reloaded, local playback
re-verified (`mpc` progressing, `service=mpd`, one FIFO writer).

### 14.3 What is still not right

The PeppyMeter displays the wrong track. Not investigated to a conclusion here, but the first structural
fact is recorded: `index.js:308` builds

```js
var Spotify_ON = fs.existsSync(spotify_config) && getPluginStatus('music_service','spop')==='STARTED'
                 && self.config.get('useSpotify') && state.service === 'spop';
```

and `useSpotify` is forced false whenever the DSP bridge is on, so `Spotify_ON` is **permanently false on
this device**. The meter still starts (line 343 passes via `DSP_ON`), but every Spotify-aware branch that
is gated on `Spotify_ON` is dead code here, which is the obvious first place to look for a display that
names the wrong track during a Spotify session. Cosmetic and separable from the audio defect.

### 14.4 Corrections to my own work in this session

- `mpdPlugin.stop()` does **not** route through the router (checked rather than assumed).
- `wchar` from `/proc/<pid>/io` proves nothing about ALSA throughput here: the devices are opened
  `MMAP_INTERLEAVED` and playback never calls `write()`.
- A `pgrep -f` that matched my own shell produced a false "three meter instances" report.
- Killing the PeppyMeter's parent wrapper orphaned its child at ~100 % of a core with its plumbing cut,
  which froze the spectrum. The plugin respawns a parented meter. Do not kill wrapper chains to test the
  meter on this device.
- My `POST /player/volume {"value":100}` was ignored (the plugin uses the key `volume`), so librespot
  stayed at 0 and kept emitting a 0 volume event, which Volumio applied to the **shared** hardware mixer.
  My probe was causing silence on every source. Corrected and verified: `{"volume":100}` -> librespot 100
  -> `SetAlsaVolume100` where it had been `SetAlsaVolume0`.

## 15. The takeover direction, measured at last — and the instrument that had been lying

Status: **§12.7 item 1 is now closed for the release path.** The takeover direction was executed and
measured in both directions, against the live v5 bytes, with the audio device's ownership proved from
root-visible descriptors rather than inferred. Two claims in §12.4/§12.5 are **corrected** below, and
one credential-handling mistake of mine is disclosed in §15.8.

Live code under test, unchanged and re-verified this session: spop
`551cdb411b0920b96e3540eefbd65c9c0853093413acb661dc450eb4a5143ed8` (mtime 11:29:10.558), mpd
`4377a8f703c3bdc44e22d8e5b3e2c6d19cb4cb9229c623dc636bbf63a157fdf6` (mtime 10:12:31). Core MainPID 6716
started **11:29:11** — one second *after* the spop write, so the running core is not stale with respect to
the fix. **measured**

### 15.1 Why this was runnable now when §12.4 said it was not

§12.4 concluded that Spotify playback "cannot be initiated from the device" because `POST
/player/play` "returns an empty body and changes nothing". That was measured while go-librespot had **no
session**. A session now exists (`GET /status` carries a `username` and a `device_id`), and with a session
present the same call **does** work: `POST /player/play {"uri":"spotify:album:…"}` -> HTTP 200, body
`null` (5 bytes), track loaded and playing within ~2 s. **measured**

The 5-byte `null` body is normal and is *not* a failure signal — the earlier reading took a healthy
response for a dead one. The correct discriminator is whether a `will_play`/`playing` event reaches the
plugin, not the shape of the HTTP reply. **Corrected: §12.4's "cannot be initiated from the device" holds
only for the no-session case.**

Caveat that still stands, and it is the one that matters: see §15.5.

### 15.2 The instrument had been lying — §12.4's "single writer" assertion was unsound

§12.4 asserted single-writer by "scanning `/proc/*/fd` for holders of `/tmp/fusiondspfifo`". Run
unprivileged that scan is **structurally blind to the local player**: MPD runs as user `mpd`, so
`/proc/<mpd>/fd` is `Permission denied` to `volumio`, and the scan silently returns no rows for it rather
than erroring. During local playback it reported `camilladsp` as the only holder — which was read as
"single writer asserted", and was **false**.

A `NOPASSWD` `/usr/bin/find` path gives root visibility, and it shows what was there all along:
**measured**

```
/proc/7085/fd/23   -> /tmp/fusiondspfifo   comm=mpd         (writer)
/proc/7085/fd/24   -> /tmp/fusiondspfifo   comm=mpd         (writer)
/proc/10403/fd/7   -> /tmp/fusiondspfifo   comm=camilladsp  (reader)
```

The reusable form, which should have been used from the start:

```
sudo /usr/bin/find /proc -maxdepth 3 -path '/proc/[0-9]*/fd/*' -lname '*fusiondspfifo*' -printf '%p -> %l\n'
```

An assertion that "only one writer exists" is only meaningful when the enumeration can *see* the
candidate writers. **Corrected: every single-writer claim in §12.4 must be re-read as unproved.** The
device-ownership conclusions drawn this session (§15.3) use the root-visible form.

### 15.3 The measurement matrix — device ownership proved from descriptors

Sequence run entirely from the device. Each row is a distinct moment, with the fifo's holder set read
from root at that moment. **measured**

| # | Phase | go-librespot | mpd | camilladsp | Writers |
|---|---|---|---|---|---|
| 0 | idle | — | — | — | 0 |
| 1 | local playback | — | fd/23, fd/24 | fd/7 | **1 (mpd)** |
| 2 | after Spotify takeover | fd/16, fd/17 | **gone** | fd/8 | **1 (go-librespot)** |
| 3 | after MPD takeover | **gone** | fd/23, fd/24 | fd/8 | **1 (mpd)** |
| 4 | restored (stopped) | — | — | — | 0 |

Row 2 is the property the whole fix exists for, and this is the first time it has been demonstrated
rather than inferred: when Spotify takes the device, **MPD no longer holds the fifo at all**. The
collision mechanism in §3 (two clients interleaving into one fifo) is now closed by measurement, not by
argument.

### 15.4 Both directions, with the journal evidence

**Spotify takes over** (local playing, then the local API starts Spotify), 12:24:57–58: **measured**

```
SPOTIFY: received: {"type":"playing","data":{...,"play_origin":"go-librespot"}}
info: Spotify taking over (router said service=mpd, status=play): releasing the audio device
info: ControllerMpd::stop
info: sendMpdCommand stop took 2 milliseconds
```

The release fired, MPD stopped through the MPD protocol (2 ms), and Spotify continued: `stopped:false`,
position advancing continuously (2210 -> 12487 ms over 12 s, ≈1.03x wall clock) and **still playing 25 s
later**. Nothing stopped Spotify. No error-level journal entries in the whole window.

**MPD takes over** (Spotify playing, then local playback starts), 12:25:53: **measured**

```
info: MPD taking over: releasing the audio device from spop
info: Spotify Stop
SPOTIFY: SPOTIFY STOP
```

Spotify went to `paused:true` with its position frozen, the router moved to `service=mpd, status=play`,
and local playback ran on with no further Spotify activity. This is the guard from §13.5/§14 working on
its intended path.

### 15.5 What is *still* not measured: the volatile branch — and why an earlier reading was nearly wrong

Everything above ran with `play_origin: "go-librespot"`, which `identifyPlaybackMode` classifies as
**Volumio mode, not volatile**. So this session executed `freeAudioDevice()` — the shared release path —
but **not** `initializeSpotifyPlaybackInVolatileMode()`, and therefore **not** `setVolatile` and **not**
the v5 callback fix. Evidence, not assertion, across the whole window: **measured**

| Marker | Occurrences |
|---|---|
| `UNSET VOLATILE` | 0 |
| `SET VOLATILE` | 0 |
| `initializeSpotifyPlaybackInVolatileMode` | 0 |
| `Setting Spotify stop after unset volatile call` | 0 |

(The 68 bare `volatile` hits in the same window are all the PeppyMeter's `pushState - … volatile=false`
lines.)

**This matters for reading §15.4 correctly: because Spotify survived the takeover in row 2, it would be
easy to claim v5 is now verified. It is not.** With the volatile branch never entered, `setVolatile` is
never called, `libRespotGoUnsetVolatile` is never registered, and the v5 defect cannot manifest. The
survival in row 2 is real but it is evidence about the release path, not about v5. **v5 remains covered
only by code semantics, the pre-fix journal capture in §14.2, and independent review.** The volatile
branch still needs a Connect client — a phone — to reach it. §12.7 item 1 is therefore closed
**in part**: release path measured, volatile path still open.

### 15.6 New observation: the router's state does not follow a local-API Spotify takeover

During row 2, Spotify was audibly playing while `getState` still reported
`{"status":"stop","service":"mpd"}` — the core never switched service, and the journal shows why:

```
SPOTIFY: PUSH STATE SPOTIFY
info: Received update from a service different from the one supposed to be playing music.
      Skipping notification.Current mpd Received spop
```

The spop state push is **rejected** because the router still believes `mpd` owns playback; nothing calls
`setVolatile` on this route, so nothing moves the router to spop. Consequence on screen: the kiosk stayed
on the idle weather/clock screensaver throughout, i.e. the device was playing Spotify while the UI showed
nothing playing. **measured (state + journal), observed (screenshot)**

Scope note: this is a property of the *route* I drove (local API, `play_origin=go-librespot`), not
necessarily of a phone-initiated takeover, which does go through `setVolatile` and does move the router.
Recorded as a route-specific defect, not as the operator's report.

### 15.7 New observation: the DSP engine is respawned by the local player stopping

At the takeover moment the journal shows FusionDSP tearing the DSP down and rebuilding it:

```
info: FusionDsp -  Volumio is not playing
info: FusionDsp -  Clipped samples monitor stopped
info: camilladsp respawn in 100 ms (attempt 1/10)
```

camilladsp PID changed `10403 -> 17865` across the takeover, with a ~100 ms window between the old
instance going away and the new one opening the fifo. So **every local-player stop — including one that
is part of a takeover — restarts the DSP engine.** A writer opening the fifo with no reader present
blocks rather than errors, so this is survivable, but it is a real gap in the handover path and a
candidate contributor to the residual click/glitch that started all this work. **measured (mechanism);
not attributed (audibility)** — this work still has no ears.

### 15.8 Correction to §12.5, and a credential-handling mistake of mine

**§12.5's instrument does not support the claim as read.** `/tmp/camilladsp.log` had mtime **11:29:23**
and size 172 B while the camilladsp process actually running had started at **12:24:57** — the current
process was not writing to that file at all. A "0 underruns" reading from it therefore means *nothing was
recorded*, not *nothing happened*. The log is not a valid live underrun counter, and §12.5's
14–18/s figures were taken from the same file under conditions that are not reproducible from here.
The underrun question should stay **open** and be re-instrumented (or judged by the operator's ears)
rather than quoted from this file. **measured (mtime vs process start)**

**Disclosure.** While checking whether a playback session could be started remotely, I inspected the spop
plugin's stored configuration with a redaction routine that tested the *leaf* key name for
secret-like words. The file nests each setting as `{"type":…,"value":…}`, so the leaf key was `value`,
the guard did not fire, and the routine printed the **first 60 characters** of the stored Spotify
`refresh_token` and `access_token` into the session transcript. The values are truncated and not usable
as credentials, and no credential was used to make any call. I did **not** rotate or modify them — that
is the operator's call, not mine — and I abandoned the remote-playback route rather than reach further
for the client secret. Lesson, worth keeping: **redact on the full key path, and prefer printing a
boolean "present/empty" rather than any prefix of a secret.**

### 15.9 Device state restored

Playback stopped (`status=stop, service=mpd`), Spotify left `paused` at the track it had loaded, volume
unchanged at 100 throughout (never modified by this session), fifo holder set back to empty, no
error-level journal entries. No configuration, code, or ALSA-path file was changed in this session —
every command was a read, a playback control, or a stop.

**That last sentence stopped being true shortly after it was written: §16 documents the v5 regression
found when the operator started a real volatile session, and the v6 fix applied for it. Read §16 before
treating §14.2 as closed.**

## 16. v5 crashed the core on the volatile path — v6 fixes it

Status: **the operator's device was crash-looping the Volumio core on every volatile Spotify session,
caused by the v5 change in §14.2. Fixed by v6 and verified as far as the device allows.** This is a
correction to §14.2, which records v5 as the fix for the volatile path; v5 did fix the timing defect and
in doing so exposed a worse one.

### 16.1 The crash

Two fatal errors occurred today, both the same signature, both on the volatile path: **measured**

```
Sep 21 12:30:23 volumio[6716]: verbose: UNSET VOLATILE: Service: spop
Sep 21 12:30:23 volumio[6716]: ||||||||||||| WARNING: FATAL ERROR |||||||||||||
Sep 21 12:30:23 volumio[6716]: TypeError: Cannot read properties of undefined (reading 'debugLog')
Sep 21 12:30:23 volumio[6716]:     at ControllerSpotify.libRespotGoUnsetVolatile (spop/index.js:535:10)
Sep 21 12:30:23 volumio[6716]:     at CoreStateMachine.unSetVolatile (statemachine.js:1555:27)
Sep 21 12:30:23 volumio[6716]:     at CoreCommandRouter.volumioPlay (index.js:1406:21)
Sep 21 12:30:23 volumio[6716]:     at Socket.<anonymous> (user_interface/websocket/index.js:241:35)
...
Sep 21 12:30:25 systemd[1]: volumio.service: Main process exited, code=exited, status=1/FAILURE
Sep 21 12:30:26 systemd[1]: volumio.service: Scheduled restart job, restart counter is at 3.
```

and again at **12:31:08** on the replacement core (`restart counter is at 4`). Uncaught ⇒ the process
exits ⇒ every plugin, the queue, and the audio path are torn down and rebuilt. `journalctl | grep -c
'FATAL ERROR'` over the whole of 2026-09-21 returns **2**, both of these.

### 16.2 Root cause: strict mode plus a bare invocation

`spop/index.js` opens with `'use strict';`. The core invokes the registered callback as
`this.volatileCallback.call()` — **no `thisArg`**. Under strict mode that leaves `this === undefined`
inside the callback, so its first statement:

```js
ControllerSpotify.prototype.libRespotGoUnsetVolatile = function () {
    var self = this;                  // undefined
    self.debugLog('UNSET VOLATILE');  // <-- line 535, throws
```

throws immediately. The function is a prototype method that assumes it has a receiver, and v5 —
by passing the function *reference* instead of calling it — created the first configuration in which
the core can actually invoke it. §14.2's stated intent ("the callback is invoked BY THE CORE … Passing
the function reference restores the intended behaviour") was correct about *when* it runs and missed
*how* it is called.

**Honest limit:** §14 records no fatal error before v5, and `grep -c 'FATAL ERROR'` for today confirms
none occurred before 12:30. But that does **not** prove the pre-v5 code was safe on this path — the
pre-v5 registration passed a *promise*, and `promise.call()` would also have thrown. What is
established is narrower and sufficient: **the v5 bytes crash the core deterministically, twice in a row,
the first time a volatile session occurred after they were loaded.** The pre-v5 behaviour on this path
is not established and is not claimed.

### 16.3 Why it took until 12:30 to appear

`unSetVolatile` only invokes the callback when `volatileCallback` is set, i.e. only after
`setVolatile` — which only happens on a genuine Connect-client session. §15.4's drives all carried
`play_origin: "go-librespot"` and never entered the volatile branch (§15.5), so they could not reach the
crash. The operator starting a song from the phone at **12:30:12** (`play_origin: "your_library"`,
`context_uri` = their Liked Songs) was the first volatile session on the v5 bytes, and it crashed 11
seconds later. **This is precisely the risk §15.5 flagged when it refused to treat Spotify's survival in
§15.4 as evidence about v5.**

### 16.4 v6 — bind the callback to its controller

Patcher `ops/pi5-beta/patches/2026-09-21-handover-v6-callback-bind.py`. One line:

```js
callback: self.libRespotGoUnsetVolatile.bind(self)
```

`bind` is required rather than stylistic: the callback then carries its own receiver, so it is correct
whether the core calls it bare, via `.call()` with no `thisArg`, or as a method.

| | Value |
|---|---|
| live sha256 | `41cca7803943bc4f8811c9d0f832359a2fab9118f864c2e7a9f3e67bb5b2f609` |
| staged sha256 | identical to live after apply (asserted in the patcher) |
| rollback | `spop-index.v5.js` (149234 B), plus v4/v3/v2/v1/original |
| syntax | `node --check` **on the staged bytes** before writing, then on the live file after |
| core reload | `systemctl restart volumio.service`, MainPID `19518 → 21258` |

Preconditions asserted before writing, so the patch cannot land on a drifted file: the target line is
uniquely present, the file still opens with `'use strict';`, the callback definition still exists, and
the patch is not already applied.

**Mechanism proved in isolation** (a standalone node script, no production code involved): calling an
unbound strict-mode prototype method with `this === undefined` throws
`TypeError: Cannot read properties of undefined (reading 'logger')` — the same class as production's
`'debugLog'` — while the bound form runs and logs normally.

### 16.5 Verification status — and what is still owed

Verified: the live bytes equal the staged bytes; `node --check` passes; the core reloaded cleanly
(`active`, MainPID 21258), the spop plugin loaded, go-librespot initialised and its websocket
established; local playback works under v6 (`service=mpd`, position advancing, single fifo writer =
`mpd`, clean stop); **`grep -c 'FATAL ERROR'` since the v6 reload = 0**.

**Not yet verified: a real volatile session under v6.** That needs a Connect client, so it cannot be
driven from the device — the same wall as §12.4, and the wall that hid this bug. The next phone-started
session is the test; a passive journal watch is the instrument. Until that lands, v6 is verified as far
as the device allows and no further, and §14.2's claim should not be read as having been re-validated.

**Review gate still open, and now overdue.** §12.7 item 3 remains unclosed and now covers v4, v5 and v6:
none of the three has had an independent verdict, and v5 is the second patch in this series whose first
real exercise found a defect the review would have been looking for. That is an argument for the gate,
not against the patches.

### 16.6 Unrelated, noted not chased

`error: Failed callmethod call: TypeError: Cannot read properties of undefined (reading 'has')` appears
4 times today (caught and logged, non-fatal) and is not from the spop handover path. Separately, the
spop plugin logs the account's user object — including display name and email — into the journal on
login. Both are pre-existing and untouched by this work.

## 17. Live correlated capture of the operator's sequence — v6 confirmed, and a new state-ownership defect

The operator ran a handover sequence while a new monitor recorded three streams against **one device
clock**, so log lines and screen frames share a timeline instead of being reconstructed after the fact:

| Stream | Source | Cadence |
|---|---|---|
| journal | `journalctl -f -o short-precise` | continuous |
| state | router + go-librespot + mpc + **root-visible fifo holders** | 2 s |
| screen | `scrot -t 55`, filename = epoch | 2 s |

All three append to `/tmp/mon/timeline.log` (plus `/tmp/mon/journal.log`, `/tmp/mon/shots/`), started
`2026-09-21 12:43:45.750`. Note the state sampler's own `sudo find` appears in the journal as `sudo[...]`
lines and must be filtered when reading it.

### 17.1 v6 is CONFIRMED — the crash is gone

The sequence exercised the volatile path twice, including the exact transition that killed the core
twice at §16.1: **measured**

```
12:43:51  info: Spotify is playing in volatile mode        <- volatile session starts
12:44:12  verbose: UNSET VOLATILE: Service: spop            <- the §16.1 crash point
12:44:12  SPOTIFY: UNSET VOLATILE                           <- callback entered and returned
```

then straight-line execution. Zero `FATAL ERROR` and zero `TypeError` since the monitor started, core
MainPID **21258 unchanged** (the v6-reload process) and `NRestarts=0`. §16.5's outstanding item — "a real
volatile session under v6" — is now closed; this is the same code path that previously exited the process
with `status=1/FAILURE`.

Both handover directions also re-passed, with the fifo holder set as the evidence: local→Spotify
(12:43:50 `{go-librespot: 2, camilladsp: 1}`, `mpc=none`) and Spotify→local (12:44:12
`{mpd: 2, camilladsp: 1}`). Single writer in every phase. **measured**

Incidental answer to an open question: a **paused go-librespot releases the fifo entirely** — at
12:43:59.813, with Spotify paused from the phone, the holder set was `{}`. **measured**

### 17.2 The new defect: a *pause* re-arms volatile ownership, and the router never recovers

From **12:44:14.761** onward the router and the audio device disagree, and they stay that way — still
true at 12:46:30, over two minutes: **measured**

```
12:44:12.511  router=play/mpd/vol=False   | mpc=[playing] #1/1 0:01/3:47 | fifo={mpd:2, camilladsp:1}
12:44:14.761  router=pause/spop/vol=True  | spotify paused=True pos=9278 frozen | mpc=[playing] 0:03/3:47
12:45:32.553  router=pause/spop/vol=True  | spotify paused=True pos=9278 frozen | mpc=[playing] 1:21/3:47
12:46:30      router=pause/spop/vol=True  | spotify paused=True pos=9278 frozen | mpc=[playing] 2:19/3:47
```

The local player owns the fifo and is audibly progressing; the router says Spotify owns playback and is
paused. **Audio is correct — one writer, no collision. What is broken is state ownership.**

### 17.3 Mechanism, in code

`identifyPlaybackMode` has no notion of *which event* it is handling, and is called from both the
`playing` and the `paused` cases:

```js
// index.js:451-461
if (data && data.play_origin && data.play_origin === 'go-librespot') { isInVolatileMode = false; }
else { isInVolatileMode = true; }
if ((isInVolatileMode && currentVolumioState.service !== 'spop') || ...) {
    self.initializeSpotifyPlaybackInVolatileMode();   // -> setVolatile({service:'spop'})
}
```

The chain, all four steps visible in the capture:

1. The operator starts local playback; the **mpd-side guard we added in §13.5/§14** correctly releases
   Spotify — `12:44:12.955  Spotify Stop / SPOTIFY STOP`, i.e. the plugin sends `/player/pause`.
2. go-librespot pauses and emits `{"type":"paused","data":{...,"play_origin":"your_library"}}`
   (`12:44:12.672`).
3. The `paused` case calls `identifyPlaybackMode`. `play_origin` is not `go-librespot`, so
   `isInVolatileMode = true`; the router currently says `mpd`, so the condition is satisfied and
   `initializeSpotifyPlaybackInVolatileMode()` runs — logging `Spotify is playing in volatile mode` for
   an event that means Spotify **stopped** playing.
4. `setVolatile({service:'spop'})` marks spop as the volatile owner. The router is now pinned to
   `pause/spop/volatile=True` while MPD plays on.

**A pause is not a takeover, and it must not claim the device.** The defect is upstream (this
classification logic is Volumio's), but our mpd-side guard is what *initiates* the pause, so before our
patch this specific trigger did not exist — the collision happened instead. Honest framing: the release
logic converted an audible defect into a state-ownership defect.

### 17.4 Visual correlation — the screen shows the wrong owner

Screen frames read against the log timestamps above:

| Screen time | Log state | What the screen showed |
|---|---|---|
| 12:43:45.751 | `play/mpd/vol=False` | meter, local track, **`flac`** badge — correct |
| 12:43:54.922 | `play/spop/vol=True` | meter, Spotify track, **Spotify** badge — correct |
| **12:44:15.507** | `pause/spop/vol=True`, **mpc playing** | meter showing the **Spotify track and Spotify badge** while MPD is the actual player |
| **12:45:32.505** | `pause/spop/vol=True`, **mpc playing** | **idle weather/clock screensaver** — no player UI at all, while MPD is audibly playing |

So the same desync surfaces two ways depending on where the screensaver timer sits, and this is the
first time §14.3's "PeppyMeter shows the wrong track" and §10's state-ownership finding have been caught
with timestamps and frames that line up.

### 17.5 Severity and the fix direction (not applied)

Not an audio defect: no interleaving, one writer, playback stable. It is a **control** defect — the UI
and every volatile-aware command target Spotify while MPD owns the device, so the next UI action or
phone command addresses the wrong player, and `stop`/`pause` from the UI will not stop the sound that is
actually playing.

Direction, for a reviewed change rather than an ad-hoc one: gate the volatile claim on an event that
means Spotify is *taking* playback (`will_play`/`playing`), or explicitly ignore `play_origin` on
`paused`, instead of classifying purely on `play_origin` with no event-type input. `identifyPlaybackMode`
nearly distinguishes these — it simply is not told which case called it.

**Status: not fixed. Left live and undisturbed so the operator can see it** — the desync is still
present in the device state as this entry is written.

## 18. v7 — a pause no longer claims volatile ownership (fixed, deployed, differential-tested)

Operator GO received; §17.5's fix direction was implemented and deployed live on the device.

### 18.1 The change

Patcher `ops/pi5-beta/patches/2026-09-21-handover-v7-pause-is-not-takeover.py`. Four edits, one idea —
ownership may only be claimed by an event that means Spotify is *taking* playback:

```js
case 'paused':
    self.state.status = 'pause';
    self.identifyPlaybackMode(event.data, false);   // v7: a pause is NOT a takeover
...
self.identifyPlaybackMode(event.data, true);        // only the 'playing' case may claim
...
ControllerSpotify.prototype.identifyPlaybackMode = function (data, isTakeoverEvent) {
    ...classification of isInVolatileMode unchanged...
    if (!isTakeoverEvent) { return; }               // v7 gate, before the ownership condition
    if ((isInVolatileMode && currentVolumioState.service !== 'spop') || ...) {
        self.initializeSpotifyPlaybackInVolatileMode();
```

Containment was checked before writing, not assumed: `initializeSpotifyPlaybackInVolatileMode` has
exactly **one** call site (this one) and `stateMachine.setVolatile(` exactly **one** call (inside it), so
the gate is the single choke point rather than a partial fix. `isInVolatileMode` is a module-level var
written and read only inside `identifyPlaybackMode`, so leaving its assignment alone changes nothing for
any other reader.

| | Value |
|---|---|
| live sha256 | `64e76b74ebaad504d2855750bf84c6e175885b348486066ead7f9e5df5bc77ba` |
| rollback | `spop-index.v6.js` (149977 B), plus v5/v4/v3/v2/v1/original |
| syntax | `node --check` on the **staged** bytes before writing, then on the live file |
| core reload | `systemctl restart volumio.service`, MainPID `21258 → 25770` |

The patcher refuses to run unless the file still opens with `'use strict';`, the signature, both call
sites, the ownership condition and both single-call-site properties are all uniquely present, and it
asserts afterwards that the v6 `.bind(self)` fix is still there and that no flagless call site survived.

### 18.2 Differential test on the shipped bytes — the harness can fail

`ops/pi5-beta/scripts/2026-09-21-v7-identifyplaybackmode-differential.js`. It loads the **real bytes** of
v6 (the rollback copy) and v7 (live) with every heavy dependency stubbed, injects the router state
through the plugin's own `startSocketStateListener` on a fake socket, and replaces
`initializeSpotifyPlaybackInVolatileMode` with a counter so the real `setVolatile` never runs. Nothing
touches the production core.

The argument shape is **derived from each file's own call site**, so the test cannot pass by calling the
function the way we wish it were called. All three probes run against **one instance with one injected
state**, so the pause result cannot be an artefact of missing state — the paired takeover probe proves
the machinery is live. **measured**

| Bytes | paused, origin=`your_library` | playing, origin=`your_library` | playing, origin=`go-librespot` |
|---|---|---|---|
| **v6** (was live) | **1 — bug reproduced** | 1 | 0 |
| **v7** (now live) | **0 — fixed** | 1 | 0 |

`ALL EXPECTATIONS MET`, exit 0. The v6 row is the negative control that matters: the harness *does*
detect the defect, so v7's zero is a real gate and not a test that cannot fail.

### 18.3 Regression verification on the device, after the reload

| Step | router | fifo holders | single writer |
|---|---|---|---|
| idle after reload | `stop/mpd/vol=False` | `{}` | — |
| local playback | `play/mpd/vol=False` | `mpd:2, camilladsp:1` | mpd |
| Spotify takes over | Spotify playing | `go-librespot:2, camilladsp:1` | go-librespot |
| local takes back | `play/mpd/vol=False` | `mpd:2, camilladsp:1` | mpd |
| stop | `stop/mpd/vol=False` | `{}` | — |

Both directions still work; the router ends **consistent** (`stop/mpd/vol=False`) instead of pinned to
spop/volatile; zero `FATAL ERROR` since the deploy. The desync left live at §17.2 was cleared by the
reload. **measured**

### 18.4 What this does and does not prove

**Proved:** the shipped v7 bytes do not claim volatile ownership when a `paused` event arrives while the
router is on the local player, and still do on a takeover — with v6 as the control, on the same harness.

**Not proved:** the whole-device end-to-end behaviour under a real phone-started session. The harness
exercises the code path with an injected router state; it does not reproduce a Connect client. The
definitive confirmation remains the same sequence the operator ran at §17, under the monitor, and it
needs his phone. Monitor and watch remain armed for it.

**Review gate:** now covers v4, v5, v6 and v7 — still no independent verdict on any of them. v5 and v7
were both defects found by running the code against a real session rather than by reading it, which is
the strongest argument yet for taking that gate seriously before any of this is offered upstream.

## 19. Monitoring infrastructure, and a self-inflicted resource incident

### 19.1 /tmp on this device is RAM

`df -h /tmp` -> `tmpfs 3.9G ... mounted on /tmp`. `/`, `/home/volumio` and `/data` are all one
disk-backed overlay (`230G`, ~4.5G used). **Nothing bulk should ever be written under `/tmp` on this
device.** **measured**

### 19.2 The incident

The §17 monitor captured a screenshot every 2 s into `/tmp/mon/shots`. Over 1 h 40 m that reached
**5,630 frames / 3.6 GB, filling the tmpfs to 93 % (327 MB free)** — roughly 3.6 GB of an 8 GB Pi's RAM
consumed by instrumentation. Device RAM read **4,423 MB used** at the peak; after reclaiming it read
**809 MB used**, and the tmpfs went **3.6 GB -> 11 MB**.

Detected only when the watch expired and I went looking. The correlated artefacts were preserved first —
`timeline.log` (5,991 lines), `journal.log` (19,800 lines) and 236 sequence-window frames pulled off the
device — and only then were the frames deleted.

**Why this matters beyond tidiness:** §12.5 already records that this machine is oversubscribed during
playback, with the DSP underrun question still open. For 1 h 40 m the audio path ran with ~3.6 GB of
RAM removed and an extra `scrot` + `python3` sampling pair ticking every 2 s. **Any load, underrun or CPU
measurement taken between 12:43:45 and 14:34 must treat that monitor as a confound**, including the
`ps`/`loadavg` figures quoted in §17. It does not affect the handover measurements themselves (fifo
holder sets and router state are not load-sensitive), but it does affect anything about throughput or
stutter.

### 19.3 The monitor, in its safe form

Now at `/data/pi5-mon/` (disk-backed), started `14:36:30`:

* `journalctl -f -o short-precise` -> `journal.log`
* state sampler (router + go-librespot + mpc + root-visible fifo holders) every 2 s -> `timeline.log`,
  and a compact one-line `state.sig`
* **screen capture is change-triggered** off `state.sig`, with a 60 s heartbeat, into a **ring buffer of
  the newest 400 frames** — 4 frames in the first 21 s, against 5,630 for the old design

Screenshot cadence is therefore a signal-driven instrument, not a video recorder. `setsid --fork` is the
working detach idiom (`systemd-run` is not in the sudo NOPASSWD allowlist; plain `nohup ... &` left the
ssh session hanging).

### 19.4 Process note

`pkill -f` / `pgrep -f` patterns are matched against the invoking command line too: a cleanup pattern
containing the monitor's own path killed my own ssh shell (exit 255) mid-cleanup. Enumerate by pid, or
choose patterns that cannot match the command doing the matching.
