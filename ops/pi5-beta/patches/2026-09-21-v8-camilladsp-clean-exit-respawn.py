import sys, hashlib, difflib, subprocess, tempfile, os

# v8: a clean FIFO-close exit must respawn IMMEDIATELY, as this file's own comment says.
#
# camilladsp-js.js:36-40 states the intent:
#   "The process may terminate either because FIFO has been closed (hence we need to
#    respawn the process immediately) or because of an error."
#
# The implementation contradicted that. Every exit - including the CLEAN code-0 exit that
# happens on every ordinary playback stop, because the writer closing pcm.volumio closes
# the fifo and camilladsp reads EOF - incremented the failure counter, and the delay was
# computed as 100 * 2^(n-1). So a normal back-and-forth between local and Spotify paid an
# exponentially growing silence in which the fifo has no reader - the gap heard at a
# handover. Measured on the device 2026-09-21:
#
#   15x "respawn in 100 ms (attempt 1/10)"
#    8x "respawn in 200 ms (attempt 2/10)"
#    4x "respawn in 400 ms (attempt 3/10)"
#    1x "respawn in 800 ms (attempt 4/10)"
#    1x "respawn in 1600 ms (attempt 5/10)"     <- 1.6 s with no fifo reader
#
# The counter only resets after 30 s of uptime, so any session shorter than that escalates.
#
# Fix: a clean exit that came well after spawn is a normal stop, not a failure, so it does
# not consume the failure budget and respawns at a short FIXED delay. A clean exit almost
# immediately after spawn (a genuine spawn/exit loop) still counts, still escalates, and
# still reaches maxConsecutiveRespawns - the protection is kept, only the false escalation
# on ordinary use is removed.
#
# This file is FusionDSP's process manager (a Volumio plugin, not spop/mpd). A plugin
# update would overwrite it, so the change is recorded in the fork with a rollback copy.

apply = '--apply' in sys.argv
BK = 'DEVICE_HOME/pi5-fix-backup-20260921-021521/'
P = '/data/plugins/audio_interface/fusiondsp/camilladsp-js.js'
src = open(P).read()

CONST_OLD = "    const respawnCountResetMs = 30000; // Reset count if up for this long"
CONST_NEW = """    const respawnCountResetMs = 30000; // Reset count if up for this long

    // v8 (pi5-beta 2026-09-21): a closed FIFO is a normal stop, not a failure.
    const cleanExitRespawnDelayMs = 100; // respawn at once, as the listener comment intends
    const cleanExitTrustMs = 2000;       // up this long => a real stop, not a spawn loop"""

BLOCK_OLD = """        // Increment consecutive respawn counter
        consecutiveRespawns++;

        // Check if we've exceeded max consecutive respawns
        if (consecutiveRespawns > maxConsecutiveRespawns) {
            logger.error(`camilladsp exceeded max consecutive respawns (${maxConsecutiveRespawns}); stopping respawn. Plugin restart required.`);
            respawnStopped = true;
            return;
        }

        // Calculate backoff delay: baseDelay * 2^(respawnCount-1), capped at maxDelay
        // For clean exit (code 0, e.g. FIFO closed), use shorter base delay
        if (code === 0) {
            timeout = Math.min(100 * Math.pow(2, consecutiveRespawns - 1), maxRespawnDelayMs);
        } else {
            timeout = Math.min(baseRespawnDelayMs * Math.pow(2, consecutiveRespawns - 1), maxRespawnDelayMs);
        }"""

BLOCK_NEW = """        // v8 (pi5-beta 2026-09-21): see the listener's comment above - a closed FIFO means
        // respawn immediately. It did not: the clean code-0 exit that follows every ordinary
        // playback stop was counted as a failure, so the delay doubled on each handover
        // (measured: 100, 200, 400, 800, 1600 ms of silence with no fifo reader). A clean
        // exit that came well after spawn is a normal stop, so it must not consume the
        // failure budget. A clean exit almost immediately after spawn is a spawn/exit loop
        // and still counts, so the maxConsecutiveRespawns protection is intact.
        const cleanExitAfterPlayback = (code === 0 && uptime >= cleanExitTrustMs);

        if (!cleanExitAfterPlayback) {

            // Increment consecutive respawn counter
            consecutiveRespawns++;

            // Check if we've exceeded max consecutive respawns
            if (consecutiveRespawns > maxConsecutiveRespawns) {
                logger.error(`camilladsp exceeded max consecutive respawns (${maxConsecutiveRespawns}); stopping respawn. Plugin restart required.`);
                respawnStopped = true;
                return;
            }
        }

        // Calculate backoff delay: baseDelay * 2^(respawnCount-1), capped at maxDelay
        // A clean exit after real playback respawns at a short FIXED delay; anything else
        // keeps the exponential backoff.
        if (cleanExitAfterPlayback) {
            timeout = cleanExitRespawnDelayMs;
        } else {
            timeout = Math.min(baseRespawnDelayMs * Math.pow(2, consecutiveRespawns - 1), maxRespawnDelayMs);
        }"""

# --- preconditions ------------------------------------------------------------------
assert 'cleanExitAfterPlayback' not in src, 'already patched (v8)'
assert src.count(CONST_OLD) == 1, 'the respawn-count-reset constant is not uniquely located'
assert src.count(BLOCK_OLD) == 1, 'the respawn/escalation block is not uniquely located'
assert src.count('consecutiveRespawns++') == 1, 'unexpected number of counter increments'
assert src.count('let timeout = 0;') == 1, 'timeout declaration not found where expected'
assert 'camilladsp respawn in ${timeout} ms' in src, 'the respawn log line moved - re-read the file'
assert src.count('let listenerClose = function(code, signal) {') == 1, 'listenerClose not uniquely located'
assert "FIFO has been closed" in src, 'the intent comment this patch relies on is gone'

out = src.replace(CONST_OLD, CONST_NEW).replace(BLOCK_OLD, BLOCK_NEW)

# --- postconditions -----------------------------------------------------------------
assert out.count('cleanExitAfterPlayback') == 3, 'expected: declaration + guard + delay branch'
assert out.count('consecutiveRespawns++') == 1, 'counter increment count changed'
assert out.count('cleanExitRespawnDelayMs') == 2, 'expected: constant + use'
assert out.count('cleanExitTrustMs') == 2, 'expected: constant + use'
assert 'Math.min(100 * Math.pow(2, consecutiveRespawns - 1)' not in out, 'the 100ms-escalation path survived'
assert out.count('respawnStopped = true;') == 1, 'the stop-respawning guard was disturbed'
assert out.count('consecutiveRespawns = 0;') == 3, 'the counter resets were disturbed'
assert out.count('respawnCountResetMs') == 2, 'the 30 s reset was disturbed'
assert len(out) > len(src), 'patch produced no growth'

# --- syntax check the staged bytes, not the live file --------------------------------
with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False) as t:
    t.write(out)
    staged = t.name
try:
    r = subprocess.run(['node', '--check', staged], capture_output=True, text=True)
    assert r.returncode == 0, 'node --check FAILED on the staged bytes:\n' + r.stderr
    syntax = 'node --check OK'
finally:
    os.unlink(staged)

print('=== PATCH v8 (fusiondsp camilladsp-js.js, clean FIFO close respawns at once) ===')
print('  bytes: %d -> %d (%+d)' % (len(src), len(out), len(out) - len(src)))
print('  ' + syntax)
for l in difflib.unified_diff(src.splitlines(), out.splitlines(), 'orig', 'v8', lineterm='', n=2):
    print('    ' + l)
print('  staged sha256: ' + hashlib.sha256(out.encode()).hexdigest())
if apply:
    open(BK + 'camilladsp-js.orig.js', 'w').write(src)
    open(P, 'w').write(out)
    print('  APPLIED. live sha256: ' + hashlib.sha256(open(P).read().encode()).hexdigest())
else:
    print('  DRY RUN only')
