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
committed. Both patches were therefore applied on the strength of the live measurements in §8 alone.
The findings of attempt three, when they exist, belong in §12 — if §12 is absent from this record, the
review never returned and the §8 measurements remain the only supporting evidence. Two production
files on a working device carry code that no second party has yet read.

The defect class is a genuine Volumio behaviour (no
service is released when a different service starts, and for `syncState` a service `stop` is
indistinguishable from end-of-track), so the durable fix belongs upstream rather than only on this
device.
