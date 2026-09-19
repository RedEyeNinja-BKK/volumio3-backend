# 2026-09-19 — I2S DAC remediation plan (narrow scope, recoverable)

Companion to `2026-09-19-i2s-dac-control-plane-rootcause.md`. That record establishes
the diagnosis; this one is the plan. **Nothing in this plan has been executed yet.**

## Safety contract

Applies to every experiment below.

1. **Every touched file is backed up byte-exactly first**, with its sha256 recorded here,
   and a second copy is placed on the FAT boot partition so it is readable from a PC with
   the card removed — i.e. a bad boot configuration is recoverable without the device
   booting at all.
2. **One variable at a time** whenever attribution matters. Where the diagnosis has
   already settled a question, steps may share a single reboot (noted per experiment).
3. **Every step has an explicit rollback** and a reboot-level recovery.
4. **`/data/configuration/**` (the live user configuration) is not edited by hand.** Only
   UI-exposed settings are changed, through Volumio's own paths.
5. **No step is left half-applied.** Each ends either verified or rolled back.
6. **Evidence is captured by the same instrument every time** —
   `scripts/i2s-diag.sh`, read-only, run before and after. Its output is the record.

### Pre-staged backups (done)

| file | sha256 of the pre-change state |
|---|---|
| `/boot/config.txt` | `8f448dd300422b465b456616eb5870173538640d167c8330856a394247687ecd` |
| `/boot/userconfig.txt` | `02f8b9a56680a38820de9fbebe03d3fbe20039a7eed6a3ee9d5118ad13425f54` |

Copies exist on the device at, respectively:

- `/home/<user>/dac-experiments/config.txt.before-20260919`
- `/home/<user>/dac-experiments/userconfig.txt.before-20260919`
- `/boot/config.txt.volumio-bak-20260919` (FAT partition, PC-readable)
- `/boot/userconfig.txt.volumio-bak-20260919` (FAT partition, PC-readable)

Both device copies were hash-verified byte-identical to the live files at backup time.

### Risk notes

- The device is supplied over PoE and is headless apart from its DSI strip. A boot
  configuration that fails to boot is recovered **off-device** from the FAT-partition
  copies above.
- The boot partition has ample free space; the backups are under 2 kB.
- `dtoverlay`, `i2cset`, `tee`, `cp` and `chmod` are all in the device's passwordless
  `sudo` allowlist, so no interactive privilege escalation is needed.

---

## E1 — Deleted-client probe: does a chip actually answer at 0x48?

**Question it settles.** The single strongest open question. `0x48` is claimed by the
kernel driver, so a userspace probe returns `EBUSY`. Remove the client and probe the raw
address.

**Why it matters.** If no chip answers, the HAT is control-less, native DSD is impossible
on this board, and the profile swap (E2) is definitively correct. If a chip *does* answer,
the address/wiring is the real problem and the sabre profile becomes worth pursuing.

**Method**

1. Stop playback; stop FusionDSP's engine (CamillaDSP holds the ALSA device, and it will
   error when the card disappears).
2. Run `scripts/i2s-diag.sh` → before-snapshot.
3. `echo 1-0048 | sudo tee /sys/bus/i2c/devices/i2c-1/delete_device`
4. Probe `0x48` directly on `/dev/i2c-1` with a 1-byte read (`I2C_SLAVE` + `read`).
   The invoking user is already in the `i2c` group and `/dev/i2c-1` is `0660 root:i2c`,
   so no privilege is required for this part.
5. Record the errno for `0x48` **and** for a known-dead control address (e.g. `0x77`):
   a valid ACK at `0x48` versus `EREMOTEIO` at `0x77` proves a chip is present.

**Expected outcomes**

| result at `0x48` | meaning | next |
|---|---|---|
| `EREMOTEIO` (NAK) | no chip answering — control-less board | E2 is the fix; native DSD is off the table |
| valid ACK | a chip exists but is mis-addressed or mis-wired | revisit the profile/overlay; E2 still removes the failed writes but is no longer the whole answer |

**Rollback / recovery.** `echo i-sabre-codec 0x48 | sudo tee .../i2c-1/new_device` to
recreate the client; if the driver does not rebind, **reboot** — the device-tree overlay
recreates the client unconditionally. Tearing down the codec removes ALSA card 1 and may
stop CamillaDSP or error MPD for the duration; both are restored by the reboot.

**Cost.** One disruption + one reboot. No configuration change; nothing persists.

---

## E2 — Correct the boot configuration (and, if E1 says so, the profile)

**Change.** Two parts, combined into one reboot when E1 has already settled the I2C
question (otherwise split, to keep attribution).

*Part 1 — hygiene (unconditional, in scope either way):*

- remove the **duplicate** `#### Volumio i2s setting below ####` + `dtoverlay=i-sabre-q2m`
  block from `/boot/config.txt` (keep exactly one),
- remove `dtoverlay=i2s-dac` from `/boot/userconfig.txt` — it is a second, conflicting
  I2S DAC overlay, both fragments target `&sound`,
- remove `param=i2s_mclk=on` from `/boot/userconfig.txt` — not a valid directive; it is
  silently ignored and only misleads future readers,
- keep `dtparam=audio=off`, the DSI panel overlay, the fan overlay, `dtparam=i2c=on`.

*Part 2 — profile (only if E1 shows no chip answering):*

- Volumio → Playback Options → *I2S DAC Model* → **"Generic I2S DAC"**
  (`overlay: hifiberry-dac`). From the overlay source this declares `ti,pcm5102a`, contains
  **no I2C node at all**, and targets the same **`i2s_clk_producer`** — so the I2S clock
  topology is unchanged. It is the correct match for a control-less I2S DAC, and it
  **already ships with Volumio**; nothing needs to be requested upstream.

*Part 3 — verify Volumio's own write did not duplicate the block.* Volumio's dedup is a
regex built from the banner line and has failed here before; confirm `/boot/config.txt`
ends with **exactly one** banner block.

**Verification (after reboot)**

- `dmesg | grep -c 'i-sabre-codec-i2c'` — expect **0** (vs 109 in 27 min before).
- no `Audiophonics Device ID : FFFFFF87` line (that line *is* a failed read).
- `aplay -l` shows the expected card name — `DAC` (sabre) or `sndrpihifiberry` (generic).
- `ls /sys/bus/i2c/devices/` — no `1-0048` client under the generic profile.
- play the 192 kHz file and confirm `hw_params` negotiates 192000.
- if the sabre profile was kept and I2C came alive, **test a DSD file** — 352800/384000
  is where the previously-unwritable register matters.

**Rollback.** Restore the two backed-up files and reboot; or, for the profile only,
re-select "Audiophonics I-Sabre ES9028Q2M" in the dropdown and reboot.

**Cost.** One reboot.

---

## E3 — Is FusionDSP's engine the cause of the "unreliable" 192 kHz?

**Hypothesis.** The reported unreliability is stutter/glitch caused by the inline
CamillaDSP userspace-FIFO round trip (`queuelimit: 1`, `enable_rate_adjust: true`), whose
DSP load scales with sample rate — not by the DAC.

**Method.** No reboot. Disable FusionDSP, then listen to the same 192 kHz file that was
used as the reference, then re-enable it and listen again. Same file, same volume, same
seat.

**Why this needs ears.** There is no capture path from the DAC's analog output, and
CamillaDSP holds the ALSA device open, so the chain cannot be bypassed for measurement.
This test is below the software's visibility by construction.

**Outcome.** If 192 kHz is clean with FusionDSP off, the engine is the cause and the
remedy is to run the DSP at a lower internal rate (or accept it only at ≤ 96 kHz).

**Rollback.** Re-enable FusionDSP in the UI.

---

## E4 — Make DSD play by capping the pipeline at 192 kHz

**Change.** No reboot. Playback Options → enable Resampling, target **192000**, quality
"very high".

**Effect.** MPD converts DSD to PCM at DSD-rate ÷ 8 — measured **DSD64 → 352.8 kHz,
DSD128 → 384 kHz, DSD256 → 192 kHz**. Only DSD256 currently lands in the regime that
works. Capping at 192 kHz brings DSD64 and DSD128 down into the measured-clean regime,
so DSD *plays* instead of requesting the high-rate mode the chip can never be told about.

**Tradeoff, stated plainly.** DSD is decimated — ~1.84× for DSD64, 2× for DSD128. This is
a real fidelity loss. But DSD → PCM conversion is *already* happening (`dop "no"`), so
this changes the degree, not the kind. The alternative, if the chip has no I2C, is no DSD
at all. Native DSD would need a HAT with a working control plane.

**Rollback.** Disable resampling.

---

## Deliberately out of scope

- **DSD over PCM (DoP).** `dop "yes"` would carry DSD64 as 176.4 kHz PCM, but DSD128 would
  become 352.8 kHz again, and DoP detection is itself a chip configuration. Worth trying
  only after E1/E2, and only if E4 proves insufficient.
- **Filing anything upstream.** The duplicate `dtoverlay=` block and the empty
  `hotRemoveI2SDAC` stub are legitimately reportable Volumio bugs, but filing needs a
  separate explicit decision. Nothing is filed.
- **Changing `/data/configuration/**` by hand**, or the Now Playing / kiosk work from the
  other 2026-09-19 records — unrelated.

## GO gates

| # | action | reboot | reversibility | status |
|---|---|---|---|---|
| E1 | probe `0x48` with the client removed | yes (recovery) | nothing persists | awaiting GO |
| E2p1 | boot-config hygiene | yes | byte-exact backup | awaiting GO |
| E2p2 | switch profile to "Generic I2S DAC" | with E2p1 | dropdown + backup | contingent on E1 |
| E3 | FusionDSP off, listen, on | no | UI toggle | awaiting GO |
| E4 | resampling to 192 kHz | no | UI toggle | awaiting GO |
