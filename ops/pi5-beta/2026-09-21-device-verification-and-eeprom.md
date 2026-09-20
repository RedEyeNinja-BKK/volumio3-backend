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

**Not yet isolated:** whether Spotify plays cleanly *alone*. That experiment separates the two
candidates and needs the same phone-side action. It is the cheapest next step.

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
