# 2026-09-21 — pi5-beta: post-power-cycle validation (audio path proven end-to-end)

**Device:** `pi5-beta` (Raspberry Pi 5, Volumio 4.204, I2S HAT DAC, DSI panel)
**Follows:** `2026-09-21-device-verification-and-eeprom.md`
**Status:** power cycle performed by the operator; **every previously-open validation item
PASSES.** The un-resolved "handover gap" line of work is now closed as unnecessary.

## Why this record exists

The streamlined `/boot/userconfig.txt` (sha256 `96a2b2e4…`, 2507 B — see
`bootconfig/userconfig.txt.streamlined-96a2b2e4`) removed six directives that were
inert or actively misleading. Removing `disable_overscan`, `framebuffer_width`,
`framebuffer_height`, `gpu_freq`, `sdram_freq` and a `dtoverlay=rpi-active-fan,…`
line is only safe if proven under a real cold start, because config.txt is read
at boot and a bad edit can cost the display or the audio device.

Confirmation required a power cycle. A cold start has now happened, and the
device was re-examined live rather than assumed healthy from the fact that it
answered SSH.

## 1. Boot and thermal

| Probe | Result |
|---|---|
| Boot time | `2026-09-21 16:14:39` |
| `vcgencmd get_throttled` | `0x0` — including all "ever" sticky bits |
| SoC temperature | 55.7 → 57.3 °C across the session |
| `measure_clock arm` | 2.4–2.6 GHz (governor ramping normally) |
| Root/data disk | 214 G free of 230 G (3 % used) |

`0x0` *with the ever-bits clear* is the meaningful part: it rules out a brownout
event during the reboot itself, not merely an idle moment afterwards.

## 2. The three things the edit could have broken

These are exactly the surfaces the removed directives touched. All three were
read from the kernel, not inferred from a successful login.

| Surface | Evidence | Verdict |
|---|---|---|
| DSI panel | `/sys/class/drm/card0-DSI-2/status` = `connected`, mode `480x1920` | **PASS** — panel mode comes from the DSI overlay, not the removed framebuffer/overscan lines |
| Touch | `/proc/bus/input/devices` → `Goodix Capacitive TouchScreen`, handlers `kbd mouse0 event1` | **PASS** — I2C still enabled, controller bound |
| Kiosk render | `scrot` on `:0` captured the Now Playing idle screen (clock + 7-day forecast), zero black area | **PASS** — Chromium survived the mode change |

The kiosk screenshot is the load-bearing one. A connected DRM connector proves the
*panel* is up; only the captured frame proves the *compositor* is drawing to it.
Both were checked because the previous black-screen defect was invisible in DRM state.

## 3. Audio path — proven with real playback, not enumeration

Card enumeration alone does not prove audio reaches the DAC, so a full
play → stop cycle was run against the real queue.

```
GET /api/v1/commands/?cmd=play      -> http=200
  player=play   card1 pcm=state: RUNNING   (stable for the whole 5 s window)

/proc/asound/card1/pcm0p/sub0/hw_params
  access: MMAP_INTERLEAVED   format: S32_LE   subformat: STD
  channels: 2                rate: 44100 (44100/1)
  period_size: 1024          buffer_size: 8192

GET /api/v1/commands/?cmd=stop      -> http=200
  player=stop   card1 pcm=closed
```

| Probe | Result |
|---|---|
| ALSA cards | `0:Loopback`, `1:sndrpihifiberry`, `7:Dummy` |
| DAC driver | `snd_rpi_hifiberry_dac` (dtoverlay `hifiberry-dac` present in `config.txt`) |
| Active PCM format | `S32_LE / 44100 / 2 ch` — FusionDSP's output format, i.e. the DSP holds the DAC, not the player |
| DSP process | `camilladsp -p 9876 … fusiondsp/camilladsp.yml` alive throughout |

`S32_LE` at 44.1 kHz is the confirmation that the signal is traversing the
FusionDSP graph rather than reaching the HAT directly — the property that
matters, and one that a mere `aplay -l` cannot show.

## 4. Handover latency (the measurement that closes the open question)

Three play → stop cycles, timed from the REST call to the observed ALSA/DSP state:

| Round | play → `RUNNING` | stop → camilladsp re-settled |
|---|---|---|
| 1 | 156 ms | 647 ms |
| 2 | **92 ms** | 667 ms |
| 3 | **88 ms** | 619 ms |

camilladsp exits cleanly on stop and is respawned with a new pid each time
(observed `…3750 → 4269 → 4387 → 4533`), which is the intended v8/v9
clean-exit-respawn behaviour: the respawn delay is now a flat 100 ms instead of
the pre-v8 exponential ladder (measured 15×100 ms, 8×200 ms, 4×400 ms, 1×800 ms,
1×1600 ms). Because the DSP is back and ready within ~0.65 s of a stop, the next
play finds it waiting and starts in under 160 ms.

**Conclusion:** the previously-suspected multi-hundred-millisecond FIFO
handover gap does not reproduce in normal use. The proposed "FIFO keeper"
mechanism (staged v11b) was rejected three times by independent review for real
lifecycle defects and is **not needed** — see §6.

## 5. Patch integrity across the reboot

All three locally-patched plugin files survived the cold start byte-identical
(to `md5sum`/`sha256sum` of the deployed versions), which also confirms none of
them live on a ramdisk path:

| File | sha256 (unchanged) |
|---|---|
| `/data/plugins/music_service/spop/index.js` | `da9ce4c7…` |
| `/volumio/app/plugins/music_service/mpd/index.js` | `05e66b5c…` |
| `/data/plugins/audio_interface/fusiondsp/camilladsp-js.js` | `5dafbc79…` |

Note the asymmetry, which is worth remembering: `mpd` lives under the
overlay-backed `/volumio` tree (so it *is* persisted, but by the overlay, not by
`/data`), while `spop` and FusionDSP live on `/data`. Both survive a reboot; only
`/data` survives a factory reset.

## 6. Decision — the "handover gap" work is closed

The v8/v9 camilladsp changes already deliver what the keeper mechanism was meant
to buy. The keeper was rejected three times by independent review
(round 3: a stale-respawn path returning without closing the descriptor, and
`this.start()` advancing the generation while leaving the descriptor physically
live but logically stale). Given §4 measures sub-160 ms resumption with no
audible-path defect, continuing that branch would be pursuing a mechanism for a
problem that no longer exists.

**Closed, not deferred.** If a real gap is ever heard, re-open with a fresh
measurement; do not resume the v11b patch.

## 7. Evidence floor

Everything above is from a live read on the device after the cold start:
`/sys/class/drm/*/status`, `/proc/asound/cards`,
`/proc/asound/card1/pcm0p/sub0/{status,hw_params}`, `/proc/bus/input/devices`,
`vcgencmd get_throttled`, a captured framebuffer, and the REST API at
`localhost:3000`. Nothing here is inferred from the device merely being
reachable, and no operator-set value (volume) was altered as a test side effect.

## 8. Residual, unrelated to this work

- `ifup@eth0.service` exits `status=1/FAILURE` once at boot (16:15:22). Benign
  race — the interface is configured by another path and networking is up; it has
  no bearing on the audio or display checks above. Left as-is.
- Two non-fatal third-party plugin errors remain deliberately unpatched
  (`now_playing` `getPluginInfo` once per core start; an MPD play-path
  `…reading 'split'` seen only at 15:11:34, before the patched region).
