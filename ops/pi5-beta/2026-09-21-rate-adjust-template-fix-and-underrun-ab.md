# 2026-09-21 — pi5-beta: `enable_rate_adjust` template fix + a controlled underrun A/B

**Device:** `pi5-beta` · **Follows:** `2026-09-21-post-power-cycle-validation.md`
**Status:** change APPLIED and VERIFIED mechanically; benefit measured but **not statistically
conclusive**, and the recorded premise did **not** reproduce.

## 1. What changed — one word, in one file

FusionDSP regenerates `camilladsp.yml` by substituting into a template, so a fix written into
the *generated* file is lost on the next playback. The value was hardcoded in the template:

```
/data/plugins/audio_interface/fusiondsp/camilladsp.conf.yml
  line 9:  enable_rate_adjust: true   ->   false
```

| | sha256 |
|---|---|
| template BEFORE | `95b88c7bef87ebba32cf4ae95c413f263fb77a1df4604226349c0a1ea2da6a15` |
| template AFTER | `0f8f03e9b0c2c62bdf7cd7b93c88d06ccd8d779a4b08ff9b4ec7fd3245197f4c` |
| backup (385 B, mode/owner preserved) | `/data/plugins/audio_interface/fusiondsp/camilladsp.conf.yml.orig-20260921` = `95b88c7b…` |

Applied with an exact-line replacement guarded by an assertion (exactly one occurrence
required), then re-grepped: 1 occurrence, now `false`, surrounding block unchanged.

**Why the template and not the generated file:** the generated config's mtime tracks playback
start, not service start — it was rewritten at 19:14:49, one second before camilladsp started.
A full `volumio vrestart` did **not** regenerate it; only a playback start did.

**Verified mechanically:** after the restart + a playback start, the generated config reads
`enable_rate_adjust: false`, with `samplerate: 384000` correctly substituted and
`capture`/`playback` sections intact.

**Rollback:** restore the backup over the template. The change is then reverted at the next
playback start; no service restart needed.

## 2. The measurement — instrument was validated FIRST

`/tmp/camilladsp.log` holds the counter (`-l warn`; underruns log at **WARN**, confirmed).
An empty log is ambiguous — it means *no underruns* **or** *a blind instrument* — so the
instrument was positively controlled before any zero was believed:

| Step | Result |
|---|---|
| DSD256 playback at 384 kHz, normal load | log **0 bytes** |
| **Positive control** — 8 spinners on 4 cores, 25 s | log **0 → 424 bytes**, captured `WARN … PB: wait underrun, trying to recover` ×2 |

**The instrument is valid.** Zeros are therefore meaningful.

### The premise did not reproduce

The recorded figure was **~14–18 underruns/s** (718–720 per 40 s). Observed here, at normal
load with DSD256 → 384 kHz: **zero**, and under artificial 4-core saturation the worst case was
**5 events per 30 s = 0.17/s — roughly 100× lower**. The recorded counts were taken at load
**4.7–5.6**, i.e. on an already-saturated machine; that condition is not normal operation here
(load during DSD256 playback measured ~2.7).

### Controlled A/B

Same track, same 384 kHz output, same 30 s of identical saturation (8 spinners), every sample
gated on `pcm state: RUNNING` before **and** after — a sample failing either gate was void.

| Arm | `enable_rate_adjust` | Underruns (3 × 30 s) | Total | Temp |
|---|---|---|---|---|
| **A** | `true` (original) | 0, 2, 5 | **7** | up to ~76 °C |
| **B** | `false` (fixed) | 0, 1, 0 | **1** | up to ~79.3 °C |

Direction favours the change (~7× fewer events). **But with 8 total events split 7/1, the
two-tailed binomial p ≈ 0.07 — suggestive, not significant at α=0.05.** I am not claiming this
as a proven win.

**A confound that argues *for* the result, not against it:** Arm B ran *hotter* and tripped the
80 °C soft limit, which reduces clocks and should *increase* underruns — yet B still had fewer.
So the true effect, if any, is at least this size.

### No regression

- camilladsp log since the change: **2 lines, both WARN underrun, zero ERROR**, no non-underrun
  warnings — so no new fault class was introduced.
- Continuous 384 kHz playback with camilladsp pid stable (24324), `seek` advancing normally.
- Journal noise is all pre-existing MyVolumio cloud-auth (`DEVICE_NOT_FOUND` / `USER_NOT_FOUND`)
  plus Volumio's known benign `updateQueue error: null`.

## 3. Incidental finding — the real CPU consumer is not the DSP

`top` during DSD256 → 384 kHz playback:

| Process | %CPU |
|---|---|
| **`python3` (PeppyMeter screensaver)** | **112.5** |
| mpd | 25.0 |
| Xorg | 18.8 |
| **camilladsp** | **18.8** |

The decorative VU meter costs **~6× the audio DSP**. It is also **inline in the PCM chain** —
`postDsp → Peppyalsa (type meter) → postpeppyalsa` — not beside it. If underruns ever become
audible, this is the first lever, not the overclock.

Also noted: `camilladsp` runs `SCHED_OTHER`, nice 0, with no realtime priority; output buffer is
8192 frames = **21.3 ms at 384 kHz** (vs 186 ms at 44.1 kHz) — the safety margin shrinks ~8.7×
as the rate rises. Both are candidate levers, neither pursued.

## 4. State changes caused by this work — disclosed

1. **Sticky throttle bit set.** `get_throttled` went from `0x0` to **`0x80000`** (bit 19, *soft
   temperature limit has occurred*). Nothing is active now (bit 2 clear), clock returns to
   1500 MHz idle and the governor max is unchanged at 2800 MHz. The stress test caused it;
   sticky bits clear on the next reboot. Earlier claims of `0x0` including the ever-bits no
   longer hold for this boot.
2. **The playback queue was replaced.** It held 9 items before testing; it now holds 1 (the
   DSD256 test track). Playback was stopped afterwards, and `status`/`volume`/`mute`/`volatile`
   were restored to exactly their pre-test values.

## 5. Decision

**Keep the change.** It is mechanically verified, introduces no regression, is one word, has a
byte-exact backup, and the direction of evidence favours it — while also removing a rate-adjust
mechanism that sits awkwardly against the standing *no resampling* directive (no `Resampler`
stage exists in the config, but rate adjust is separate from pipeline resampling, so whether it
falls inside that directive remains an operator judgement).

The honest summary: **the change is safe, it is in place, and its benefit is unproven at normal
load because there is no measurable problem at normal load.** Proving it would need a condition
that actually reproduces the recorded rate, which this device in its current state does not.

## 6. Evidence commands (reproducible)

```
# the property
grep enable_rate_adjust /data/plugins/audio_interface/fusiondsp/camilladsp.conf.yml
grep enable_rate_adjust /data/configuration/audio_interface/fusiondsp/camilladsp.yml
# the counter, with the liveness gate that makes a sample valid
head -1 /proc/asound/card1/pcm0p/sub0/status     # must be "state: RUNNING"
stat -c %s /tmp/camilladsp.log                   # offset, then diff after the window
grep -c underrun /tmp/camilladsp.log
```
