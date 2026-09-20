# 2026-09-21 — pi5-beta: playback stability check, Spotify path finding, bootloader EEPROM update

Device: `volumio-pi5-beta` (Raspberry Pi 5, Volumio 4.204, aarch64 kernel / armhf userland, NVMe boot)

**Status: one action remains and it must be taken at the machine — a power cycle to activate the
flashed bootloader EEPROM (section 4). Nothing is broken; the device is running normally.**

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

## 3. Spotify simultaneous-play — NOT TESTED, blocked on an operator action

This case could not be exercised, and the reason is concrete rather than a matter of effort:

* The Spotify ALSA routes are disabled by configuration: the plugin setting `useSpotify` is **False**,
  which is why the generated ALSA config carries `spotify1_off` / `spotify2_off`. **measured**
* `go-librespot` is running and holds an authenticated session, but `POST /player/play` returns
  **HTTP 400**: `disable_autoplay` is `true` and there is no active playback context. The daemon
  needs a real Spotify client (phone or desktop app) to open a session; its local API has no
  load-a-URI endpoint, so there is no tool-side way to start Spotify playback. **measured**

**To test:** with local playback running, start playback from a Spotify app targeting the device, and
observe whether local playback survives and whether the journal shows ALSA contention.

Disclosure: a probe of the librespot `/events` endpoint (which expects a WebSocket upgrade) produced a
"WebSocket protocol violation" error and a "superfluous response.WriteHeader" warning in the journal.
**Those two log lines were caused by this diagnostic probe, not by a defect.**

## 4. Bootloader EEPROM — updated, awaiting one power cycle

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
Caveat: the config-backup file the same documentation mentions was **not** written by the immediate
path, so retention rests on that documented default and not on a backup artifact. Boot order matters
here because this device boots from NVMe; the retained `0xf614` includes the NVMe entry.

**ACTIVATION PENDING.** The EEPROM is written, but the running system still reports the previous
bootloader; the new image takes effect at the next power-on. The agent's shell environment blocks
restart and power commands by policy, so **this step has to be done at the machine.** The device is
stable in the meantime — an EEPROM that is flashed but not yet active carries no risk.

**Verify after the power cycle:**

```
vcgencmd bootloader_version        # expect 2026/05/26
vcgencmd bootloader_config         # expect BOOT_ORDER=0xf614 and the other keys above
```
plus: device boots from NVMe, network returns, display returns, and audio still plays.

**Rollback.** The package upgrade removed `pieeprom-2025-12-08.bin` — the previously running image is
no longer on the device, so there is no byte-exact rollback to the old bootloader. Rolling back means
flashing one of the images still present:

```
ls /usr/lib/firmware/raspberrypi/bootloader-2712/latest/*.bin
sudo rpi-eeprom-update -f <chosen image>
```

## 5. Unchanged by any of this

* The three files the PeppyMeter architecture fix touches (`index.js`, `install.sh`,
  `run_peppymeter.sh`) remain byte-identical to upstream 3.4.5 on this device. **measured**
* Overlays intact: `dtoverlay=hifiberry-dac`, the display panel overlay, and the active-fan overlay.

---

Nothing here is proposed upstream without a separate, explicit decision.
