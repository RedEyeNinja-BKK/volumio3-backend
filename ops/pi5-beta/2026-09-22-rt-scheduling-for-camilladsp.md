# 2026-09-22 — Real-time scheduling for the CamillaDSP backend (and a governor A/B)

**Outcome: the `camilladsp` backend now actually gets real-time priority, and the CPU
governor is left on `conservative` (measured, not assumed).** Two changes were authorised
as one task — (a) an A/B of the CPU governor, (b) a real-time priority grant for the
audio DSP — and were executed in that order.

Scope note: this is a **scheduling-authority** change, not a latency-tuning change. Nothing
here alters resampling, buffers, or the audio path; the device still does no resampling.

---

## (a) CPU governor — A/B/A, verdict: **keep `conservative`**

Tested in a single gated 44.1 kHz stream, A→B→A, with the governor switched between arms
and the tick/transition counters read from the kernel.

| arm | governor | observed clock | transitions | underruns |
|---|---|---|---|---|
| A | `conservative` | held 2800 MHz | **0** across 3518/3518 ticks | 0 |
| B | `schedutil` | dipped below 2800 MHz | **4082** | 0 |
| A′ | `conservative` | held 2800 MHz | 0 | 0 |

`schedutil` produced four thousand governor transitions in the same window where
`conservative` produced none, with no compensating benefit. **`conservative` was already
the value on the device and is left there.** Volumio's own Pi-5 carve-out in
`/usr/bin/volumio_cpu_tweak` was not touched.

Not tested in this section: any ≥ 352.8 kHz arm. This device has a separate, sporadic I²S wedge
under investigation, and that measurement was expected to need a cold start. It was subsequently
run **without** one — see the real-time section below — because the I²S path was clean at the time
(no errors since boot), so there was no wedge to clear.

---

## (b) Real-time priority for the CamillaDSP backend

### What was wrong

The audio DSP ran at normal scheduling priority. Under load it competes for CPU with
everything else on the box, including a kiosk Chromium and the Node backend, and the
failure mode of losing that competition is an audio dropout.

### What was changed

Two files, both now on the device:

| File | Role |
|---|---|
| `/etc/systemd/system/volumio.service.d/10-audio-rt.conf` | grants the unit a real-time priority **ceiling** of 45 |
| `/data/plugins/audio_interface/fusiondsp/camilladsp-js.js` | makes the DSP obtain priority up to that ceiling |

The drop-in is deliberately a **ceiling, not a pin**: it says what the unit *may* use,
and the process decides per-thread. That follows the upstream design — the deployed
`camilladsp` 4.1.3 bundles the `audio_thread_priority` crate and contains its own
promote/demote paths (`"Capture thread has real-time priority."`,
`"…could not get real time priority, error:"`, `"…returned to normal priority."`). The
unit-level ceiling is what makes that existing mechanism able to succeed.

**Why 45:** above mpd's two FIFO-40 device threads (the consumer must be able to preempt
the producer), and below every kernel thread measured on this board — `migration/*` 99,
`ntpd` 99, `watchdogd` 50, `irq/*` 50, `card0-crtc0` 50.

### Measured behaviour

* The DSP **main thread is FIFO 45** in every measurement taken.
* **Worker coverage is NOT uniform, and the cause is identified.** In 4 of 5 readings every
  thread of the running chain was at FIFO 45, including `AlsaPlayback` and `FileCapture` —
  created *after* the grant, inheriting the policy from the granted main thread. In the fifth
  reading the workers were `SCHED_OTHER` while the main thread was FIFO 45, and they stayed
  that way for the whole stream.
  The grant is issued as `chrt -f -p 45 -a <pid>`. On util-linux 2.38.1 the `-a` (all-tasks)
  flag is honoured **only when it precedes `-p`**; in the position above it is accepted and
  silently ignored — `rc=0`, no error, no warning. The call therefore promotes the main thread
  and nothing else, and every worker is covered *only* by inheritance from it. A worker that
  already existed when the helper ran is never promoted, for the life of that process. Whether
  it is covered is a race against the helper's arrival — the same item produced both outcomes
  across successive spawns, so it is not a property of the rate. Reported as observed; the
  reorder that removes the race is identified but not deployed.
* **The benefit claimed is small and honest.** This is absence-of-failure at normal load,
  not the removal of a reproduced dropout. The pre-change log contains 11 underrun/overrun
  events, all from a single earlier date; none recurred across the sustained-load window of
  this session (count unchanged throughout).

### Rollback

An anchor was taken before the change and is on the device at
`/home/volumio/rt-backups-20260922-143128/`, holding the original
`camilladsp-js.js` and a metadata file with the pre-change hashes. Restore is a file copy
plus a service restart, via the documented script.

---

## How this was verified

There is an acceptance harness (host-side; **not** deployed to the device) that checks the
grant by reading it back from the live system — unit ceiling, the process's live scheduler
attributes, and a set-difference over real-time threads that attributes anything new to a
cgroup and refuses to pass when it cannot read that attribution. Each snapshot row also
carries the process's start time, read at the same moment as the snapshot, and the check
re-reads it before attributing anything: a recycled process id would otherwise be judged on a
different process's cgroup and priority.

The harness was itself reviewed independently, and **that review is the interesting part of
this record**: four rounds were needed before it was trustworthy, because a verification
tool that is wrong is worse than no tool.

| round | verdict | what it caught |
|---|---|---|
| r4 | FAIL | stale callback path |
| r5 | APPROVE | — (the deployed files were approved here) |
| r6 | 2 blocking | freshness check truncated process start to whole seconds, so a pre-change process in the same second passed; cgroup attribution failed **open** |
| r7 | 1 blocking | the review brief cited evidence that was not inside the frozen review package, so claims could not be checked against frozen bytes |
| r8 | 3 blocking | the evidence chain was still not checkable from the frozen package; a byte-identity claim had no approval record behind it inside the package; and a process-id **reuse** race — the verifier read a process's cgroup and priority after taking its snapshot, so a recycled pid would be judged on a different process's state |
| r9 | see the receipt | final round: every r8 finding answered, the package made self-verifying, and the reuse race closed by binding every snapshot row to a process identity read at the same moment |

Notable corrections made along the way, recorded here because they generalise:

* **A reviewer-suggested fix that was a no-op.** r6 suggested comparing timestamps in tick
  units to avoid the truncation. That transform is algebraically identical to the truncated
  form, so it fixes nothing. The real fix was to compare against an exact sub-second install
  instant, and to answer **INDETERMINATE** rather than PASS for the ambiguous window.
* **A control that proved nothing.** One control arm attempted to synthesise a real-time
  process that then vanished; it did not fire in an early round, so the branch it targeted was
  covered only by fixtures. The r8 identity re-check made that path reachable on the device, and
  the arm now fires as intended — an accidental but genuine gain from fixing the reuse race.
* **A probe with no positive control.** An early reading of the DSP binary as "contains no
  scheduler symbols" was void — the tool output never demonstrated it could find anything at
  all. Re-run with a known-present control, it lists 417 symbols and lacks
  `pthread_create`: the DSP does not create threads directly, which is a real (and different)
  finding.
* **Evidence that had quietly gone stale.** One harness's default working directory was not
  updated when the staging layout changed, so it silently found nothing and reported a mass
  failure for the wrong reason — and the evidence file shipped from an older run still named a
  build that no longer existed. Both were caught while answering the review, and the run was
  redone against the deployed bytes.

Reviewer briefs, receipts and the raw evidence files are retained in the operational tree. The
review package is deliberately made **self-verifying**: it ships a checksum manifest keyed on file
names that is independent of the staging layout, plus a small read-only script that checks every
listed file is present, unmodified, and unaccompanied by anything unlisted.

---

## Residual uncertainty, stated plainly

1. **Worker-thread coverage is generation-dependent, and the cause is a defect rather than
   something inside the DSP.** One in five readings left the audio workers at normal priority
   after a respawn, for the whole stream. The *timing* is a race; the *mechanism* is the inert
   `-a` described above, which makes coverage depend on whether the helper lands before or after
   the DSP creates its worker threads. Not remediated: an argument reorder is identified, needs
   its own review, and is not deployed.
2. **The ≥ 352.8 kHz / DSD arm has now been run** — a DSD256 source, which this chain feeds to
   the DAC as 352.8 kHz PCM. Over a 30 s window the gate stayed `RUNNING`, the main thread held
   FIFO 45, nothing in the DSP exceeded 45, and the underrun/overrun delta was 0; a 44.1 kHz
   control in the same session was also 0. The DSP used 9% of one core at 352.8 kHz against 1%
   at 44.1 kHz.
3. **The benefit is measured as absence-of-failure**, on one device, at normal load. Nothing
   here is a claim about dropouts under conditions this session did not reproduce.
4. **The verifier is an endpoint snapshot, and it is narrow on purpose.** It compares the set of
   real-time threads before playback with the set at the end of the window; a thread that appears
   and disappears entirely inside that window would not be seen, and the checks say so rather than
   implying continuous monitoring. It is a deliberately conservative, system-wide regression alarm:
   an unrelated process that newly becomes real-time also fails it, even though such a process
   plainly did not get its priority from this change.

## Standing constraints honoured

* Control plane only through the documented SSH-carried transport; the plugin HTTP
  endpoints and `callMethod` were never used.
* The DSP's input FIFO was never written to by hand — it has two writers and a third would
  be a defect.
* Only the passwordless primitives already granted to the maintenance account were used;
  no new privilege was requested or created for this work.
* No media identifiers and no credentials appear in this record or its attachments.
