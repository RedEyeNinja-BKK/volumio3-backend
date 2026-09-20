# 2026-09-20 — PeppyMeter upstream fix: robust Volumio architecture detection

Device: `volumio-pi5-beta` (Raspberry Pi 5, Volumio 4.204, aarch64 kernel / armhf userland)
Repo under contribution: `foonerd/peppy_screensaver` (Volumio 4 plugin, MIT, single maintainer)
Our fork: `RedEyeNinja-BKK/peppy_screensaver`

**Status: patch prepared and reviewed (three rounds, final verdict APPROVE); NOT deployed. The
branch is published on our fork (`fix/arch-detection-fallback`, `eff0f419`, 2026-09-21) so the diff
can be inspected; the upstream pull request is still NOT opened, and remains a separate explicit
decision. No device change was made in preparing it.**

Provenance labels per `2026-09-19-i2s-dac-regression-and-revert.md`: **measured** = we measured it,
**observed** = operator reported/confirmed, **claimed** = third-party.

---

## 1. What happened

During the 2026-09-20 OS package update on this device (recorded separately in
`2026-09-20-bootup-review-os-update.md`), `apt` upgraded `base-files`. That package ships
`/usr/lib/os-release`, and on Volumio 4 the root filesystem is an overlay whose writable upper
layer shadows the base image. The upgrade therefore replaced Volumio's `os-release` — the one
carrying every `VOLUMIO_*` key — with the stock Raspbian file.

Consequences on the device:

* Music playback itself was unaffected (the I²S/DAC path does not use these variables). **measured**
* The PeppyMeter screensaver stopped starting: repeated
  `error: peppy_screensaver: Error start PeppyMeter: Error: Command failed: .../run_peppymeter.sh`
  with the launcher itself logging `ERROR: Could not detect Volumio architecture`. **measured**
* It stayed broken until Volumio's own `os-release` was restored by hand from the base layer
  (`cp /static/usr/lib/os-release /usr/lib/os-release`). **measured**

So: a distro package upgrade silently disabled a plugin, with no diagnostic in the plugin's own
logs beyond "could not detect architecture".

## 2. The upstream defect

Eight places across three files read the architecture from `/etc/os-release`, each with **no
fallback at all**:

| file | count | behaviour when `VOLUMIO_ARCH` is absent |
|---|---|---|
| `install.sh` | 1 | hard error `Could not detect Volumio architecture`, install aborts |
| `run_peppymeter.sh` | 1 | hard error, screensaver never starts |
| `index.js` | 6 | `get_SDL2_enabled` builds `.../lib//python` and reports pygame missing; the five `isX64` checks silently take the non-x64 branch |

The five `isX64` sites are harmless on a Pi (non-x64 is the correct branch there anyway) but wrong
on x64: no `snd-aloop` for the Spotify meter path, the standard rather than x64 ALSA template, and
MPD output 1 not enabled. `index.js:4522` (as read at the time) was the one genuinely broken
everywhere, and unlike the others it was not even inside a `try/catch`. **measured against source**

### The trap that makes the obvious fix wrong

This device reports `uname -m` = `aarch64` because its **kernel** is 64-bit, while its **userland**
is 32-bit (`arm_64bit=0`, `getconf LONG_BIT` = 32, `readelf` class = ELF32,
`dpkg --print-architecture` = armhf). Volumio's canonical value is `VOLUMIO_ARCH="arm"`. **measured**

A fallback based on `uname -m` alone selects `armv8`, whose libraries provably fail to load on this
device with `wrong ELF class: ELF64`. Any correct fallback must key off the userspace ABI. **measured**

## 3. The fix

One implementation, `resolve-arch.sh`, new and shared by all three files. It:

1. reads `VOLUMIO_ARCH` from `/etc/os-release`, then `/static/usr/lib/os-release` (the Volumio
   base-layer copy — the very file that fixed the device by hand), then `/usr/lib/os-release`;
2. parses the value exactly and validates it against the supported set
   `arm | armv7 | armv8 | x64`, so an unsupported or malformed value falls through instead of
   producing a `lib/<garbage>` path;
3. only then derives the value from the userspace ABI — `getconf LONG_BIT` plus `uname -m` — and
   prints nothing if it still cannot decide, leaving each caller's existing hard error intact.

Every 32-bit ARM userland maps to `arm` (Volumio's own value for these devices), never `armv7`.

Consumers: `install.sh` and `run_peppymeter.sh` call it; `index.js` gained a cached
`resolveVolumioArch()` helper that validates the same id set at the JS boundary and is used by all
six former call sites. Zero raw `VOLUMIO_ARCH` reads remain. A fallback is never silent — if
`/etc/os-release` did not supply the value the caller logs a warning naming the value used.

The helper caches only successful resolutions, so a transient read failure cannot poison the value
for the life of the plugin process.

## 4. Verified

Device proof. The three files this change-set modifies are byte-identical to upstream 3.4.5 on the
device, and `resolve-arch.sh` is not installed there. **measured**, re-checked when this record was
committed:

```
cd /data/plugins/user_interface/peppy_screensaver
git diff --exit-code -- index.js install.sh run_peppymeter.sh    -> no output, rc 0
find . -name resolve-arch.sh                                     -> no match
```

Corroboration from the device's own installation. The plugin's `lib/` tree holds `arm/`, `armv7/`,
`armv8/` and `x64/`, but only `arm/` is populated (`libpeppyalsa.so`, plus 831 files under
`lib/arm/python/`); `armv7/`, `armv8/` and `x64/` are empty. Upstream's own installer therefore
resolved this device to `arm` — the same answer this patch's resolver returns for it. **measured**

Caveat — the plugin directory is **not** a clean checkout, and has not been since it was installed.
`git status` there lists the tracked `packages/*/peppy-python-packages.tar.gz`, `volumio_peppymeter/`,
`templates/` and `templates_spectrum/` trees as deleted, and `lib/arm/python/`, `lib/libpeppyalsa.so`,
`node_modules/`, `screensaver/` and `theme-gallery/` as untracked. That is **normal post-install
state, unrelated to this change-set**: Volumio installs a plugin by cloning upstream (`git reflog`:
`clone: from https://github.com/foonerd/peppy_screensaver.git`, 2026-09-19 02:08) and then consuming
and relocating files. Everything in that diff is dated 2026-09-19 02:08–11:05, before this session.
An earlier revision of this record asserted the directory was "clean"; that was wrong — see the
corrections in section 9.

```
uname -m = aarch64   getconf LONG_BIT = 32   canonical VOLUMIO_ARCH = "arm"      (measured)
  lib/arm and bin/arm both exist                                                 (measured)

A) old idiom, stock os-release      -> []      <- empty; launcher exits 1
B) resolve-arch.sh, same file       -> [arm]   <- the fix
C) resolve-arch.sh, real os-release -> [arm]
D) empty, then bogus, then stock    -> [arm]
E) aarch64 + 32-bit, negative control -> arm (never armv8)
```

Test suite: `bash test-arch-detection.sh` — **29/29 pass**. Covers quoted, unquoted, single-quoted
and CRLF values, a decoy key (`VOLUMIO_ARCH_SUFFIX`), empty/unreadable/unsupported candidates, every
supported platform, the 64-bit-kernel/32-bit-userland case, the unknown-platform empty result, the
single-line output contract, and integration assertions so no consumer can drift back to reading
`/etc/os-release` directly. `node --check index.js` and `bash -n` on all four scripts pass.

Artefacts, commit `eff0f41977640e6a836c0794a94d4668b3bf788f`, base upstream `main` `efbd0ad`:

```
3b74ec3ccadaf8c6ce608a64a29bb24fc699672f8e80924937b7f82974270b37  resolve-arch.sh
e529c47b18e892517f4e29152aaffbe0511bd3354b80fb96dd24e662b3c293fb  index.js
34bb692644858b9b69188449ed14d7c21386463262dccf47b97fc1d0003d23e3  run_peppymeter.sh
038faf0071e20ab97e628cc2d33e93618355279b6769f7536dd1a5dd32fc1a1a  install.sh
f160b36c5a881e96cda3ac8d0a2fbcc1d0143c8c100880a1e51992f2ea153574  test-arch-detection.sh
```

## 5. Hypothesis, and what was attempted and reverted

* **Hypothesis (not proven):** the same `base-files` shadowing would equally break any other
  consumer of `VOLUMIO_*` on any Volumio 4 device with this overlay layout. Inferred from the
  overlay design plus the observed file replacement; not tested on other devices.
* **Attempted and reverted during preparation:** the first version of the patch duplicated the
  resolver into both shell scripts and left `index.js` untouched. Reverted after review showed the
  duplication was unnecessary once a shared script existed, and that leaving `index.js` made the
  fix incoherent. The first JS helper also cached `''` on failure, which would have kept the plugin
  broken until restart; changed to cache successes only.

## 6. Review history

Independent review through the sanctioned Hermes lane (`switchyard-smart-bounded-hermes`), three
rounds, each tied to frozen artefact hashes:

* **Round 1 — APPROVE-WITH-FINDINGS**, 2 blocking. Blocking: (a) map 32-bit ARM to `arm`, not
  `armv7`; (b) leaving `index.js` unconverted makes the fix incoherent. Also: the test's `awk`
  extraction of the function did not justify a "cannot drift" claim; value parsing was too
  permissive; the default candidate list was never exercised.
* **Round 2 — APPROVE-WITH-FINDINGS**, no blocking. Round 1 blocking items confirmed remediated;
  all hashes matched; 26/26 tests passed. New MAJOR: the JS boundary accepted raw script output
  without validating it.
* **Round 3 — APPROVE**, no findings. All five hashes matched; 29/29 tests passed. Reviewer:
  *"This commit is ready to push to your fork and open as an upstream pull request."*

Round 2 also raised a deliberately unfixed MINOR: `LD_LIBRARY_PATH`/`PYTHONPATH` are appended with
a trailing colon when previously unset. Pre-existing, unrelated to architecture resolution, and
left out of this patch on purpose rather than silently.

## 7. Open

* Opening the upstream pull request — **awaiting an explicit decision**. The branch itself is
  already published on our fork on 2026-09-21, verified by re-cloning it from GitHub: all five files
  are byte-identical to the reviewed round-3 hashes above, and the bundled test suite passes 29/29
  in the pushed copy. Publishing the branch does not notify the maintainer; opening the pull request
  does.
* The Volumio-platform root cause — a distro upgrade shadowing the base-layer `/usr/lib/os-release`
  and silently dropping every `VOLUMIO_*` key — affects far more than this plugin (Volumio's own
  `pluginmanager.js`, `webradio` and `system` plugin read the same file). Candidate for a separate
  report to Volumio rather than a plugin patch.
* `VARIANT` and `HARDWARE` are read from `/etc/os-release` with the same fragility, in both
  `install.sh` and `index.js`. Not part of this patch.
* The trailing-colon path issue noted above.

## 8. Rollback

Nothing to roll back on the device: this change-set touches no live device file. `resolve-arch.sh`
was copied to `/tmp`, executed there, and deleted afterwards; it was never installed into the plugin
directory, and the three files this patch modifies are unaltered on the device.

If the patch is later deployed to `volumio-pi5-beta`, roll back by restoring the three original
files from the fork at upstream `main` (`efbd0ad`) and deleting `resolve-arch.sh`.
`resolve-arch.sh` must land together with the scripts that call it, never alone — deploying it by
itself is harmless, but deploying a caller without it reintroduces the hard error.

## 9. Corrections to this record

* **An earlier revision of this record claimed the plugin directory was clean.** It said the live
  plugin "was never modified, confirmed with `git status` on the plugin directory", and that "the
  plugin directory is clean". Both were false. `git status` in the plugin directory reports a large
  deleted/untracked diff; all of it is dated 2026-09-19 02:08–11:05 and is caused by the ordinary
  plugin installation procedure, not by this work. The claim those sentences were meant to support —
  that this change-set modified nothing on the device — is true, and is now stated precisely and
  re-measured in section 4. Corrected 2026-09-21.

* **One device file is not accounted for.** `asound/Peppyalsa.postPeppyalsa.5.conf` shows as modified
  with an mtime of 2026-09-20 23:10 — well after the installation, and after the boot at 22:35.
  `index.js` and `install.sh` are the only files in the plugin that reference `postPeppyalsa`, so the
  plugin itself is the likely writer, but that attribution is **not proven**. Recorded as an
  observation; it is not folded into any claim above.
