# 2026-09-20 - Bootup config review, OS package update, and the runtime I²S wedge

**Status: complete. OS layer updated + verified; app layer was already current; EEPROM deliberately not touched (operator decision pending). One operator action recommended: a cold start to activate the upgraded in-memory binaries.**

Provenance labels per `2026-09-19-i2s-dac-regression-and-revert.md`: **measured** = we measured it, **observed** = operator reported/confirmed, **claimed** = third-party.

## 1. Bootup config review (read-only)

Boot chain: NVMe → squashfs base + overlay rootfs (ext4 dynamic layer), kernel 6.12.75-v8+
(Sep-14 image build), armhf userspace (`arm_64bit=0`, intentional), bootloader EEPROM Dec-2025 release.

Findings:

| item | status | note |
|---|---|---|
| `dtoverlay=i-sabre-q2m` (config.txt, ×2 duplicate banner) | accepted | vendor-prescribed profile for the R19 board; duplication is the known upstream dedup bug (append-on-miss). Harmless while stable; do NOT "fix" by hand — any future Volumio config regen may re-add it |
| `dtoverlay=i2s-dac` + `param=i2s_mclk=on` in userconfig.txt | accepted as-is | conflicts with i-sabre-q2m (both target &sound); sabre wins at boot; `i2s_mclk` inert per overlays README. E2's removal of these was part of the reverted hifiberry-dac experiment — do not re-apply piecemeal |
| `dtparam=audio=on` (volumioconfig) vs `=off` (userconfig) | OK | userconfig includes last → internal audio off, as intended |
| Waveshare 8" DSI overlay + rotation | OK | kiosk renders (Xorg+Chromium alive) |
| TURBO OC (arm 2800 / core-gpu-v3d 1100 / sdram 2400 / over_voltage=6) | OK under PoE | measured: throttled=0x0, 0 UV events; temp 56 °C at load ~1.5, **70.3 °C** during the update run — fan curve (60 °C/180, 70 °C/255) should engage; worth a glance at whether the fan physically spins |
| `ifup@eth0.service` failed at boot | cosmetic | known single-network-era artefact; multi-network mode active (SINGLE_NETWORK_MODE=false), wlan0 holds default route |
| `hciconfig hci0 up` fails | cosmetic | Bluetooth unused |
| kiosk drop-ins 10-restart / 20-blank-on-stop | intact | verified present + kiosk active |

**Alignment verdict:** the bootup config is internally consistent and healthy; no changes made.

## 2. The "mysterious revert" was a misread — and the runtime I²S wedge

This session's first pass (before retrieving `2026-09-19-i2s-dac-regression-and-revert.md` from
the fork) reported E2 as "reverted by unknown actor." **Correction: it was an intentional,
documented byte-exact revert** — the hifiberry-dac switch caused silence (machine-driver framing
mismatch), and the live file hashes match that record's claimed values exactly. Lesson restated:
the fork is the authoritative source; memory snapshots lag it.

New finding this session (**observed** by operator + **measured** on Pi):

- Operator reported: sound played for a while after the Sep-19 cold start, then stopped again.
- Pi side fully exonerated during the silence: FusionDSP fifo carried real audio (peak 0.0 dBFS,
  RMS −9.9 dBFS), card 1 PCM open at valid hw_params, **0 XRUNs**, throttled=0x0.
- A full cold start (operator-initiated) restored sound immediately.

**Conclusion: the I²S link wedges at runtime and only a power cycle clears it.** Consistent with
the May-2025 measurements recorded in the revert record (R19 on-board MCU re-configures MCLK on
rate change → DO400 fails to re-lock). The wedge is board/DAC-side; nothing in the Pi's config or
pipeline caused it. Open hardware questions unchanged: R19 J2/J3 (MCLK) + J4/J5 (interface mode)
positions vs the DO400 `I²S MODE` menu — neither visible from the Pi; use the board's LOCK/DSD
LEDs to bisect if the wedge recurs often.

## 3. OS package update (operator GO given for the full sequence)

Pre-flight facts:

- **App layer already current:** installed `/volumio` HEAD `0a52c1f` == upstream master HEAD
  (Sep 11). Zero commits behind. OTA updater runs in **Test mode** (would not pull stable anyway).
  `pull.sh` is a dev tool (re-clones, keeps stale node_modules) — NOT the production update path.
- Therefore "update to latest" = OS packages only (136 upgradable incl. security: openssl
  3.0.20, openssh +deb12u10, sudo; kiosk's Chromium 143→153) + optionally the bootloader EEPROM.

Backups staged first at `/home/volumio/update-backups-20260920/` (tarball + sha256 manifest):
`.env`, `config.txt`, `userconfig.txt`, `volumioconfig.txt`, `cmdline.txt`, `asound.conf`,
i2s_dacs + alsa_controller configs, `camilladsp.yml`.

### The failure and its repair

`apt-get -y upgrade` aborted at package 14/78:

```
dpkg: error processing archive libpython3.11-stdlib_3.11.2-6+deb12u8_armhf.deb (--unpack):
 trying to overwrite '/usr/lib/python3.11/EXTERNALLY-MANAGED', which is also in package
 raspberrypi-sys-mods (20250930~bookworm)
```

**Root cause:** the Debian PEP 668 marker file is owned by BOTH `raspberrypi-sys-mods` (Raspberry
Pi's copy, since Sep-2025) and the new `libpython3.11-stdlib`. dpkg refuses to let one overwrite
the other → transaction aborted with ~40 packages unpacked-but-unconfigured. Known
Raspberry-Pi-vs-Debian packaging clash.

**Repair (three steps, all rc=0 after step 2):**
1. Back up both marker versions (contents differ only in wording; both are PEP 668 notices).
2. Remove the live file + drop its line from `raspberrypi-sys-mods.list` (backed up first) —
   this phantom db claim is what kept blocking re-install even after the file was removed.
3. `dpkg -i libpython3.11-stdlib...deb` → `dpkg --configure -a` → `apt-get -f -y upgrade`.

Result: **78 upgraded, 0 failed; dpkg audit clean; marker now owned solely by
libpython3.11-stdlib.** (First repair attempt failed on a missing `-y`; second attempt failed only
because the .list phantom claim survived — both superseded.)

### Post-update verification (all PASS)

| check | result |
|---|---|
| dpkg --audit | clean |
| remaining upgradable | `rpi-eeprom` only (28.13→28.31) — deliberately not touched; no pin exists, it was simply held back by the transaction |
| key versions | chromium 153.0.8010.47, libssl3 3.0.20-1~deb12u2, openssh 9.2p1-2+deb12u10, sudo 1.9.13p3-1+deb12u4, python3.11 deb12u8 |
| services | volumio, mpd, fusiondsp, volumio-kiosk, wireless, ssh all active; only failed unit = cosmetic `ifup@eth0` |
| protected files | **all 8 manifest hashes OK** incl. `.env` (SINGLE_NETWORK_MODE=false survived byte-identical — the update did not regenerate it) and asound.conf |
| web apps | port 3000 → 200, port 4004 → 200 |
| audio post-upgrade | card 1 opens at native 96 kHz for a FLAC; fifo peak −14.7 dBFS / RMS −30.1 dBFS (real signal, not silence); 0 XRUNs; operator hearing confirmed pre-update after cold start |

### Operator actions during the window

- **PeppyMeter disabled** in the UI (`peppy_screensaver enabled:false / STOPPED`) as a test
  while audio was wedged. Its ALSA hooks (meter/dummy paths) are still generated into
  `/etc/asound.conf` by the plugin when enabled; re-enabling regenerates them. Left as-is per
  operator intent — decide after the cold start whether to re-enable and watch the screensaver
  render (first post-3.4.5 idle render was never visually verified).

## 4. State + recommended next actions

1. **Cold start when convenient** (operator action, via Volumio UI power-off → physical power
   break → restore; remember software power-off is a halt, not a power cut). Purpose: activate
   the upgraded systemd/openssh/Xorg binaries currently still running from pre-update memory
   (process start times predate the upgrade), and re-establish the I²S link. Expectation: same
   healthy boot as this morning + sound working.
2. **After the cold start:** verify throttled=0x0, card 1 name `DAC`, 0 XRUNs, kiosk renders on
   Chromium 153, and re-enable PeppyMeter if wanted (watch first screensaver render).
3. **Bootloader EEPROM (28.13 → 28.31): operator decision pending.** Not done — per the approved
   plan ("don't touch a working boot chain") and because `rpi-eeprom-update` is masked by design.
   If GO: it's a small, reversible flash with a known-good fallback in the same EEPROM region;
   do it AFTER the cold start so any bootloader issue is trivially attributable.
4. **I²S wedge (board-side):** if "plays then stops" recurs before the next power cycle, the
   diagnostic is physical — R19 LOCK LED while playing + DO400 `I²S MODE` menu setting. See the
   revert record's measured MCLK-mode matrix and its TRUST CORRECTION (vendor jumper tables are
   unverified; sweep empirically with the LEDs).
5. **E3/E4 (192 kHz clean? DSD as PCM?)** remain open per the original plan — ears + DoP tests,
   now that the Pi side is proven healthy through the update.

## Artifacts on device

- `/home/volumio/update-backups-20260920/` — pre-update backup set + manifest + both
  EXTERNALLY-MANAGED versions + `raspberrypi-sys-mods.list.bak` + apt/repair logs
- `/home/volumio/audible-probe.py` — read-only audibility probe (FusionDSP fifo RMS/peak dBFS +
  CamillaDSP GetSignalLevels; play→measure→pause). Note: it blocks on the FIFO open if nothing
  is queued — queue a track first or run under `timeout`.
