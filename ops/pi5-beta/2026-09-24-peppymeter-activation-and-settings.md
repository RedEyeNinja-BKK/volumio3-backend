# 2026-09-24 — pi5-beta: PeppyMeter 3.4.6 was deployed but never *running*; activation + settings

**Device:** `pi5-beta` (Raspberry Pi 5, Volumio 4.204)
**Scope:** user-interface plugin (PeppyMeter Screensaver) upgrade activation and two settings.
**Status:** APPLIED + VERIFIED

## Summary

The plugin tree was upgraded to `3.4.6` and passed its file-level acceptance (all deployed
files byte-identical to the reviewed tree, ALSA chain intact, guard state correct). It then
turned out that **none of the new plugin JavaScript had ever executed**: the backend process
predated the file replacement, and a plugin stop/start does not reload `index.js` from disk.
The operator-visible symptom was a tap on the meter bringing it straight back with no usable
window on the player controls.

| # | Change | State |
|---|---|---|
| 1 | Root cause identified: new plugin JS never loaded | **DIAGNOSED** |
| 2 | Backend restart so the on-disk plugin JS actually runs | **APPLIED + VERIFIED** |
| 3 | `timeout` 1 → 15, `persist_duration` 0 → 15 | **APPLIED + VERIFIED** |

## 1. Root cause — deployed ≠ running

Volumio's plugin manager loads a plugin's `index.js` when the plugin is loaded, and
`stopPlugin()` / `startPlugin()` call only `onStop` / `onStart` on the **already-loaded module
object**. They do not re-`require()` the file. The backend had been running since ~10 minutes
*before* the new tree was placed, so the previous version's code kept executing in memory
indefinitely; the new meter process (read fresh from disk on each launch) was writing a
dismissal marker that no loaded code ever read.

Three independent observations:

| Observation | Result |
|---|---|
| backend process start vs file placement | backend start **precedes** the file replacement |
| previous version's `index.js` | **zero** references to the dismissal marker path; new version references it five times |
| a feature present only in the new version | absent from live runtime state (no tag applied, no related log line) |

Alternatives that were tested and **falsified**, rather than assumed:

* **namespace split on `/tmp`** — ruled out: the unit is not private-`/tmp`, and the marker
  file showed the same inode in the host view and in the plugin process's view.
* **sub-second ordering race** — ruled out: the marker's mtime preceded the plugin's exit
  callback timestamp by 0.68 s (sub-second journal timestamps).
* **late write** — ruled out: a marker planted *in advance*, followed by a clean meter exit
  with no user interaction, was still not read and not consumed. The reading code was absent,
  not late.

An earlier internal note had attributed the missing behaviour to the previous 1-second
`timeout` "starving" the marker out of the window. That attribution was **wrong** and is
corrected here: the clearing line sits in the plugin's one-time start path, not in a
per-second path, and the real cause was that the code containing the feature was not loaded.

## 2. Activation

`systemctl restart` of the Volumio backend service, so the plugin is loaded from the on-disk
tree and the configuration file is re-read.

Verified by a discriminator that only the new code can satisfy: a marker file planted
immediately *before* the restart was **absent** afterwards, cleared by the new version's
one-time start path — the previous version contains no reference to that path at all. A real
tap then produced the first dismissal/re-arm record this device has ever logged, and the
meter stayed dismissed for the full window.

## 3. Settings

`timeout` and `persist_duration` were both set to `15`. The same `timeout` value serves two
purposes — the delay before the meter first appears after playback starts, and the window the
meter stays away after a dismissal — so the operator chose a single middle value.

Applied by writing exactly those two keys in the plugin configuration file (asserted: no
other key changed, backup retained) followed by a backend restart, because **a plugin
stop/start does not re-read the configuration**: `loadFile` appears only in the plugin's
start-up path and in the backup-restore method, not in `onStart`.

| Observation | Result |
|---|---|
| configuration file | `timeout=15`, `persist_duration=15` |
| meter first appearance after playback starts | **15 s** (previously ~60 s with the old value, ~1 s with the value before that) |
| ALSA chain after the restart | full chain defined, hash unchanged |
| plugin status | STARTED |

## Operational hazard (recorded for future work)

A plugin stop/start — and a backend restart — **bounces MPD and clears the play queue**. Any
future plugin or settings change must be done with playback stopped and the operator's queue
restored afterwards, or scheduled for a moment when the player is idle.

## Hypothesis (not proven)

* The `persist_duration` value is loaded by the same `loadFile` mechanism as `timeout`, so it
  is expected to hold its display for 15 s after a genuine stop. That specific behaviour has
  not been measured; the `timeout` value was measured directly.
* A backend restart also resets the on-screen player application's page state, which can leave
  the display showing a stale view until it reconnects.

## Attempted and reverted

Nothing in this change-set. (The upgrade work that this record activates — including an ALSA
template correction and a theme-tree guard — was reviewed and accepted separately before this
record; nothing was reverted.)

## Open

* Cold-boot behaviour of the activated plugin has not been measured; the change-set was
  verified across a service restart.
* Two one-shot diagnostic scripts used here are not published with this record because they
  contain operator-specific values; the equivalent methods are described in the text.

## Rollback

* **Settings:** restore the retained backup of the plugin configuration file
  (`/data/configuration/user_interface/peppy_screensaver/` — backup copy kept in the device
  temp directory as `config.json.bak-pre-1515`) and restart the backend service.
* **Activation:** there is nothing to roll back — a backend restart only loads what is already
  deployed on disk. Reverting to the previous plugin version is the rollback of the upgrade
  itself, whose preview tree and rollback bundle remain on the device, unused and intact.
