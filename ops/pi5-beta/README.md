# pi5-beta — device profile and invariants

## Hardware / software

| Item | Value |
|---|---|
| Board | Raspberry Pi 5 Model B Rev 1.1 (aarch64) |
| OS | Debian bookworm, kernel 6.12.75-v8+ |
| Volumio | system version 4.204 (built 2026-09-14) |
| Node | v20.5.1 |
| Volume arch id | `VOLUMIO_ARCH="arm"` (from `/etc/os-release`) |
| Display | 1920×480 strip, driven by a Chromium kiosk |
| Audio out | I2S HAT (generic clone), codec-less — see below |
| Backend checkout on device | `/volumio` (git) |

## Layout that matters

| Path | Meaning |
|---|---|
| `/data/plugins/<category>/<name>/` | installed plugin tree (the scan path) |
| `/data/configuration/<category>/<name>/config.json` | the plugin's **live config** — not part of the package |
| `/data/INTERNAL/NowPlayingPlugin/` | Now Playing user data (backgrounds, fonts, settings backups) |
| `/opt/volumiokiosk.sh` | kiosk launcher (waits for port 3000, then loops Chromium) |
| `/lib/systemd/system/volumio-kiosk.service` | kiosk unit (`startx` → Xsession → `/opt/volumiokiosk.sh`) |

## Audio / I2S DAC

The board drives an I2S HAT. Two things are worth knowing before touching audio:

- **The HAT's DAC does not answer on I2C.** The codec driver's register accesses fail
  with `-EREMOTEIO` from boot, every time, and have never once succeeded. The
  `Audiophonics Device ID : FFFFFF87` line in `dmesg` is not an ID — it is the error
  code `-121` sign-extended and printed as if it were a value.
- **The profile that was selected matched by name, not by hardware.** Volumio's DAC
  list is a list of overlays; "Audiophonics I-Sabre ES9028Q2M" hard-codes its codec at
  `i2c1 / 0x48`, which nothing on this HAT answers. A codec-less profile
  ("Generic I2S DAC" → `hifiberry-dac`) is the correct match and ships with Volumio.

Consequence: everything that needs a register write is unavailable — the DAC mute, the
FIR filter type, the digital volume, and the rate-class flag for ≥ 352.8 kHz, which is
where DSD lands. See:

- `2026-09-19-i2s-dac-control-plane-rootcause.md` — diagnosis and evidence
- `2026-09-19-i2s-dac-remediation-plan.md` — the experiment plan and rollback paths
- `scripts/i2s-diag.sh` — read-only snapshot, run before/after every audio experiment

### Audio pipeline shape

MPD feeds an ALSA chain that includes the FusionDSP plugin (whose engine is
CamillaDSP, inline over a userspace FIFO) and a PeppyMeter scope:

```
MPD ── volumio ── volumioDsp (plug, S32_LE) ── fusiondsphook ── /tmp/fusiondspfifo
                                                                     │
                                          CamillaDSP ◄───────────────┘
                                               │
  hw:DAC ◄── volumioHw ◄── volumioOutput ◄── postPeppyalsa ◄── Peppyalsa (meter)
```

Two practical consequences: the DSP engine's cost scales with sample rate (roughly
8.7× the realtime work at 384 kHz versus 44.1 kHz), and because it sits inline it
forces DSD → PCM. With `dop "no"` on MPD's output, DSD is converted at DSD-rate ÷ 8 —
measured DSD64 → 352.8 kHz, DSD128 → 384 kHz, DSD256 → 192 kHz.

## Display stack (as observed)

```
volumio-kiosk.service
  └─ /usr/bin/startx /etc/X11/Xsession /opt/volumiokiosk.sh -- -nocursor
       ├─ Xorg :0 -nocursor
       └─ /opt/volumiokiosk.sh
            ├─ wait for TCP 127.0.0.1:3000 (Volumio backend)
            ├─ clean stale Chromium Singleton* files in /data/volumiokiosk
            ├─ openbox-session &
            └─ while true; do chromium --kiosk --user-data-dir=/data/volumiokiosk \
                 ... http://localhost:4004; done
```

The kiosk renders **port 4004**, i.e. the *Now Playing plugin's own app*, not the
Volumio UI on 3000. 3000 is only used as the readiness gate.

The unit as shipped has **no `Restart=` policy** and is not enabled via a `.wants`
symlink. The backend toggles it from `saveHDMISettings`
(`app/plugins/system_controller/system/index.js`), which runs
`systemctl <restart|stop> volumio-kiosk.service && systemctl <enable|disable> ...`.
Consequence: if X or Chromium exits for any reason, nothing brings it back — the
screen stays dead until a manual power cycle. See
`2026-09-19-now-playing-1.1.1.md` §2 for the mitigation we applied.

## Capturing the display (for verification)

The kiosk's X server is **not on `:0`**. Read the real values from the running
Chromium's environment instead of guessing:

```sh
CPID=$(pgrep -f 'chromium.*localhost:4004' | head -1)
XA=$(tr '\0' '\n' < /proc/$CPID/environ | sed -n 's/^XAUTHORITY=//p')
DISP=$(tr '\0' '\n' < /proc/$CPID/environ | sed -n 's/^DISPLAY=//p')
XAUTHORITY="$XA" DISPLAY="$DISP" scrot -o /tmp/screen.png
```

Observed live values: `DISPLAY=:1`, `XAUTHORITY=/home/<user>/.Xauthority`.
Guessing `:0` fails with `Can't open X display` — which looks like a dead display but
is only the wrong display number. Check `scrot`'s exit code too: it fails silently if
its output is being piped, and a chained `&& echo` will not run.

## Invariants (learned the hard way)

1. **Replace a plugin by swapping its tree — never install over it.** Two separate
   mechanisms are involved:
   - The store installer refuses with *"Plugin `<name>` already exists"* because
     `checkPluginDoesntExist` rejects when the `<plugin_type>.<name>` key is already
     in Volumio's plugin config (`app/pluginmanager.js:1600-1612`). That is a
     **registry** check, not a filesystem check.
   - `pluginFolderCleanup` (`app/pluginmanager.js:1384`) walks every subdirectory
     under a plugin path as a plugin folder. Configured ones are left untouched;
     unconfigured ones are **removed only when it is called with `cleanup === true`**
     (the uninstall path) — the unconditional delete is commented out in the source,
     noted there as having once deleted plugins when new ones were installed. The
     same routine does remove stray non-directory entries and empty category
     directories.

   So move the old tree **out** of `/data/plugins/<category>/` and put the new tree
   in its place: a tree left behind is walked on every cleanup pass and can be
   deleted by a later uninstall.
2. **Never touch `/data/configuration/<category>/<name>/config.json`** during a
   plugin upgrade. It is the user's live configuration and is not shipped in the
   package.
3. **Run a plugin's `install.sh` the way Volumio does**: as root, from inside the
   plugin directory. Several plugins invoke `su <user>` internally, which fails
   from a non-root context.
4. **`/var/log` is a 20 MB tmpfs.** Do not point persistent journald at it
   (`No space left on device`). The runtime journal lives in `/run` (volatile,
   capped at 30 MB) — so evidence from a crash is lost on reboot unless a
   different location is chosen deliberately.
5. **Power health is a first-class suspect** for display failures. Measured
   2026-09-19: **292 undervoltage events in ~12 h** — the first 24 s after power-on,
   then 11–55 per hour, all day — each followed by `Voltage normalised` within
   seconds, i.e. transient 5 V dips rather than a sustained brownout.
   `vcgencmd get_throttled` reports sticky `0x50000`. The rail feeds an **NVMe SSD**
   (238 GB, PCIe) plus the strip display, with no USB peripherals attached. A Pi 5
   wants 5 V / 5 A (27 W) at its USB-C input.
   - **PoE as an alternative supply:** the PoE budget has to cover the same load, and
     an under-sized class (e.g. 802.3af, 15.4 W) tends to show up as subsystems
     failing to initialise rather than as a clean boot — Wi-Fi is typically the first
     casualty. Check the PoE class/budget and the splitter's 5 V regulation before
     concluding that Wi-Fi itself is at fault.
   - **Observed setup and its expected behaviour:** 802.3at (25.5 W at the port) via a
     PoE HAT, with the PoE router carrying **no data**. Two consequences:
     1. A dead-end uplink should *not* steal connectivity: eth0 is configured
        `iface eth0 inet dhcp` with `noipv4ll`, so with no DHCP server it takes no
        address and adds no route — Wi-Fi keeps the default route. Volumio's network
        plugin has no "disable wireless when wired" path either (`wireless_enabled` is
        only written by the explicit `wirelessEnable`/`wirelessDisable` methods), so
        Wi-Fi disappearing is **not** explained by the network configuration.
     2. 802.3at at the port is *less* than a healthy 5 V/5 A USB-C supply once the
        HAT's conversion losses are counted, so PoE is a cabling convenience rather
        than a power upgrade — and this board already logs undervoltage on its
        current supply.
   - **RESOLVED - see `2026-09-19-poe-wifi-single-network-mode.md`.** The cause is
     Volumio's Single Network Mode: it decides by `eth0` **carrier** alone, so a
     PoE HAT (cable always plugged) makes it suppress Wi-Fi by design. Fix applied:
     `SINGLE_NETWORK_MODE=false` in `/volumio/.env`. Measured on the same boots: PoE
     power was **cleaner** than USB-C (0 undervoltage events vs 326).
   - **Settling it with evidence:** `scripts/poe-diag.sh` (+ the `poe-diag.service`
     one-shot on the device) writes throttled flags, core volts, the undervoltage
     timeline, the Wi-Fi driver bring-up lines, interface/route/rfkill state and the
     eth0 link state to `/data` **at boot** — before Wi-Fi fails and the box becomes
     unreachable. Arm it, power over PoE, switch back, then read the report.
6. **FusionDSP regenerates `camilladsp.yml` from `camilladsp.conf.yml` — the TEMPLATE is
   the authority, never the generated file.** A fix written into
   `/data/configuration/audio_interface/fusiondsp/camilladsp.yml` is silently lost at the
   next playback start. Measured 2026-09-21: the generated file's mtime tracks **playback**
   start, not service start — a full `volumio vrestart` does **not** regenerate it, but
   starting playback does (generated `19:14:49`, camilladsp started `19:14:50`). The
   template also carries the substitutions (`${outputsamplerate}`, `${chunksize}`,
   `${resampling}`, `${composeout}`, `${resulteq}`, `${mixers}`, `${composedpipeline}`).
   - Corollary: this is why `enable_rate_adjust` kept reverting to `true` — it is hardcoded
     in the template, not derived from plugin config. Fixed 2026-09-21
     (`2026-09-21-rate-adjust-template-fix-and-underrun-ab.md`); backup at
     `camilladsp.conf.yml.orig-20260921`.
7. **The VU meter is INLINE in the audio path, and it costs more than the DSP.** The PCM
   chain is `postDsp → Peppyalsa (type meter, scopes.0 peppyalsa) → postpeppyalsa → hardware`,
   so the meter's ALSA plugin is not beside the stream, it is in it. Measured during DSD256 →
   384 kHz playback: **`python3` (PeppyMeter) 112.5 % CPU vs `camilladsp` 18.8 %** — about
   6× the DSP. If audio glitching ever becomes audible, this is the first lever to examine,
   not the overclock (which is already at the 2800 MHz ceiling with `0x0` throttling).
   - Related sizing fact: the output buffer is **8192 frames = 21.3 ms at 384 kHz** but
     186 ms at 44.1 kHz — the safety margin shrinks ~8.7× as the rate rises, which is why
     high rates are the sensitive case. `camilladsp` also runs `SCHED_OTHER`, nice 0, with
     no realtime priority, against a concurrent >1-core decorative load.
   - **Counting underruns:** `camilladsp` is launched `-l warn` and underruns log at WARN, so
     `/tmp/camilladsp.log` is a usable counter — but it is **append-mode and survives
     restarts**, so count *deltas in bytes*, and ALWAYS gate a sample on
     `head -1 /proc/asound/card1/pcm0p/sub0/status` reading `state: RUNNING` both before and
     after, or a stopped stream yields a meaningless zero. An empty log means either no
     underruns or a blind instrument — positively control it (saturate the cores) before
     trusting a zero. It is **not** truncated per run, contrary to an earlier note.
8. **The inline meter is DELIBERATE — and `O_NONBLOCK` does NOT make it "latency-harmless".**
   Do not overclaim here; an earlier version of this entry did, and independent review
   corrected it. Two separate results, both measured 2026-09-21:
   - *Deliberate:* `peppy_screensaver/index.js` picks the topology and logs the choice. With
     `useDSP = True` (the Fusion bridge on, our device) it selects **`inline-meter (bridge on)`**
     — `${alsaInlineMeter}` → `Peppyalsa`, inline in the audio path. The off-path alternative
     (`route_policy "duplicate"` + `type multi` with the meter on a `dummy` branch) is used only
     when the bridge is **off**, because the inline one is chosen for *"no multi, no dummy, no
     rate constraint"*. Upstream knows inline causes `hw_params` trouble — their own comment on
     the alternative says it *"avoids hw_params issues when meter is inline with main audio"* —
     but the alternative imposes a rate constraint, which would conflict with the standing
     **no-resampling** directive. So it is a deliberate trade-off, not an oversight.
   - *What the fd flags actually establish, and what they do not:* every handle on the meter
     FIFOs is non-blocking — `camilladsp` writes `/tmp/myfifo` and `/tmp/myfifosa` with flags
     `04001` (`O_WRONLY|O_NONBLOCK`), the core holds them `02404002`, the Python meter
     `02404000`. That **does** remove FIFO backpressure as a way to sleep the audio thread: a
     slow consumer makes the write fail with `EAGAIN` rather than wait. It does **not** show
     that the inline plugin is free of cost. `libpeppyalsa.so` still executes *synchronously in
     the audio path*, and could copy/convert samples, take a lock, allocate per period, or do
     its own error handling — none of which the descriptor flags can see, and none of which has
     been verified. **"No FIFO backpressure" is not "no inline cost".**
   - *The stronger evidence is the measurement, not the flags:* at normal load we measure
     **zero** underruns at DSD256/384 kHz; deliberate 4-core saturation produced at worst
     5 per 30 s. That supports "CPU contention is the observed risk", and **not** "the topology
     is defective". It does not establish audibility either way.
   - The real cost is the **consumer's CPU**, and it is tunable, not structural:
     `screensaver/spectrum/config.txt` has `frame.rate = 30`, `update.ui.interval = 0.04`, and
     renders a 1920×480 skin straight to `/dev/fb0`; the scope uses `spectrum_size 20`,
     `smoothing_factor 60`, `decay_ms 500`. Reducing the frame rate or using a lighter skin is
     the lever that actually addresses the ~1.1-core cost. The meter only runs during playback
     (nothing is holding the FIFOs at idle).
   - **Before any topology surgery, profile**: the audio-thread write path with `perf` or
     scheduler tracing would settle whether the inline plugin adds jitter. Cheaper than
     re-architecting, and the ranking of options is: leave topology → reduce consumer cost →
     enlarge the buffer (21.3 ms at 384 kHz vs 186 ms at 44.1 kHz) → multi/duplicate *only after*
     verifying at every source rate that `hw_params` stay unchanged → `nice`/`SCHED_FIFO` as a
     last resort only, since it changes who loses under contention rather than making the meter
     cheaper.
