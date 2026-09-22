# 2026-09-19 - I²S regression and byte-exact revert

**Status: reverted, awaiting a cold start. Root cause: our own profile change.**

## What happened

Earlier the same day we swapped the I²S profile from `dvoerlay=i-sabre-q2m` to
`dtoverlay=hifiberry-dac`, on the reasoning recorded in
`2026-09-19-i2s-dac-control-plane-rootcause.md`. That reasoning was wrong.

The operator reported **no sound on the DAC's I²S input**, while **coax and optical inputs work
normally**. That single fact re-frames the whole diagnosis:

- The HAT is **not a DAC**. It is an **I²S output board**.
- The DAC is an external **SMSL DO400**, fed over I²S.
- Coax/optical working proves the DO400 is fine; they are fed from a different source.

## Why the previous reasoning failed

The prior record established - correctly - that every codec register access fails with
`-EREMOTEIO` from boot and that nothing answers at I²C `0x48`. It then reasoned:

> the driver never reads the chip, so it is not configuring anything, so a codec-less profile
> is strictly better.

**The second step does not follow.** The *machine driver* (`snd-rpi-i-sabre-q2m`) also configures
the **I²S DAI - framing, bit-clock ratio and clocking - and none of that uses I²C.** A blind
control plane is not the same as an inert driver.

Replacing it with `hifiberry-dac` substitutes a different machine driver, which frames the
stream differently (it drives the `pcm5102a` dummy codec). The DO400's I²S input did not decode
that framing, so the link went silent.

## The measurement gap that let a regression pass as "verified"

Every check in the execution record is **upstream of the failure**:

| check | what it actually observes |
|---|---|
| `hw_params` rate/format | the Pi's ALSA device, not the wire |
| `hw_ptr` advancing | the playback clock running - **silence advances it identically** |
| CamillaDSP `GetSignalLevels` | signal at the Pi's output stage, not at the DAC |
| `dmesg` I²C error count = 0 | one subsystem, unrelated to framing |
| MPD `state=play, error=None` | the player, not the sink |

None of these can distinguish "a decodable I²S frame is arriving at the DO400" from "the Pi is
happily clocking bits into a link the DAC cannot lock". **"Verified" meant "clean at the ALSA
device", not "audible", and that gap is how a regression was reported as a pass.**

A second instance of the same gap: after disabling FusionDSP, `hw_ptr` was still advancing at
192 kHz and every software indicator was green, while CamillaDSP was in fact emitting silence.
The instrument that finally detected it was CamillaDSP's own `GetSignalLevels` (below).

## Revert

Five files restored **byte-exact** from the pre-change copies, hashes verified against the
values recorded in the execution record:

| file | restored sha256 (prefix) |
|---|---|
| `/boot/config.txt` | `8f448dd300422b46` |
| `/boot/userconfig.txt` | `02f8b9a56680a388` |
| `/etc/asound.conf` | `e1466cfa7b81fcba` |
| `…/system_controller/i2s_dacs/config.json` | `88e2c78640613255` |
| `…/audio_interface/alsa_controller/config.json` | `c18c86fdc58fe090` |

`dtoverlay=i-sabre-q2m` (both blocks as originally present), `dtoverlay=i2s-dac`,
`param=i2s_mclk=on`, `card "DAC"` and the Audiophonics selection are all back as they were.
**Takes effect on a cold start** - the device tree overlay is applied by the boot firmware.

FusionDSP's effect flag was also restored to `true` after the disable/enable experiment.

## Instruments gained

- **CamillaDSP control socket, port 9876.** Websocket, messages are **JSON-encoded bare command
  strings** (`"GetState"`, not `{"id":1,...}` and not a raw string). `GetSignalLevels` returns
  per-channel `capture_rms/peak` and `playback_rms/peak` in dBFS - the first instrument in this
  work that can tell **silence from audio** rather than merely "a device is open". Also
  `GetState`, `GetVolume`, `GetCaptureRate`, `GetProcessingLoad`, `GetBufferLevel`.
- **Volumio socket.io `callMethod`**, payload
  `{endpoint: "<category>/<name>", method, data}` on port 3000 - arrival is confirmed in the
  `volumio` journal as `CALLMETHOD: <category> <name> <method>`. Note the shape is `endpoint`,
  not `plugin`; a wrong shape is swallowed silently.
- **Hazard:** the `fusiondsp.disableeffect` method is not a safe bypass. It sets the flag and
  logs "Effects disabled" but can leave the ALSA hook in `asound.conf` and the engine emitting
  silence; its CamillaDSP reload reported a WebSocket error. Use `GetSignalLevels` to confirm
  audio before and after any DSP state change.

## Open

- Does the DO400's I²S input require a specific pinout/mode selection, and does it need MCLK on
  the link? Not yet established (web search was unavailable). These are the questions that
  govern whether 192 kHz and DSD can work over this link at all.
- The original complaint - 192 kHz unreliable, DSD not playing - is now a question about the
  **I²S link between the Pi and the DO400**, not about a codec register on the HAT.

---

## The board identified - and the manufacturer prescribes the profile we reverted

**Board: TZT Ustars Audio R19 Digital Audio Board** - a **digital audio *output* board** for
Raspberry Pi (SPDIF/coax + optical + HDMI-I²S). It contains **no DAC**.

Manufacturer's own parameter-test note:

> "For Raspberry Pi 4B (version 1.2) to install the optimized version for MoodeAudio640.
> **The output setting is: Audiophonics ES9028/9038 DAC**"

So `dtoverlay=i-sabre-q2m` - the "Audiophonics I-Sabre ES9028Q2M" selection - is the
**documented, required** configuration for this board. The root-cause record's claim that the
profile "matched by name, not by hardware" and that "Generic I2S DAC" was "definitively correct"
was wrong: the board depends on the I²S timing that overlay's **machine driver** produces. That
is exactly the mechanism the correction banner describes, now confirmed by the vendor.

### Stated capabilities (vendor specification)

| output | PCM | DSD |
|---|---|---|
| Coaxial | 44.1-384 kHz / 24-bit | DSD64/128 (DoP) |
| Optical | 44.1-192 kHz / 24-bit | DSD64 (DoP) |
| **I²S over HDMI** | **44.1-384 kHz / 32-bit** | **DSD64/128/256/512 (Native DSD)** |

Other stated properties that matter to us:

- **Local dual audio clock synchronisation** - two on-board oscillators, *not* PLL-derived.
- **"Use HDMI interface to output IIS signal, with MCLK output, you can set multiple output
  modes and MCLK clock frequency."** The board therefore has **hardware output-mode / MCLK
  settings** (jumpers or switches) that must match the receiving DAC.
- **"No need to set output resampling, just turn off by default, truly original code lossless
  output"** - the vendor expects resampling **off**.
- Vendor distinguishes a general version from **"the official version for VOLUMIO", which
  supports 44.1-192 kHz and DSD64**. DSD128+ is stated for the MoodeAudio variant. This is a
  software-side ceiling, not an IIS limit (the IIS section claims up to DSD512).

### Receiving DAC side - SMSL DO400 (from its own manual)

- **I²S input is an HDMI-type connector** whose pinout **includes MCLK.**
- Menu has **I²S MODE**: `NORMAL (PS AUDIO format)` / `REVERSED (DATA and LRCK inverted)`, with
  the vendor note: *"this option is used to match different I²S interface standards. Before use,
  please check the interface definition of the signal source."*
- Menu also has **AUDIO PHASE**: `NORMAL (2+,3−)` / `INVERTED (2+,3+)`.
- Spec table breaks out USB (PCM to 768 kHz/32-bit, DSD to 22.5792 MHz) and coax/optical
  (DoP64); it does **not** list a separate I²S row. States all inputs except Bluetooth support DSD.

### Consequence

**Two independent hardware settings must agree, and neither is visible to the Pi:**
the R19's output-mode / MCLK selection, and the DO400's I²S MODE. The DO400 manual explicitly
directs the operator to match its setting to the source's interface definition. This is the
first place to look for the original complaint (192 kHz unreliable, DSD silent) - and it is
outside anything we can measure from the Pi.

### Corrected ceiling for the original question

Under VOLUMIO the vendor documents **PCM 44.1-192 kHz and DSD64**. The operator's DSD test files
were DSD64, DSD128 and DSD256 - so DSD128/256 not working is *expected* for this board on
Volumio, and only 192 kHz PCM and DSD64 are worth pursuing here.

---

## Hardware configuration of the R19 (vendor documentation) - and the prime suspect for 192 kHz

### On-board settings

The board photo and tables give four jumper groups:

| jumper | function |
|---|---|
| J1 | no function |
| **J2, J3** | **IIS MCLK frequency** (4 modes) |
| **J4, J5** | **IIS interface mode** (4 HDMI pinout modes) |

And **`LOCK` / `DSD` indicator LEDs** - a free diagnostic for whether the board sees a valid
IIS signal and whether it is in DSD mode.

Vendor note: **"IIS output BICK is 64FS"** - the bit clock is 64×Fs.

### MCLK mode table (J2/J3) - relevant rows

| sampling frequency | mode 1 (J2 ✗ J3 ✗) | mode 2 (J2 ✓ J3 ✗) | mode 3 (J2 ✗ J3 ✓) | mode 4 (J2 ✓ J3 ✓) |
|---|---|---|---|---|
| 44.1 kHz | 256FS / 11.289 M | 512FS / 22.579 M | 1024FS / 45.158 M | 256FS / 11.289 M |
| 96 kHz | 256FS / 24.576 M | 256FS / 22.579 M | 512FS / 49.152 M | 128FS / 12.288 M |
| **192 kHz** | **128FS / 24.576 M** | **128FS / 24.576 M** | **256FS / 49.152 M** | **64FS / 12.288 M** |
| DSD 2.8224 MHz (DSD64) | 22.579 M | 22.579 M | 45.158 M | 11.289 M |

**This explains the symptom exactly if the board is in MCLK mode 4.** In mode 4, 44.1 kHz gets
256FS (11.289 M) and 96 kHz gets 128FS (12.288 M) - both healthy - while **192 kHz gets only
64FS (12.288 M), a MCLK ratio an ES9039-class DAC will not lock to.** That is precisely the
observed pattern: 44.1 and 96 work on IIS, 192 kHz reports playback with no sound.

In **mode 1** (J2 and J3 both removed), 192 kHz gets **128FS / 24.576 MHz**, a healthy ratio.

### IIS interface mode table (J4/J5) vs the receiving DAC

| mode | jumpers | pin 1 / 3 | pin 7 / 9 |
|---|---|---|---|
| 1 | J4 ✗ J5 ✗ | DTAT− / DTAT+ (R) | LRCK− / LRCK+ (L) |
| 2 | J4 ✗ J5 ✓ | DTAT+ / DTAT− (R) | LRCK+ / LRCK− (L) |
| 3 | J4 ✓ J5 ✗ | DTAT− / DTAT+ (L) | LRCK− / LRCK+ (R) |
| 4 | J4 ✓ J5 ✓ | DTAT+ / DTAT− (L) | LRCK+ / LRCK− (R) |

Modes 1/2 present the **R** side first, modes 3/4 the **L** side; modes 1/3 are **negative-first**,
2/4 **positive-first**. This is the polarity the SMSL DO400's own **`I2S MODE`** menu item exists
to match - its manual offers `NORMAL (PS AUDIO format)` and `REVERSED (DATA and LRCK inverted)`,
with the instruction to check the source's interface definition. The two settings must agree.

### Host compatibility - Raspberry Pi 5 is NOT listed

Vendor states the board is for **Raspberry Pi 2B / 3B / 3B+ / 4B**. This device is a **Pi 5**,
where I²S is provided by RP1 with different clocking and overlays. Low rates working proves the
link is functional, but high-rate behaviour on a Pi 5 is outside anything the vendor validated;
their tables were derived on a Pi 4B.

### Power-safe configuration

The board can be powered from the Pi's 40-pin header **or** its own DC socket, and the vendor
states the two must never be used together - doing so can damage the board, the Pi, or the supply.
This device is PoE-powered through a HAT, so nothing may be plugged into the R19's DC socket.

### Prior operator characterisation (independent confirmation)

A public Volumio community post by the operator (June 2025, Volumio 4.012, Pi 5 8 GB) on this same
board already recorded the matrix above, with playback option **"Audiophonics I-Sabre ES9028Q2M"**
chosen "as per the item's ad" - matching the vendor's parameter-test instruction. 192/24 over
i2s/HDMI was "Volumio shows playback, but no sound to DAC" while coax worked; DSD "can start a song,
but no sound". The symptom predates every change made in this record and is rate-dependent, not
profile-dependent.

---

## RETRACTION - the MCLK-mode-4 prediction above is WRONG

The previous section infers from the vendor MCLK table that **mode 4** (192 kHz → 64FS/12.288 M)
must fail to lock, and offers that as the explanation. **The operator's own measurements, made
in May 2025 on this exact board, disprove it.** That was reasoning from a datasheet expectation
instead of from the available evidence, and the evidence was one hyperlink away.

### The measured matrix (operator, Volumio, this board + SMSL DO400)

| MCLK mode | jumpers | 192/24 over IIS | DSD over IIS |
|---|---|---|---|
| mode 1 | none fitted | **maxes out at 96/24** | - |
| mode 2 | J2 ✓ J3 ✗ | maxes out at 96/24 | **works - DSD out as PCM**, then drops out after ~3 tracks (click → white noise) |
| mode 3 | J2 ✗ J3 ✓ | not reported | not reported |
| **mode 4** | **J2 ✓ J3 ✓** | **192/24 WORKS** | **no DSD output** |

Operator verbatim, May 2025:

> "previously with another HAT, the i2s/HDMI output will max out at 96/24, but **in MCLK_mode4, I
> was able to get it to play 192/24 thru i2s but no DSD output**"

> "The log … is supposedly in **MCLK_mode2** and it was able to **output DSD thru i2s as PCM**. In
> the Playback options, it was left as DSD native. HOWEVER!1!!! It played like 3 songs and then
> **audio completely dropped out** when I tried to load another DSD file, with only a slight click
> and then white noise. In this mode, it also maxes out at 96/24 otherwise."

> "The pics in the link with regards to the jumpers and the config of i2s modes/output appears to be
> wrong, but having played around with it, I've gotten it to output the correct channel by **just
> leaving it as is [no jumpers]**."

### What this actually means

1. **The board forces a tradeoff between 192 kHz PCM and DSD over IIS.** Mode 4 gives 192/24 with
   no DSD; mode 2 gives DSD but caps PCM at 96/24. They are mutually exclusive on this board.
2. **192 kHz is achievable** - it is not a hardware wall, it is a jumper selection. The earlier
   framing in this workstream ("192 kHz may be beyond this HAT") was wrong.
3. **DSD's failure in mode 2 is abrupt and total** (three tracks, then a click and white noise),
   consistent with the board's function-control MCU switching MCLK on a rate change and the DAC
   failing to re-lock.
4. **Both the operator and the vendor point at the platform, not the board.** The operator's
   conclusion: *"it appears that moode already has full support for these hats for DSD via i2s."*
   The vendor distinguishes a **Volumio variant (44.1-192 kHz, DSD64)** from a **MoodeAudio variant
   (44.1-384 kHz, DSD64-128)**. Volumio lacking a proper driver for this board is therefore a
   documented platform gap, not a defect we can repair from the device.
5. **Both profiles were already tried** by the operator in May 2025 ("Audiophonics ES9028/9038 DAC"
   and "HifiBerry DAC"). The `hifiberry-dac` substitution made in this workstream had already been
   tested a year earlier.

### Corrected guidance

- **Want 192 kHz PCM** → MCLK mode 4; accept no DSD.
- **Want DSD** → MCLK mode 2; PCM caps at 96/24 and dropouts are expected.
- **Want 384 kHz or DSD128+** → outside what Volumio supports for this board.
- Jumpers J4/J5 set the IIS pinout and must still agree with the DO400's `I2S MODE`.

---

## TRUST CORRECTION - the vendor jumper tables are NOT a verified mapping

The sections above record the R19's jumper tables as if they define which physical jumper
position selects which mode. **They do not, and they must not be used to instruct anyone.**
The operator's own experience, in the same post quoted above:

> "I also played with the jumpers for MCLK_modes… **this is where it gets interesting as I can not
> rely on the pics in the link to correspond to what actual mode it's in**"

So the correspondence between **documented mode number** and **actual jumper position** is
**unverified**, and the mode labels used in the measured matrix (`mode 1/2/4`) are the operator's
*attribution* at the time, not a confirmed mapping. Re-reading the vendor table and saying
"set J2+J3 to mode 4" would be an instruction built on an unverified diagram.

### What is actually solid, and what is not

| statement | status |
|---|---|
| IIS bit clock is 64FS | vendor claim, not independently measured |
| J2/J3 relate to MCLK, J4/J5 to interface mode | **verified** - both the board silkscreen labels and the operator's experiments |
| the four MCLK mode *numbers* correspond to specific J2/J3 positions in a specific way | **UNVERIFIED - do not rely on it** |
| some MCLK configuration yields 192/24 over IIS with no DSD | **observed** (operator) |
| some MCLK configuration yields DSD (as PCM) with a 96/24 PCM ceiling | **observed** (operator) |
| no jumpers fitted yields correct channel assignment | **observed** (operator) |
| the DO400 `I2S MODE` and the R19 interface mode (J4/J5) must agree | inferred from the DO400 manual's own instruction; **not yet verified on this pair** |

### The method this forces

Because the documented mapping cannot be trusted, the configuration cannot be *read off a table*
- it has to be **found empirically**, and the board provides its own instruments for that:
the **`LOCK` and `DSD` indicator LEDs**.

Protocol: fix the interface jumpers at the position already observed to give correct channels,
then sweep the MCLK jumpers one at a time. For each position, drive a known 192/24 file and a
known DSD64 file from the Pi and record - for each - the LED state and whether there is sound.
The Pi's side (rate actually sent, signal levels present) is measurable and stable, so the jumper
position is the only variable. That yields a jumper-position→behaviour table that is *measured*
rather than transcribed.

### Reusable lesson: mark provenance and trust level in every record

This is the second time in this workstream that a **secondary source was treated as
authoritative** and conclusions were built on it - first the `family_*` memory digests (used to
justify deletions until the digest bodies were actually read), then these vendor jumper tables.
Both looked authoritative; neither was. Records in this directory should state, for each fact,
whether it is **measured by us**, **observed by the operator**, or **claimed by a third party** -
and third-party claims should never be the basis of an instruction.
