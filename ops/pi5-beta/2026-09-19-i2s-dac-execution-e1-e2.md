> ## CORRECTION - 2026-09-19, later the same day
>
> **The central conclusion in this record is superseded, and the change it recommended caused
> a regression. Read `2026-09-19-i2s-dac-regression-and-revert.md` before using anything here.**
>
> The HAT is **not** a DAC. It is an **I²S output board**; the actual DAC is an external
> **SMSL DO400** fed over I²S. Therefore:
>
> - The dead I²C at `0x48` is fully explained - there was **never a DAC chip on the HAT**.
> - The inference drawn from it was wrong. The record reasoned "the driver is blind, so it is
>   doing nothing useful, so a codec-less profile is strictly better." The driver was not doing
>   nothing: the *machine driver* (`snd-rpi-i-sabre-q2m`) also configures the **I²S DAI -
>   framing, bit-clock ratio and clocking - none of which involves I²C.**
> - Replacing it with `hifiberry-dac`, whose machine driver frames the stream differently via
>   the `pcm5102a` dummy codec, produced **total silence on the DO400's I²S input.** Coax and
>   optical continued to work because they are fed from a different source.
>
> **Every measurement in this record is upstream of the failure it claims to explain.**
> `hw_params`, `hw_ptr`, CamillaDSP signal levels and the zero I²C error count all describe the
> Pi's side of the link; none observes whether the I²S frame arriving at the DO400 is decodable.
> "Verified" here meant "clean at the ALSA device", not "audible" - and that gap is how a
> regression came to be reported as a pass.
>
> Reverted byte-exact the same day.

> ## ⚠️ CORRECTION - 2026-09-19, later the same day
>
> **This record's central conclusion is WRONG and its recommendation caused a regression.
> Do not act on it.**
>
> The HAT is **not** a DAC. It is an **I²S output board**; the actual DAC is an external
> **SMSL DO400** fed over I²S. So:
>
> - But the inference drawn from it was wrong. I concluded "the driver is blind, therefore it
>   is doing nothing useful, therefore a codec-less profile is strictly better." **The driver
>   was not doing nothing:** the *machine driver* (`snd-rpi-i-sabre-q2m`) also configures the
>   **I²S DAI - framing, bit-clock ratio and clocking - and none of that involves I²C.**
>   optical still work because they are fed by a different source.
>
> `hw_ptr`, CamillaDSP signal levels and the zero I²C error count all describe the Pi's side
> of the link. None of them observes whether the I²S frame the DO400 receives is decodable.
> what let a regression be reported as a pass.
>

# 2026-09-19 — I2S DAC: E1 result and E2 applied

Execution record. Follows `2026-09-19-i2s-dac-control-plane-rootcause.md` and
`2026-09-19-i2s-dac-remediation-plan.md`.

**State: E1 complete, E2 applied and verified, awaiting one cold start to take effect.**

---

## E1 — Deleted-client probe: RESULT = there is no chip at 0x48

**How the `EBUSY` wall was cleared.** `delete_device` is unusable for this client: the
kernel returns `-ENOENT` (`delete_device: Can't find device in list`) for this
device-tree-created client, and tee's message for that errno reads
`No such file or directory`, which is misleading — it is the *write*'s errno, not a
missing path. The lever that works is **unbinding the i2c driver**:

```sh
printf '1-0048\n' | sudo tee /sys/bus/i2c/drivers/i-sabre-codec-i2c/unbind
```

That tears down ALSA card 1 (confirmed gone) and releases the address, after which a
plain `I2C_SLAVE` + `read` on `/dev/i2c-1` performs a real bus transaction.

**Result:**

| address | role | 1-byte read |
|---|---|---|
| `0x48` | the codec address | **`EREMOTEIO`** |
| `0x30` | control | `EREMOTEIO` |
| `0x77` | control | `EREMOTEIO` |

The controls behaving *identically* to `0x48` is what makes this conclusive: the probe
discriminates, and `0x48` is not "present but mumbling" — nothing answers.

Combined with the earlier topology scan (nothing else responds on bus 1, no HAT EEPROM
at `0x50`) and the absence of any controller-level error in `dmesg`, this settles it:

> **The HAT is a control-less I2S DAC board. It has no I2C-controllable chip.**

Consequences: the "Generic I2S DAC" profile is definitively correct, and **native DSD is
not achievable on this board** — an ES9028Q2M cannot be put into DSD mode over I2S
without register writes. The realistic ceiling is DSD decoded to PCM (plan experiment E4).

The driver was rebound afterwards and card 1 came back cleanly.

## E2 — Boot configuration and profile: APPLIED

### Changes, with before/after sha256

| file | before | after |
|---|---|---|
| `/boot/config.txt` | `8f448dd3…87ecd` | `9ce4390d…7ca078` |
| `/boot/userconfig.txt` | `02f8b9a5…25f54` | `2cf28b6c…0ec473` |
| `/etc/asound.conf` | `e1466cfa…2d475` | `a297a504…66f2b` |
| `…/system_controller/i2s_dacs/config.json` | `88e2c786…46441` | `1b54c836…9d530` |
| `…/audio_interface/alsa_controller/config.json` | `c18c86fd…8bc22` | `4e865ac2…93805a` |

`/boot/config.txt` — the duplicate banner block removed, overlay changed:

```diff
 #### Volumio i2s setting below: do not alter ####
-dtoverlay=i-sabre-q2m
-
-#### Volumio i2s setting below: do not alter ####
-dtoverlay=i-sabre-q2m
+dtoverlay=hifiberry-dac
```

`/boot/userconfig.txt` — the conflicting second DAC overlay and the inert directive
removed; everything else (DSI panel, `dtparam=audio=off`, active-fan, `i2c=on`) untouched:

```diff
 # Use external DAC/I2S; avoid the analog jack
 dtparam=audio=off
-dtoverlay=i2s-dac
-param=i2s_mclk=on
```

`param=i2s_mclk=on` is not a config.txt directive at all and `i2s_mclk` is not a
recognised overlay parameter — confirmed against the authoritative on-device
`/boot/overlays/README`. It was silently ignored; removing it changes no behaviour.

Volumio's own selection was set to match, i.e. exactly what picking the dropdown entry
writes: `i2s_enabled=true`, `i2s_dac="Generic I2S DAC"`, `i2s_id="hifiberry-dac"`,
`outputdevicename="Generic I2S DAC"`, `outputdevicecardname="sndrpihifiberry"`.

Verified after applying: **1** banner block, **1** `dtoverlay=` line, both JSON files
parse, and every pre-change file hash still matches its backup.

### A latent Volumio defect found while doing this

`/etc/asound.conf` pins the hardware device **by card name**
(`pcm.volumioHw { type hw; card "<outputdevicecardname>" }`), generated by
`ControllerAlsa.prototype.internalUpdateALSAConfigFile`. That generator is reached from
`updateVolumeSettings()`, which `onVolumioStart` calls **only conditionally** (when a
config key happens to be missing).

So changing the DAC to a profile with a **different ALSA card name** does not guarantee
`asound.conf` is regenerated at start. The stale name would leave MPD unable to open the
device — audio silently dead, with nothing obviously wrong in the config.

It was aligned by hand here (`card "DAC"` → `card "sndrpihifiberry"`), which also matches
what the generator would produce from the new `outputdevicecardname`, so a regeneration
is a no-op either way. This is a third candidate upstream bug, alongside the duplicate
banner block and the empty `hotRemoveI2SDAC` stub.

## What remains

**One cold start** — the device tree overlay is applied by the boot firmware, so the
change cannot take effect without it. The runtime `dtoverlay` route cannot be used: the
old overlay is already present from this start and the two conflict on `&sound`.

### Expected signature after the cold start

| measurement | before | expected after |
|---|---|---|
| `dmesg` codec error lines | 116 | **0** |
| codec log lines not matching the error pattern | 0 | 0 |
| ALSA card name | `DAC` | `sndrpihifiberry` |
| `i2c` clients on bus 1 | `1-0048` present | **absent** |
| `hw_params` for a 192 kHz file | 192000 | 192000 |

### Rollback

```sh
sudo cp /home/<user>/dac-experiments/config.txt.before-20260919     /boot/config.txt
sudo cp /home/<user>/dac-experiments/userconfig.txt.before-20260919 /boot/userconfig.txt
sudo cp /home/<user>/dac-experiments/asound.conf.before-20260919    /etc/asound.conf
cp /home/<user>/dac-experiments/i2s_dacs-config.json.before         /data/configuration/system_controller/i2s_dacs/config.json
cp /home/<user>/dac-experiments/alsa_controller-config.json.before  /data/configuration/audio_interface/alsa_controller/config.json
```

then one cold start. Copies of both boot files also sit on the FAT boot partition as
`*.volumio-bak-20260919`, so a configuration that prevents booting is recoverable from a
PC with the card removed — no dependence on the device coming up.

## Still open

- Whether the analog output is clean at 192 kHz (experiment E3 — needs ears; there is no
  capture path and CamillaDSP holds the ALSA device).
- Whether capping the pipeline at 192 kHz makes DSD play as PCM (experiment E4).
- Whether *any* of the three candidate upstream bugs gets filed. Separate decision.

---

## VERIFIED - after the cold start (2026-09-19, device uptime fresh)

The cold start was performed by the operator. All expected signals confirmed:

| measurement | before | after |
|---|---|---|
| codec I2C error lines in `dmesg` | **116** per session | **0** |
| `Audiophonics Device ID : FFFFFF87` line | present (a failed read) | **absent** |
| ALSA card 1 | `DAC` / `I-Sabre Q2M DAC` | **`sndrpihifiberry`** / `pcm5102a-hifi-0` |
| `i2c` client on bus 1 | `1-0048` present | **absent** (only the DSI panel/touch remain) |
| banner blocks in `/boot/config.txt` | 2 | **1** |
| overlay | `dtoverlay=i-sabre-q2m` ×2 | **`dtoverlay=hifiberry-dac`** |
| Volumio selection | Audiophonics I-Sabre ES9028Q2M | **Generic I2S DAC** / `hifiberry-dac` |

Playback verified live through the full chain, MPD reporting `state=play` with no error and
the hardware device `RUNNING` at the negotiated rate:

| source | rate at `hw:DAC` |
|---|---|
| 44.1 kHz / 16-bit FLAC | 44100 |
| 192 kHz / 24-bit FLAC | 192000 |
| DSD64 (`.dsf`) | 352800 |

**`asound.conf` needed the hand alignment.** `internalUpdateALSAConfigFile` builds
`card "<outputdevicecardname>"` but is only reached *conditionally* from `onVolumioStart`,
so the stale `card "DAC"` would not have been corrected automatically. It was set to
`sndrpihifiberry` before the cold start and held.

### What this does NOT prove

The swap removes the failing control path; it does not change what the DAC does. The chip was
running on power-on defaults before (because every write failed) and is on power-on defaults
now (because there is no codec driver at all). So:

- **For DSD this is decisive:** with no control plane the ≥352.8 kHz rate-class bit can never be
  set, so native DSD is not reachable on this board. The remedy is to cap the pipeline at
  192 kHz and let DSD play as PCM (plan experiment E4).
- **For 192 kHz this is NOT yet proven.** The measurable gain is the removal of 116 failing I2C
  transactions per session from the audio path (they occur inside `hw_params` at stream start
  and on every track change, so they sit directly in the playback path). Whether 192 kHz is now
  *audibly* reliable is an empirical question that needs the operator's ears - plan experiment
  E3. No claim is made here either way.

Still open: E3 (is 192 kHz clean with FusionDSP bypassed?) and E4 (does capping at 192 kHz make
DSD play?).
