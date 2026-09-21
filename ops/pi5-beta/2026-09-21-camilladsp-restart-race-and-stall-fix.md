# 2026-09-21 — pi5-beta: camilladsp restart race (the "long squeak then silence") + fix

**Device:** `pi5-beta` · **Follows:** `2026-09-21-rate-adjust-template-fix-and-underrun-ab.md`
**Status:** diagnosis COMPLETE and evidenced from the operator's own log; fix **APPLIED and
VERIFIED** on rate changes. **One part deliberately NOT applied** (§6) — see that section.

Operator report: switching DSD → Spotify on the web UI gives **a long delay, then a very loud
squeak** (audible-harm risk), then no sound. Observed twice, 19:29–19:31 and 19:38–19:39.

---

## 1. What the operator's log showed

```
19:31:34  camilladsp respawn in 100 ms  (attempt 0/10)     ← clean-exit path
19:31:35  camilladsp respawn in 1000 ms (attempt 1/10)     ← FAILURE path, immediately after
...
19:38:02  camilladsp stopping service pid 29601
19:38:02  camilladsp service terminated, instance 1
19:38:02  camilladsp service started and running in background, instance 1
19:38:02  camilladsp respawn in 1000 ms (attempt 1/10)
19:39:43  (101 seconds later) Volumio is not playing
```

The `1000 ms` on a fresh start is `baseRespawnDelayMs × 2^(n−1)` — the **failure** ladder, not the
100 ms clean-exit delay. `attempt 1/10` then climbing is the doubling that produces the silence.

## 2. Root cause — two defects in `camilladsp-js.js`, both ours to fix

**(a) A deliberate stop left the failure streak primed.** `this.stop()` sets `run = false`;
`listenerClose` then early-returned **without touching `consecutiveRespawns`**:

```js
if (run === false)
    return;                       // counter left as-is
```

FusionDSP's rate change *is* a `stop()` → `start()` pair (`index.js` `checksamplerate`: `needRestart`
→ `camillaProcess.stop()` → `createCamilladspfile(cb)` → `camillaProcess.start()`). So every rate
change left the counter primed, and the next early exit was charged as **attempt N** rather than a
fresh start — the 1000/2000/4000/8000 ms ladder.

**(b) `processStop()` did not clear the JS handle, so an immediate `start()` was silently deferred.**
`camilla = null` lived only in the async `close` handler, while `processStop()` waited only on
`/proc/<pid>/cmdline`. So `start()` → `processSpawn()` hit:

```js
if (camilla !== null) return;     // stale (dead) handle -> NO spawn
```

…and the actual respawn was postponed to the `close` event — i.e. **delayed by the backoff**
instead of starting at once.

## 3. The fix (v10) — three lifecycle changes, no audio-data change

| | Change |
|---|---|
| A | `listenerClose` takes the child it belongs to and **ignores a stale close** (`if (child !== camilla) return;`), so a late close cannot clobber a newer instance or double-spawn |
| B | Each child is bound to its own handler: `child.on("close", (c,s) => listenerClose(child,c,s))` |
| C | `processStop()` sets `camilla = null` **after** confirming the OS process is gone, so an immediate `start()` spawns at once |
| + | A **deliberate** stop (`run === false`) now resets `consecutiveRespawns = 0` — a requested stop is not a failure |

sha256 `56f3a393ff9cc094886ce26f56af19efe59bf04a233beb439a8f33c94fb92422` (9210 B)
Backup: `camilladsp-js.js.bak-v9-20260921` = `5dafbc797e79ae5dcac533df8e77637baa6d1ef05bb4a77149acbe90ed61e07b`
Rollback: copy the backup over the live file, then reload the core.

**Why this is safe to ship ahead of full verification:** these touch process *lifecycle* only. They
cannot alter sample data, the DSP graph, or the config. The worst case is a spawn that does not
happen, which is the thing being fixed.

## 4. Verification — applied, then 3 cycles of real rate changes

| | Before | After |
|---|---|---|
| `respawn in 1000 ms` (failure path) | every switch | **0** |
| `attempt N/10` | climbing | **`attempt 0/10` every time** |
| Restart shape | `stopping` → deferred → backoff | `stopping` / `terminated` / `started` **in the same second** |
| Rates reached | — | **6/6 correct** (384000 DSD, 44100 FLAC) |
| New camilladsp ERRORs | stalling | **none** |

Three full DSD256(384 k) ↔ 44.1 k FLAC cycles, each landing `card1 RUNNING` at the right rate.
Normal playback re-verified independently (44.1 kHz, `RUNNING`).

## 5. A flawed instrument I built and discarded — worth recording

To avoid reproducing an audible-harm artefact, I pivoted `pcm.volumioHw` in `/etc/asound.conf`
from `sndrpihifiberry` to the **`Dummy`** card so the pipeline ran with no output. It *appeared*
to work (card1 closed, card7 RUNNING) but it was **not a valid rig**: camilladsp then failed with

```
ERROR [camilladsp] Playback error: snd_pcm_prepare failed with error 'Device or resource busy (16)'
```

and the DSD phase showed camilladsp not running at all. The Dummy card is not a neutral
substitute. It was **restored byte-exact** (`/etc/asound.conf` = `a297a504…`, verified) before
anything else was concluded. Lesson: a proxy device is not a proxy for the *hardware's* rate and
contention behaviour.

## 6. NOT applied — the suspected squeal mitigation

The loud artefact is likely **rate-mismatched data** read by a freshly-started camilladsp. The
FIFO is drained only on an *unclean* exit:

```js
if (code > 0) { execSync("/bin/dd if=/tmp/fusiondspfifo of=/dev/null bs=32k iflag=nonblock"); }
```

A rate change is a **clean** stop, so that drain never runs on the very path that needs it. Moving
the drain into `processSpawn` (so it happens on **every** spawn) is a two-line change and is the
most plausible mitigation.

**It is deliberately NOT in v10**, because unlike §3 it touches the data path and I have not
verified it — and the last time I changed audio behaviour on a reasoned hunch the operator had his
worst experience. It needs the failing switch reproduced safely first.

## 7. Evidence gap — stated plainly

Everything above was reproduced with a **single writer** (MPD ↔ MPD). The operator's failure needs
**two writers** (MPD + go-librespot) i.e. a Spotify Connect takeover, which **cannot be initiated
from the device** — a session must be started by a client app. So the stall fix is verified; the
**squeal elimination is not**, and cannot be until that switch is reproduced under a safe rig
(Loopback card rather than Dummy) or with the amplifier physically attenuated.

## 8. Evidence commands

```
grep -E "camilladsp (respawn|stopping|service)" <journal for the window>
grep -c "camilladsp respawn in 1000 ms" <journal>      # failure ladder — should be 0 after v10
node --check /data/plugins/audio_interface/fusiondsp/camilladsp-js.js
```
