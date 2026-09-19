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

# 2026-09-19 — I2S DAC: the codec's I2C control plane has never worked

**Device:** `pi5-beta`
**Symptom reported:** "192 kHz does not play reliably if at all, and neither does DSD",
with Volumio → Playback Options → *I2S DAC Model* set to
**"Audiophonics I-Sabre ES9028Q2M"**, on a generic ("R19") HAT.

**Conclusion:** this is not a 192 kHz/DSD performance limit of the HAT. The HAT's DAC
**does not answer on I2C**, so the kernel codec driver has never been able to configure
it. Everything that depends on a register write silently fails. One of those registers
is the rate-class flag for ≥ 352.8 kHz — which is exactly where DSD lands.

---

## 1. Verified: the I2C control plane is dead

`dmesg` from **11.8 s uptime** — the moment the driver probed the chip — onward:

```
i-sabre-codec-i2c 1-0048: ASoC: error at soc_component_read_no_lock on
    i-sabre-codec-i2c.1-0048 for register: [0x00000001] -121
snd-rpi-i-sabre-q2m soc@...:sound: Audiophonics Device ID : FFFFFF87
i-sabre-codec-i2c 1-0048: ASoC: error at soc_component_read_no_lock on
    i-sabre-codec-i2c.1-0048 for register: [0x00000002] -121
snd-rpi-i-sabre-q2m soc@...:sound: Audiophonics API revision : FFFFFF87
```

`-121` is `-EREMOTEIO`: the chip NAKs the address. Every access fails, in both
directions.

Over one 27-minute boot:

| | count |
|---|---|
| total I2C errors on `1-0048` | **109** |
| successful register operations | **0** |
| register `0x21` (mute / Digital Playback Switch) | 73 |
| register `0x10` ("Notify Sampling Frequency") | 32 |
| register `0x22` (FIR filter type) | 2 |
| registers `0x01`, `0x02` (chip ID / API revision) | 1 each |

### The `FFFFFF87` tell

`0xFFFFFF87` is not a device ID. It is **`-121` (`0x87`) sign-extended to 32 bits** —
the driver's read failed, and the driver logged the error code as if it were the value.
The same is true of the "API revision". So:

- the driver has **never** read the chip ID,
- the driver's API-revision branch (which selects a register map per board revision)
  is operating on a meaningless value,
- the driver never checks either read, so probe "succeeds" regardless.

This is why the device looks configured and works at all: the ASoC card is created and
the I2S link carries audio. Only the control plane is missing.

## 2. Verified: what the failing registers control

From the upstream Raspberry Pi kernel codec driver — `i_sabre_codec_hw_params()`
writes **register `0x10`, bit 0**:

| rate handed to the codec | bit 0 written |
|---|---|
| 44100, 48000, 88200, 96000, 176400, **192000** | `0x00` |
| **352800, 384000**, 705600, 768000, 1411200, 1536000 | **`0x01`** |

The write always fails. For ≤ 192 kHz the intended value happens to be `0x00`, which is
also the chip's power-on default — so that case is harmless by accident. For
**≥ 352.8 kHz the chip can never be told it is in the high-rate regime**, because the
write that would say so never lands.

Also failing, though benignly: `0x21` is the DAC mute/unmute (`i_sabre_codec_dac_mute()`),
which fails on **every track change**; `0x22` is the FIR filter type; `0x20` is the
digital playback volume (moot — Volumio is configured `mixer_type: None`).

**Hypothesis (not proven):** the missing `0x10` write is the mechanism by which
DSD-derived streams fail. It is consistent with the observed boundary (192 kHz works,
the ≥ 352.8 kHz class does not) but has not been confirmed against the chip, because
the chip cannot be read.

## 3. Verified: the HAT's I2C topology

The `i-sabre-q2m` overlay hard-codes the codec:

```dts
fragment@2 {
    target = <&i2c1>;
    __overlay__ {
        i-sabre-codec@48 {
            compatible = "audiophonics,i-sabre-codec";
            reg = <0x48>;
        };
    };
};
```

`&i2c1` resolves to the 40-pin header bus (`/dev/i2c-1`, RP1 `i2c@74000`) — confirmed
via the device-tree aliases. That is the correct bus for a HAT.

A raw scan of `/dev/i2c-1` over addresses `0x03`–`0x77` found:

- `0x48` → `EBUSY` (claimed by the kernel driver, therefore **not testable** from
  userspace while bound),
- **every other address → NAK, nothing responds.**

There is also **no HAT EEPROM at `0x50`**, and no I2C controller-level errors in
`dmesg` (no timeouts, no arbitration loss). So the bus itself is electrically healthy;
there is simply nothing else on it, and the one address we cannot test is the one that
fails from the driver.

**Inference:** the HAT is a control-less I2S DAC board — the DAC either has no I2C
wiring or no I2C-controllable part at `0x48`. This is typical of generic clone boards:
the chip runs on its power-on defaults and the board is sold as a plain I2S DAC.

## 4. Verified: Volumio is not refusing anything

Driven through MPD with the hardware device watched live:

| source | MPD reports | rate at `hw:DAC` | XRUNs | MPD error |
|---|---|---|---|---|
| 192 kHz / 24-bit FLAC | `192000:24:2` | 192000 | 0 | none |
| 44.1 kHz / 16-bit FLAC (control) | `44100:16:2` | 44100 | 0 | none |
| DSD64 (`.dsf`) | `dsd64:2` | **352800** | 0 | none |
| DSD128 (`.dsf`) | `dsd128:2` | **384000** | 0 | none |
| DSD256 (`.dsf`) | `192000:24:2` | 192000 | 0 | none |

`dop "no"` is set on MPD's output, so DSD is converted to PCM and lands at
DSD-rate ÷ 8 — i.e. **DSD64 → 352.8 kHz and DSD128 → 384 kHz**, the two rates that
require the register write that cannot happen.

Measured DAC clock against wall clock (`hw_ptr` slope; the chain was left untouched):

| nominal | measured | deviation |
|---|---|---|
| 44100 | 44 098.8 Hz | −27 ppm |
| 192000 | 191 989.4 Hz | −55 ppm |
| 384000 | 383 976.5 Hz | −61 ppm |

All within tens of ppm, i.e. inaudible and within measurement noise. **So "the Pi
cannot clock 192 kHz" is false** — it clocks 384 kHz correctly.

## 5. Verified: boot-configuration defects

`/boot/config.txt`:

```
15  #### Volumio i2s setting below: do not alter ####
16  dtoverlay=i-sabre-q2m
17
18  #### Volumio i2s setting below: do not alter ####   <- duplicate block
19  dtoverlay=i-sabre-q2m
```

`/boot/userconfig.txt`:

```
dtparam=audio=off
dtoverlay=i2s-dac            <- a different, codec-less I2S DAC overlay
param=i2s_mclk=on            <- not a valid config.txt directive; silently ignored
```

Include order is `volumioconfig.txt` → `userconfig.txt` → Volumio's block, so
**two conflicting overlays both target `&sound`**, and the sabre overlay is applied
twice on top of `i2s-dac`. Observable consequence: `snd_soc_pcm1794a` (from `i2s-dac`)
is loaded alongside the sabre codec.

Neither file is hand-maintained by us; both are in scope for the remediation plan.

**Hypothesis (not proven):** whether this pile-up contributes to the I2C failure.
Against it: the failures are pure bus NAKs on a bus with no other device, which is
more consistent with absent hardware than with a device-tree conflict. For it: nothing
rules it out, and it is cheap to test.

### Volumio-side code findings

- `ControllerI2s.prototype.hotRemoveI2SDAC` is an **empty stub** —
  `app/plugins/system_controller/i2s_dacs/index.js`. A hot-remove path is declared and
  never implemented.
- `revomeAllDtOverlays` (sic) can only remove *runtime* overlays
  (`dtoverlay -l` / `-r`); `dtoverlay -l` reports **"No overlays loaded"**, because the
  DAC overlay is applied by the boot firmware, not at runtime.
- The config.txt write path dedups with a regex built unescaped from the banner line
  (`i2sOverlayBanner`). It did not hold here — the file contains **two** banner blocks.
  This is a genuine upstream bug and is independent of the HAT.
- MPD's generated output block has **no `mixer_type`**, so MPD defaults to *hardware*
  mixing, fails to find a `PCM` control, and logs
  `output: Failed to open mixer for "alsa": no such mixer control: PCM` on every track
  change — while Volumio's own config says `mixer_type: None`. Harmless to audio
  (MPD falls back to software volume) but it is a config-generation gap.

## 6. Verified: the signal chain adds a second suspect

```
MPD (dop "no")
 └─ volumio (empty)
     └─ volumioDsp (plug, slave format S32_LE)
         └─ fusiondsphook ─── /tmp/fusiondspfifo ─── CamillaDSP
                                                        (12 biquads, −6.87 dB,
                                                         queuelimit: 1,
                                                         enable_rate_adjust: true,
                                                         rate = 192000/352800/384000)
         └─ postDsp → Peppyalsa (meter) → volumioOutput (plug) → volumioHw = hw:DAC
```

FusionDSP's engine is **CamillaDSP**, running inline over a userspace FIFO, and it
**re-rates itself per stream** — its config was observed rewritten to 192000, 352800 and
384000 as the test files changed. Its DSP load scales with rate (~8.7× the realtime work
at 384 kHz versus 44.1 kHz), and its position inline inherently forces DSD → PCM.

If the reported "unreliable" behaviour is stutter or glitch rather than wrong pitch,
**this is the prime suspect** — see the remediation plan, experiment 3.

## 7. Open / not determined

- **Whether the analog output is actually clean at 192 kHz.** There is no capture path
  from the DAC's output, and CamillaDSP holds the ALSA device open permanently, so it
  cannot be bypassed. This is below the software's visibility: it is not a refusal, not
  an underrun (0 observed), not a clocking error and not a wrong rate.
- **Whether a chip physically exists at `0x48`.** The address is claimed by the kernel
  driver, so a userspace probe returns `EBUSY`. Settling it requires unbinding or
  deleting the i2c client — see plan experiment 5.
- **Whether native DSD is achievable on this HAT at all.** Without a control plane an
  ES9028Q2M cannot be put into DSD mode over I2S. If the chip has no I2C, native DSD is
  off the table for this board and the ceiling is DSD decoded to PCM (see experiment 4).

## 8. Upstream position

Nothing needs to be requested from the Volumio maintainers about this HAT:

- Volumio's profile list is a list of *overlays*, and the correct match for a
  control-less I2S DAC **already ships** — "Generic I2S DAC" → `hifiberry-dac`, which
  declares `ti,pcm5102a` and contains **no I2C node at all**, while targeting the same
  `i2s_clk_producer` (identical clock topology).
- "Audiophonics I-Sabre ES9028Q2M" was matched by *name*, not by *hardware*. A genuine
  Audiophonics board with a dead I2C line would fail identically.

Two findings in section 5 are legitimately reportable as Volumio bugs, independent of
the HAT: the **duplicate `dtoverlay=` block** from the banner-regex dedup, and the
**empty `hotRemoveI2SDAC` stub**. Neither has been filed; that needs a separate,
explicit decision.
