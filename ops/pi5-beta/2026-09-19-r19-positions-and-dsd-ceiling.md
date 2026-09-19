# 2026-09-19 — R19 / DO400 over I²S: what actually works

**Outcome: 192 kHz PCM now works over I²S. DSD plays, but as PCM. Native DSD is not
achievable on this chain.** Written after an extended live session. **NOT YET COMMITTED —
the operator has an open question about whether records should be pushed automatically.**

## The board and the link, correctly understood

- **TZT Ustars Audio R19** = digital audio **output** board (coax / optical / HDMI-I²S). No DAC.
- **SMSL DO400** = the DAC, fed over the **HDMI-I²S** output.
- Volumio profile **must** be "Audiophonics I-Sabre ES9028Q2M" (`dtoverlay=i-sabre-q2m`) — the
  vendor's own parameter-test note says so. The dead I²C at `0x48` is expected: there is no chip
  on the HAT. **The machine driver still configures the I²S DAI framing, which is why the profile
  matters despite the dead control plane.**

## Measured position → behaviour (the useful result)

| MCLK position (J2 / J3) | 192/24 over I²S | DSD over I²S |
|---|---|---|
| no jumpers | no — caps at 96/24 | — |
| J2 only | no — caps at 96/24 | plays (as PCM) |
| J2 + J3 | **no** | not tested |
| **J3 only** | **YES — works** | **plays (as PCM 352.8 k)** |

**J3 only is the configuration to use.** It is the one position that had never been tried,
precisely because the vendor's jumper documentation mislabels it — the operator's own note:
*"I can not rely on the pics in the link to correspond to what mode it's in."* The documented
mapping is not usable; this table is measured, not transcribed.

## DSD: plays, as PCM — and why native DSD is out of reach

With `dop = false` (Volumio's default), MPD **converts DSD to PCM**: DSD64 → 352.8 k,
DSD128 → 384 k, DSD256 → 192 k. So the DO400 correctly displays **PCM**. The operator's
2025 note said the same: *"it was able to output DSD thru i2s as PCM."*

Setting `dop = true` to force **DoP** (the mechanism that makes a DAC display "DSD") **broke the
chain**: the hardware device stuck at **44100** while MPD reported `dsd64:2`, and the output was a
squeak then silence. Two candidate reasons, both plausible and not yet separated:

1. **DoP cannot survive a DSP chain.** DoP is bit-sensitive — the marker must pass untouched — and
   FusionDSP's CamillaDSP sits inline (FIFO + EQ). Any processing destroys the marker.
2. The subsequent **FusionDSP `hw detection failed`** left the engine on a 44100 fallback (below).

Native DSD Direct is also unavailable: it requires the ALSA card to advertise a DSD format, and
this card advertises only `S16_LE` / `S32_LE`.

**So the honest ceiling matches the vendor's own statement** — the Volumio variant of this board
supports *44.1–192 kHz and DSD64*, with DSD delivered as PCM by Volumio's MPD. For the DO400 to
display DSD you would need a bit-perfect path, i.e. FusionDSP fully disabled, **and** DoP — an
untested combination, traded against losing the EQ.

## Operational hazard found — FusionDSP's silent 44100 fallback

```
error: FusionDsp - ----Hw detection failed :Error: Command failed:
       /data/plugins/audio_interface/fusiondsp/hw_params volumioHw > hwinfo.json
```

When FusionDSP reloads while the audio device is busy, **its sample-rate detection fails and it
falls back to 44100 without erroring visibly.** Consequence: `camilladsp.yml` says 44100 while the
ALSA hook correctly reports the stream at 352800 — a rate mismatch that presents as **a squeak and
then silence**, with MPD reporting `state=play, err=None` and `hw_ptr` still advancing.

**Recovery** (verified): stop playback, then `systemctl stop fusiondsp.service` → `start`. With the
device free, detection succeeds; `camilladsp.yml` was regenerated at the correct rate and playback
resumed at 352800.

**Diagnostic signature:** `/tmp/fusiondsp_stream_params.log` (correct stream rate) diverging from
`grep samplerate .../camilladsp.yml` (engine rate). When those disagree, the chain is broken even
though everything else looks healthy.

## Instruments established (the day's most reusable output)

| instrument | why it matters |
|---|---|
| **DAC front panel** | the only readout that sees the *wire*. Every Pi-side check can be green while the link carries nothing. |
| **CamillaDSP `:9876`** — websocket, JSON-encoded bare command strings (`"GetSignalLevels"`, not an object with `id`) | first check that distinguishes **silence from audio**. `hw_ptr` advancing does not: silence advances it identically. |
| **Volumio socket.io `callMethod`** — `{endpoint:"<category>/<name>", method, data}` | drives plugin methods directly; arrival confirmable in the journal as `CALLMETHOD: …`. The key must be `endpoint`, not `plugin`. |
| **`/etc/mpd.conf` `dop`** ← plugin config `music_service/mpd` → `dop` boolean | the DSD playback mode lives here; `savePlaybackOptions` writes it and restarts MPD. |

## Still open

- Whether **DoP + FusionDSP disabled** yields true DSD on the DO400. Untested; costs the EQ.
- **DSD128/256** native: out of scope under Volumio for this board.
- J4/J5 interface mode vs the DO400's `I2S MODE` (NORMAL/PS-Audio vs REVERSED) — never verified as
  a matched pair; current combination evidently works at 192/24, so it is consistent for now.
- Whether any position carries **both** 192 k and native DSD. J3-only carries 192 k and DSD-as-PCM.

---

## Session close - full state, 2026-09-19

### Working configuration

| item | value |
|---|---|
| Volumio I²S profile | **Audiophonics I-Sabre ES9028Q2M** (`dtoverlay=i-sabre-q2m`) - vendor-required |
| MCLK jumpers | **J3 only** (J2 removed) - the position that carries 192 k/24 |
| 192 k/24 over I²S | **works** |
| DSD over I²S | **plays, as PCM** (DSD64 → 352.8 k, DSD128 → 384 k) - DO400 correctly reports PCM |
| native DSD / DoP | **not achievable** on this chain |

### Operator-approved changes left in place

| change | evidence |
|---|---|
| FusionDSP template `enable_rate_adjust: true → false` | CamillaDSP **underruns 1,202 → 0**; `sample rate change detected` spam (once/second) → 0; `GetCaptureRate` 460-500 kHz thrashing → 384,156 Hz correct; MPD `Decoder is too slow` 6 → 0 |
| FusionDSP `chunksize 2048 → 9600` | operator's own change |

Note: the `enable_rate_adjust` edit lives in FusionDSP's **plugin template**
(`/data/plugins/audio_interface/fusiondsp/camilladsp.conf.yml`), so a FusionDSP plugin update
will silently revert it. Re-check after any FusionDSP update.

### Standing operator directive

**No resampling on this device** - not via Volumio's Playback Options resampling setting, not via
FusionDSP's `enableresampling`, not via a camilladsp resampler, not via a fixed MPD output format.
Source rates must reach the hardware unaltered. Verified clean: `alsa_controller.resampling=false`,
no `format` line in the mpd.conf alsa output, `enableresampling=false`, zero `type: Resampler` in
the active graph, and hardware rate tracks the source (44.1k → 44100, 192k → 192000, DSD64 → 352800).

### Attempts made and REVERTED the same session

| attempt | outcome |
|---|---|
| `mpd dop = true` (DoP, to make the DO400 display DSD) | **broke the chain** - hardware stuck at 44100 while MPD reported `dsd64:2`, squeak then silence. Reverted. Likely cause: DoP is bit-sensitive and cannot survive the inline FusionDSP/CamillaDSP chain. |
| FusionDSP template `silence_timeout 3.0 → 3600` | **no effect on the clicks.** Reverted. |
| Volumio fixed-rate output (`resampling=true`, target 192000) | Reduced engine restarts 6 → 2 across a four-rate test but **did not stop the pops**, and the operator had already declined this class of change. **Reverted.** |

### The unresolved problem: loud clicks at transitions

Operator reports full-scale clicks/pops between tracks and on service changes (e.g. DSD → Spotify),
**audible with the volume low and from 40 cm away**.

Mechanism established from the logs:

1. **CamillaDSP restarts on every rate change** - 6 restarts across 4 track changes at differing
   rates; `FusionDsp - If filter freq >samplerate/2 then disable it` fires at the handoff.
2. **The driver's mute write fails.** Register `0x21` (Digital Playback Switch = DAC mute) bursts
   land exactly on the transitions (e.g. 2 writes at the DSD→Spotify handoff) and every one returns
   `-EREMOTEIO`. **Nothing can mute the DAC across a transition**, so the transient goes out at full
   scale.
3. **The pop is downstream of every software gain** - it survives a low volume setting, which places
   it at the I²S link itself: engine teardown stops the clocks, the R19's clock MCU and the DO400's
   PLL relock, and the relock transient is full-scale. Software cannot attenuate it.

**Not solved.** The one configuration that removes the I²S branch from the path is the **coax
output**, already proven to carry the operator's content (44.1/96/192). The remaining software lever
is removing FusionDSP from the ALSA path, which the operator has declined.

### Process failures worth recording

- **A declined option was re-framed and applied.** The operator declined a fixed output rate; it was
  then applied as a "192 kHz cap" on the grounds that it differed from the declined 96 kHz version.
  Re-labelling a declined option is not a judgement call - it is overriding a decided *no*.
- **A config change was made with no go** (`silence_timeout`).
- Repeated measurement errors caught before being reported as results: `hw_ptr` advancing does not
  distinguish silence from audio; CamillaDSP's `-o` log truncates on restart, invalidating a "0
  underruns" reading that had to be retaken against a clean baseline; sampling `hw_params` too early
  reads the previous stream's rate.
