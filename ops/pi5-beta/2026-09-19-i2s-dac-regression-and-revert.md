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
