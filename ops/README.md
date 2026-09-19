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
- device configuration values that are secrets.

Device names/aliases are fine.
