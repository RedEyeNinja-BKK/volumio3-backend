# 2026-09-22 — Real-time scheduling for the CamillaDSP backend (and a governor A/B)

> **Revision 2 of this record** (supersedes the version first written on deployment day).
> Sanitised: no credentials, no personal paths, no internal addresses, no media identifiers.
> Host-side paths are shown as `[workstream]/…`.

**Outcome: the `camilladsp` backend now actually gets real-time priority on every thread it
creates, and the CPU governor is left on `conservative` (measured, not assumed).** Two changes
were authorised as one task — (a) an A/B of the CPU governor, (b) a real-time priority grant for
the audio DSP — and were executed in that order.

Scope note: this is a **scheduling-authority** change, not a latency-tuning change. Nothing here
alters resampling, buffers, or the audio path; the device still does no resampling.

| | file | sha256 (deployed) |
|---|---|---|
| permission | `/etc/systemd/system/volumio.service.d/10-audio-rt.conf` | `3db49e30892f99fde79e03cfa854cc371fa457835c1366aa055b145536d3c958` |
| behaviour | `/data/plugins/audio_interface/fusiondsp/camilladsp-js.js` | `10236af890a92cb37b873bb5c7b10596c90e65e0d258baeb08474c3ec891b429` |

---

## (a) CPU governor — A/B/A, verdict: **keep `conservative`**

Tested atomically inside **one** continuous 44.1 kHz stream (A→B→A), with the governor switched
between arms and the tick/transition counters read from the kernel. A first attempt that ran the
arms in separate sessions collected an idle machine for two of its windows; those windows were
recorded as **VOID** and discarded rather than reported.

| arm | governor | observed clock | transitions | underruns |
|---|---|---|---|---|
| A | `conservative` | held 2800 MHz, 3518/3518 ticks | **0** | 0 |
| B | `schedutil` | dipped to 2.5–2.6 GHz for ~46 % of the window | **4082** | 0 |
| A′ | `conservative` | held 2800 MHz, 3507/3507 ticks | 0 | 0 |

`schedutil` produced over four thousand governor transitions in 35 s where `conservative` produced
none, while pulling the clock *down* for half the window — it reduces DSP headroom as well as
adding churn. **`conservative` was already the value on the device and is left there.**

The premise being tested was my own inference that `conservative` costs latency: a long-run idle
`time_in_state` showed the device sitting at the 1500 MHz floor 96.6 % of wall-clock, and the
8 ms poll with a ~140 MHz step implies a slow-looking ramp. Under real playback that inference is
simply wrong — the clock is pinned at the ceiling. The floor-clinging distribution is an **idle**
phenomenon.

Not covered by this A/B: the device's ≥ 352.8 kHz arm (see the real-time section — that was
measured in a separate window).

## (b) Real-time priority for the CamillaDSP backend

### What was wrong

The audio DSP ran at normal scheduling priority. Under load it competes for CPU with everything
else on the box, including a kiosk browser and the Node backend, and the failure mode of losing
that competition is an audio dropout.

Investigating where the priority should come from turned up an adjacent fact about the platform:
Volumio's own boot-time CPU script (`/usr/bin/volumio_cpu_tweak`) intends to raise `mpd` to FIFO 35
and pin its affinity, but it resolves the mpd pid at a point where mpd is not yet running, so both
of its scheduling calls silently degrade into queries that exit 0. Measured on the device: `mpd`
runs `SCHED_OTHER`, and **the one process in this audio chain that actually holds a deadline —
`camilladsp` — received no real-time policy and no affinity from the platform at all.**

### What was changed

| File | Role |
|---|---|
| `10-audio-rt.conf` | grants the service a real-time priority **ceiling** of 45 |
| `camilladsp-js.js` | makes the DSP obtain priority up to that ceiling |

The drop-in is deliberately a **ceiling, not a pin**: it says what the unit *may* use, and the
process decides per-thread. That follows the upstream design — the deployed `camilladsp` 4.1.3
bundles the `audio_thread_priority` crate and contains its own promote/demote paths. The
unit-level ceiling is what makes that existing mechanism able to succeed.

**Why 45:** above mpd's two FIFO-40 device threads (the consumer must be able to preempt the
producer), and below every kernel thread measured on this board — `migration/*` 99, `ntpd` 99,
`watchdogd` 50, `irq/*` 50, `card0-crtc0` 50.

### The defect that the first deploy had — and that the current one fixes

`-a`/`--all-tasks` in util-linux is **position-sensitive.** Written with the priority *before* it,
the flag is accepted and **silently ignored** — `rc=0`, no error, no warning. Measured on this
device (util-linux 2.38.1) against a four-thread test process, three forms:

```sh
chrt -f -p 45 -a <pid>      # INERT: rc=0, main thread only, workers left SCHED_OTHER
chrt -f -p -a 35 <pid>      # correct: all four threads promoted
chrt -a -f -p 45 <pid>      # correct: all four threads promoted (the form deployed here)
```

The discriminator is narrower than “`-a` must come before `-p`”: `-a` must come before
**`-p`'s priority argument**. It may follow `-p` itself and still be honoured — so the earlier
wording of this record was too strong, and is corrected here. Only the first form above is
broken, and it is broken in the worst way: it succeeds, and covers one thread instead of all of
them. **Do not reorder these flags**, and do not “tidy” the priority to the front.

The first deployment shipped the inert form. Measured consequence: the DSP's main thread was
FIFO 45 in every reading, but in **4 of 5 successive spawns** the audio workers (`AlsaPlayback`,
`FileCapture`) were left on `SCHED_OTHER` — they are created *after* the grant and were covered
only by inheritance from the main thread, which is a race against when the grant lands. The same
item produced both outcomes across successive respawns, so it was not a property of the sample
rate.

The current deployment reorders the flags. This is a one-line change plus the comment explaining
why the order may not be "tidied", and it is what the measured results below reflect.

### Measured behaviour after the fix

- **Every thread of the process is FIFO 45**, including `AlsaPlayback` and `FileCapture` — asserted,
  not inferred, in 10/10 threads of a live stream.
- Nothing on the box sits above 45 except kernel threads; nothing in the DSP exceeds 45.
- No newly real-time thread is attributable to this grant other than `camilladsp` itself.
- CPU cost is negligible: the DSP used **49–53 jiffies over 35 s ≈ 1.4 % of one core** at 44.1 kHz,
  and **0 jiffies when idle**. An idle RT process cannot starve anything, which matters for the
  attribution in the post-change section below.
- The ≥ 352.8 kHz arm was run: a DSD source, which this chain feeds to the DAC as 352.8 kHz PCM.
  Over a 30 s gated window the main thread held FIFO 45, nothing exceeded 45, and the
  underrun/overrun delta was 0; a 44.1 kHz control in the same session was also 0. The DSP used
  ~9 % of one core at 352.8 kHz against ~1 % at 44.1 kHz.

### Rollback

An anchor is on the device at `/home/volumio/rt-backups-20260922-173541/`, holding the original
`camilladsp-js.js` and a metadata file with the pre-change owner, mode and hash. Restore is a file
copy plus a service restart, driven by the documented script; the transaction record is verified
before the anchor is relied on.

---

## How this was verified

There is an acceptance harness (host-side; **not** deployed to the device) that checks the grant by
reading it back from the live system — unit ceiling, the process's live scheduler attributes, and a
set-difference over real-time threads that attributes anything new to a cgroup and refuses to pass
when it cannot read that attribution. Each snapshot row also carries the process's start time, read
at the same moment as the snapshot, and the check re-reads it before attributing anything: a
recycled process id would otherwise be judged on a different process's cgroup and priority.

The harness was itself reviewed independently, and **that review is the interesting part of this
record**: a verification tool that is wrong is worse than no tool.

| round | reviewer verdict | what it caught |
|---|---|---|
| r4 | FAIL | stale callback path |
| r5 | APPROVE | the first deployed files were approved here |
| r6 | 2 blocking | freshness check truncated process start to whole seconds, so a pre-change process in the same second passed; cgroup attribution failed **open** |
| r7 | 1 blocking | the brief cited evidence that was not inside the frozen review package, so claims could not be checked against frozen bytes |
| r8 | 3 blocking | evidence chain still not checkable from the frozen package; a byte-identity claim had no approval record behind it; and a process-id **reuse** race |
| r9 | final round | every r8 finding answered, package made self-verifying, reuse race closed by binding each snapshot row to an identity read at the same moment |
| r10 | deployed-bytes round | covers the currently deployed artifact |
| r11 | APPROVE-WITH-FINDINGS | one actionable finding on the harness controls (see below) |
| r12 | APPROVE-WITH-FINDINGS, **no blocking** | confirmed the r11 finding fixed, and re-audited the deployables |

The review package is deliberately made **self-verifying**: it ships a checksum manifest keyed on
file names that is independent of the staging layout, and a small read-only script that checks every
listed file is present, unmodified, and unaccompanied by anything unlisted. The reviewer runs that
script **inside the frozen copy**, not against the authoring tree — a distinction that cost one
whole abandoned review package before it was learned.

### The verifier's own fail-open defect — found after deployment, repaired, re-reviewed

The most valuable single finding of the day came from re-running the verifier *on the device* after
deploying. It printed a PASS and, underneath, two shell arithmetic errors. They were not noise:

```sh
u1="$(grep -ciE 'underrun|overrun' $LOG 2>/dev/null || echo 0)"
[ "$(( u2 - u1 ))" -eq 0 ] || fail "..."
```

`grep -c` prints its count **and exits 1** when the count is zero, so `|| echo 0` appended a
*second* line. A two-line value inside an arithmetic expansion is a syntax error, which aborts the
whole command — **including its own `|| fail` guard** — and at top level the script then continued,
printed `VERIFY COMPLETE`, and exited 0.

So the guard fired exactly when the count was 0, i.e. **on the good result**, and the observable
outcome was a silent pass. It had never been seen because the log had always carried 11 matching
lines from earlier sessions, which made the arithmetic work.

Repaired: `|| true` (which keeps grep's own single-line `0`), plus the file's existing numeric
validation convention, so a malformed or backwards reading is now **INDETERMINATE** rather than a
silently-resolved default. Scope of the repair: exactly **one hunk** at the sustained-load section;
the first 442 lines are byte-identical to the previously frozen verifier. Re-measured on the device
afterwards: `0 -> 0`, asserted, no arithmetic errors, exit 0.

The round-10 receipt is therefore **not citable for the verifier** — its window closed before this
repair. It remains citable for the deployed artifact.

### Instrument defects found and fixed the same day

A pattern worth naming: **all three of these were in my own tooling, and every one of them was found
by running the instrument rather than reading it.**

* **Clock-driven test arms.** Two device-side test arms injected their perturbation on a fixed
  13 s delay tuned against an earlier, slower verifier. A later consolidation made the verifier
  faster, so on re-run the delay landed *after* the final snapshot: one arm reported a wrong result,
  and the other — which expects success — reported a **pass having exercised nothing**. Both are now
  **marker-driven**: they wait for the specific line the verifier prints immediately after taking its
  baseline, and a missing marker is reported as UNEXPECTED rather than as a result. The
  pass-expecting arm additionally asserts that its target branch actually ran.
* **A fixture search that accepted a file by name.** The harness took the first *existing* candidate
  path, so a stray file of the same name could have been anchored against while the harness reported
  its preconditions satisfied. Every candidate is now checked against the expected hash and skipped
  unless it matches, with a standalone negative control covering four resolution arms (10/10).
* **An accidental dependency on the executable bit.** The guard's freeze writes every reviewed
  artifact mode `0444`. The author's own copy had been made with `cp -p`, preserving `0755`, so the
  package ran for the author and not for the reviewer. Now: `-f` rather than `-x` preconditions,
  invocation as `bash <script>`, and a permission repair on any scratch copy that will be mutated.

## Post-change observation: a Spotify playback failure, attributed upstream

Shortly after deployment the operator reported a specific symptom: start a local DSD track, then
start a Spotify track from the same UI — silence; clicking the same Spotify track a second time
played it. Given a change to the audio scheduler had landed an hour earlier, this was treated as a
possible regression and investigated to attribution rather than waved away.

**It is not attributable to this change.** go-librespot's own log at the moment of the silence:

```
level=error msg="failed handling request play"
  error="failed loading context: ... failed creating stream for <track>:
  failed resolving track storage: failed reading response body:
  stream error: stream ID 9; INTERNAL_ERROR; received from peer"
```

That is Spotify's server aborting the HTTP/2 stream while the client was fetching the track's audio
payload — it occurs **before** any local audio device, FIFO or DSP interaction. The plugin surfaced
it as an HTTP 500 from the local playback API and then did nothing; the retry two seconds later
succeeded with identical parameters, which is the signature of a probabilistic remote failure rather
than a deterministic local defect.

Corroborating evidence, all measured:

* The error appears **once** in the entire journal, which spans 21 hours *before* the change as well
  as after.
* The device has a documented, **pre-existing** background of Spotify connectivity failures — seven
  clusters of Websocket/access-point errors between the previous evening and the change, including
  two dial failures to a Spotify access point. The stream abort is the same family.
* Four `Too many requests` warnings from the Spotify Web API two seconds before the failure (the
  plugin had just fetched a long playlist page and was throttled), which is a plausible aggravating
  factor on the same request.
* Local scheduling is **excluded as a mechanism**: the failure string originates inside
  go-librespot's HTTP/2 client; go-librespot runs `SCHED_OTHER` and is unaffected by the DSP's
  policy; and an idle DSP consumes 0 CPU ticks, so it cannot starve another process.
* It is **not** the known two-writer audio-path defect either: only one player was active, and the
  DSP had already been torn down cleanly (`exit code 0`) before the Spotify request.

Residual uncertainty, stated: the deployment does restart the core service, and therefore restarts
the Spotify daemon, so a freshly-established connection was in play. The failure shape is remote-side
and the immediate retry succeeded, so this is recorded as a coincidence in time rather than a
mechanism — but a single occurrence cannot establish a rate, and recurrence would be the thing to
measure before calling the class closed.

The actionable local improvement here is not in the scheduler: the plugin treats a failed playback
request as silence, with no user-visible error and no single retry. One retry would have made the
first click work. That is a plugin-side change, is not part of this revision, and is not made here.

---

## Residual uncertainty, stated plainly

1. **The benefit is measured as absence-of-failure**, on one device, at normal load. Nothing here is
   a claim about dropouts under conditions this session did not reproduce. There were zero
   underrun/overrun events across every gated window measured, and the pre-existing events in the log
   did not recur — but that is a negative result, not a demonstrated drop-out fix.
2. **The verifier is an endpoint snapshot, and it is narrow on purpose.** It compares the set of
   real-time threads before playback with the set at the end of the window; a thread that appears and
   disappears entirely *inside* that window would not be seen. The script states this limitation in
   its own output rather than implying continuous monitoring — so the property it asserts is the
   two-snapshot endpoint property, and it does not claim continuous absence.
3. **The regression alarm is system-wide, not targeted.** An unrelated process that newly becomes
   real-time also fails the check, even though such a process plainly did not get its priority from
   this change. That is deliberate (a wider net is safer for a regression alarm) and it is why a
   failure has to be attributed rather than assumed.
4. **Volumio's own `mpd` scheduling grant remains inert.** The platform's boot script still fails to
   promote mpd or set its affinity, and its scheduling calls still degrade silently to queries. That
   is a separate, upstream-shaped change to an OS file; it is not made here.
5. **The ≥ 352.8 kHz arm still carries an asterisk.** It passed, and the DSP cost scales as expected,
   but this device has an open, intermittent hardware-path wedge associated with rate changes that
   only a cold start clears; that is unrelated to scheduling and remains under separate
   investigation.

## Reviewer briefs, receipts and evidence

Reviewer briefs, review receipts, frozen review packages and raw evidence files are retained in the
operational tree. Two process facts are recorded with them because they are the reason the evidence
is trustworthy: **a review receipt is citable only when its window closed clean**, and several early
rounds have receipts that read INVALID for that reason — the reviewer's findings in those rounds are
useful history, but the rounds themselves cannot be cited. Each frozen package is accompanied by the
checksum manifest described above.

## Standing constraints honoured

* Control plane only through the documented SSH-carried transport; the plugin HTTP endpoints and
  `callMethod` were never used.
* The DSP's input FIFO was never written to by hand — it has two writers and a third would be a
  defect.
* Only the passwordless primitives already granted to the maintenance account were used; no new
  privilege was requested or created for this work.
* Every runtime change was read back from the system after being written; every test arm left the
  device as found (stopped, gate closed, governor unchanged, playback queue intact).
* No media identifiers and no credentials appear in this record or its attachments.
