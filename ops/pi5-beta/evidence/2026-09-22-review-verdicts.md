# 2026-09-22 — review verdicts for the real-time scheduling change-set

Two independent things are recorded per round, and they answer different questions:

- **reviewer verdict** — what the reviewer concluded about the artifacts.
- **window verdict** — whether the review's *own* read-only window stayed clean, computed by the
  review guard from pre/post fingerprints of every reviewed artifact. A round whose window did not
  stay clean is **not citable**, whatever the reviewer found: the bytes the reviewer read are not
  provably the bytes that were shipped.

A round is citable only when its window verdict is `CLEAN-READONLY`.

| round | reviewer verdict | window verdict | note |
|---|---|---|---|
| r1 | — | INVALID | artifact mutated inside the review window |
| r2 | — | CLEAN-READONLY | |
| r3 | — | INVALID | artifact mutated inside the review window |
| r4 | FAIL | CLEAN-READONLY | stale callback path |
| r5 | APPROVE | CLEAN-READONLY | the first deployed files were approved here |
| r6 | 2 blocking | INVALID | freshness check truncated to whole seconds; attribution failed open |
| r7 | 1 blocking | INVALID | brief cited evidence outside the frozen package |
| r8 | 3 blocking | INVALID | evidence chain not checkable; a pid-reuse race |
| r9 | final | CLEAN-READONLY | every r8 finding answered; package made self-verifying |
| r10 | deployed-bytes round | CLEAN-READONLY | **not citable for the verifier** — see below |
| r11 | APPROVE-WITH-FINDINGS | CLEAN-READONLY | one actionable finding on the harness controls |
| r12 | APPROVE-WITH-FINDINGS, no blocking | CLEAN-READONLY | confirmed the r11 finding fixed; re-audited the deployables |

## Why the r10 envelope is citable for the deployment but not for the verifier

The r10 review window closed before the verifier's own arithmetic fail-open defect was found and
repaired (see the change record). The deployment it covers — the `camilladsp-js.js` and
`10-audio-rt.conf` bytes then installed — is unchanged by that repair and remains covered. The
verifier is not: its current bytes were reviewed in r11/r12, not r10.

## What r12 checked, by its own account

- Both harness controls run to completion **from the frozen, read-only package copy**, invoked as
  `bash <script>`: 7/7 control assertions with the device guard exercised, and 10/10 across the four
  fixture-resolution arms.
- The integrity gate over the frozen package reports **62 listed entries, 62 byte-identical, 0
  missing, 0 hash mismatches, 0 unexpected files**.
- No remaining silent-vacuous pass in the reviewed controls; the device-side arms require the
  verifier's own progress marker before they act, and report `UNEXPECTED` if it never appears.
- `verify.sh` differs from the frozen predecessor by **exactly one hunk**, which closes a failure
  mode rather than weakening an assertion.
- Non-blocking: a release gate that requires untouched-device coverage should hard-fail or require a
  separately recorded device-capable run rather than accept an automatic skip. Satisfied here by the
  device-capable control run, which reports the guard as exercised rather than skipped.

Full receipts and reviewer reports are retained in the internal operational tree; the verdicts,
labels and run identifiers above are the publicly relevant extract.
