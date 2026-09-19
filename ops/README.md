# ops/ — local operations records

This fork is the **coordination and documentation point** for changes we make to the
Volumio devices in our stack. Upstream code is tracked unchanged on `master`; every
change we apply to a live device is recorded here.

## Conventions

- One directory per device (`pi5-beta/`).
- One dated record per change-set: `YYYY-MM-DD-<topic>.md`.
- **Live artifacts are committed byte-for-byte** (systemd drop-ins, scripts), so this
  repository — not a shell history — is the source of truth for what is deployed.
- Every record states, explicitly and separately:
  - what is **verified** (with the observation that verified it),
  - what is **hypothesis** (not yet proven),
  - what was **attempted and reverted**, with the reason,
  - what remains **open**.
- Rollback paths are recorded for every change.
- Nothing here is proposed upstream without a separate, explicit decision.

## Scope and safety

This fork is **public** (it is a fork of a public upstream). Therefore these records
must never contain:

- credentials, API keys or tokens,
- internal IP addresses or network topology,
- device configuration values that are secrets,
- personal file paths (media library locations, home directories),
- specific media titles, artists or albums from a personal library.

Device names/aliases are fine.

## Tooling safety — the `gh` default repository

Our working clone has **two** remotes: `origin` (our fork) and `upstream` (the official
project, kept for fetching). Because both are present, `gh` had resolved its *default*
repository to **upstream**, not our fork:

```
$ gh repo view --json nameWithOwner,isFork
{"isFork":false,"repo":"volumio/volumio3-backend"}      # <-- upstream!
```

Consequence: a bare `gh pr view 1` in that clone read **upstream's** PR #1, not ours.
`git` was never at risk — `upstream`'s push URL is set to the literal string `no_push`,
and a dry-run push fails hard — but **any `gh` write** (comment, issue, PR edit) would
have landed on the official repository.

Fixed with, in the clone:

```sh
gh repo set-default RedEyeNinja-BKK/volumio3-backend
```

**Rule:** in this clone, either rely on the pinned default *and* pass `--repo
RedEyeNinja-BKK/volumio3-backend` explicitly on any write, or verify the target with
`gh repo view --json nameWithOwner` immediately before a write. Re-check after any
re-clone: `gh repo set-default` is stored in the clone's own `.git/config`.

## Diagnostic scripts

Read-only diagnostic and evidence-capture scripts live in `scripts/` and are committed
byte-for-byte alongside the records, so the measurement that produced a finding can be
re-run identically later. A script that writes anything beyond its own output file must
say so in its header and carry a rollback note in the corresponding record.
