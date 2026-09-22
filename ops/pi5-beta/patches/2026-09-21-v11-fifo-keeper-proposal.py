import sys, hashlib, difflib, subprocess, tempfile, os

# v11 (PROPOSAL - reviewed before deployment) - stop camilladsp exiting on every handover.
#
# WHY. Measured on the device with the ALSA hardware pointer, not with camilladsp's own log (shown
# to be stale and unusable as a counter): a handover costs
#   RUNNING -> SETUP (50 ms) -> CLOSED (251 ms) -> PREPARED -> RUNNING
# i.e. ~351 ms of silence. The trace also showed WHY: the DAC (card1, the I2S DAC) is opened by
# camilladsp, not by the players. Players write into the fifo; the hardware stream belongs to the
# DSP engine. camilladsp's capture is a RawFile read of the fifo, so when the last writer closes it
# the read hits EOF, the process exits cleanly (code 0), and the DAC stream it owned is torn down
# and rebuilt. This is separate from the defect v8 fixed (that was the respawn DELAY escalating).
#
# WHAT. Hold a write-only descriptor on the fifo for as long as camilladsp is meant to be running.
# A writer on the file means the reader never sees EOF, so the engine survives a handover.
# Measured with a manual keeper first: camilladsp survived a stop with zero respawns, the DAC stayed
# PAUSED rather than CLOSED, and the handover gap fell from 351 ms to 201 ms.
#
# REVIEW ROUND 2 (2026-09-21) REJECTED the first version of this patch for leaking descriptors, and
# the finding was correct. Two mechanisms, both now fixed:
#   F1  an fs.open callback still in flight when stop() ran could return AFTER a later start(), see
#       run === true again, and install a stale descriptor;
#   F2  nothing prevented overlapping opens, so each assignment to fifoKeeperFd overwrote - and
#       leaked - the previous one.
# THE FIX IS A LIFECYCLE GENERATION plus an in-flight guard. Every keeper operation carries the
# generation it began under; closeFifoKeeper() and this.start() advance that generation, so any
# in-flight open, retry timer, or already-scheduled respawn from an earlier lifecycle is invalid the
# moment it lands. fifoKeeperOpening makes a concurrent second open impossible, which is what makes
# an overwrite - and hence a leak - unrepresentable rather than merely unlikely.
#   F3  the ENXIO retry timer is now cleared on success and carries the generation.
#   F5  the respawn timer captures the generation and returns if it is stale.
# Round 2 also CLEARED the hazard I had asked about: open(O_WRONLY|O_NONBLOCK) on a fifo with no
# reader returns ENXIO and does not block, which is defined kernel fifo semantics rather than a
# property of this kernel version - so the event loop cannot stall on it.
#
# LIFETIME. The keeper is closed when the engine is deliberately stopped and when respawning has
# been abandoned, so we can never knowingly leave a writer on a fifo with no reader.
#
# Reviewed but NOT deployed by this script: it is a proposal until a review approves it.

apply = '--apply' in sys.argv
emit = None
if '--emit' in sys.argv:
    emit = sys.argv[sys.argv.index('--emit') + 1]

BK = 'DEVICE_HOME/pi5-fix-backup-20260921-021521/'
P = '/data/plugins/audio_interface/fusiondsp/camilladsp-js.js'
src = open(P).read()

REQUIRES_OLD = """const { execSync } = require("child_process");
const { spawn } = require("child_process");"""
REQUIRES_NEW = REQUIRES_OLD + """
const fs = require("fs");"""

CONST_OLD = """    const cdPathConfig = "/data/configuration/audio_interface/fusiondsp/camilladsp.yml";"""
CONST_NEW = CONST_OLD + """
    const cdFifo = "/tmp/fusiondspfifo";"""

STATE_OLD = """    let consecutiveRespawns = 0;
    let lastSpawnTime = 0;
    let respawnStopped = false;"""

STATE_NEW = STATE_OLD + """

    // v11: the fifo keeper. See the patch header for the measurement and the reasoning.
    //
    // LIFECYCLE GENERATION. Review round 2 (2026-09-21) rejected the first version of this because
    // it leaked descriptors: an fs.open callback still in flight when stop() ran could return AFTER
    // a later start(), see run === true again, and install a stale descriptor; and nothing
    // prevented overlapping opens, so each assignment to fifoKeeperFd overwrote - and leaked - the
    // previous one. Every keeper operation now carries the generation it began under, and both
    // closeFifoKeeper() and this.start() advance that generation, so any in-flight open, retry
    // timer or scheduled respawn from an earlier lifecycle is invalid the moment it lands.
    let keeperGeneration = 0;
    let fifoKeeperFd = null;
    let fifoKeeperTimer = null;
    let fifoKeeperOpening = false;

    let closeFifoKeeper = function() {
        // Advance FIRST: this is what invalidates every in-flight keeper operation and any respawn
        // timer scheduled by the lifecycle we are ending.
        keeperGeneration++;
        fifoKeeperOpening = false;
        if (fifoKeeperTimer) {
            clearTimeout(fifoKeeperTimer);
            fifoKeeperTimer = null;
        }
        if (fifoKeeperFd !== null) {
            try {
                fs.closeSync(fifoKeeperFd);
            } catch (e) {
                // already gone
            }
            fifoKeeperFd = null;
            logger.debug('camilladsp fifo keeper released the fifo');
        }
    };

    let openFifoKeeper = function(deadline, generation) {
        // Four reasons to do nothing: stale generation, a descriptor already held, an open already
        // in flight, or a stopped plugin. The in-flight guard is what makes an overwrite - and
        // therefore a leaked descriptor - impossible.
        if (generation !== keeperGeneration || fifoKeeperFd !== null || fifoKeeperOpening || run === false)
            return;

        // O_NONBLOCK is load-bearing: without it this open blocks until camilladsp has opened the
        // read end, freezing the plugin's event loop. With it, a fifo with no reader returns ENXIO
        // instead - the kernel's defined fifo open semantics, not a property of this kernel.
        const flags = fs.constants.O_WRONLY | fs.constants.O_NONBLOCK;
        fifoKeeperOpening = true;

        fs.open(cdFifo, flags, function(err, fd) {
            fifoKeeperOpening = false;
            if (generation !== keeperGeneration || run === false) {
                // Superseded while this open was in flight. Close what we were handed and do not
                // install it - this is exactly the descriptor the first version leaked.
                if (!err && fd !== undefined && fd !== null) {
                    try { fs.closeSync(fd); } catch (e) { /* nothing to close */ }
                }
                return;
            }
            if (!err) {
                fifoKeeperFd = fd;
                // Clear any pending retry, so no timer can outlive a settled open.
                if (fifoKeeperTimer) {
                    clearTimeout(fifoKeeperTimer);
                    fifoKeeperTimer = null;
                }
                logger.info(`camilladsp fifo keeper holding the fifo open (fd ${fd})`);
                return;
            }
            // ENXIO means no reader has the fifo open yet - expected until camilladsp starts.
            if (err.code === 'ENXIO' && Date.now() < deadline) {
                fifoKeeperTimer = setTimeout(function() {
                    fifoKeeperTimer = null;
                    openFifoKeeper(deadline, generation);
                }, 50);
                return;
            }
            logger.warn(`camilladsp fifo keeper could not open the fifo: ${err}`);
        });
    };"""

SPAWN_OLD = """        camilla = spawn(cdPath, args);
        lastSpawnTime = Date.now();"""
SPAWN_NEW = """        camilla = spawn(cdPath, args);
        lastSpawnTime = Date.now();

        // v11: hold the fifo open so the next handover does not tear the DAC stream down.
        openFifoKeeper(Date.now() + 2000, keeperGeneration);"""

STOP_OLD = """        let pid;

        try {

            if (camilla === null)
                return;"""
STOP_NEW = """        let pid;

        try {

            // v11: release the keeper first - and advance the generation - so a deliberately
            // stopped engine never leaves a writer on a fifo with no reader.
            closeFifoKeeper();

            if (camilla === null)
                return;"""

RESPAWNSTOP_OLD = """            if (consecutiveRespawns > maxConsecutiveRespawns) {
                logger.error(`camilladsp exceeded max consecutive respawns (${maxConsecutiveRespawns}); stopping respawn. Plugin restart required.`);
                respawnStopped = true;
                return;
            }"""
RESPAWNSTOP_NEW = """            if (consecutiveRespawns > maxConsecutiveRespawns) {
                logger.error(`camilladsp exceeded max consecutive respawns (${maxConsecutiveRespawns}); stopping respawn. Plugin restart required.`);
                // v11: respawning is abandoned, so the keeper goes too - otherwise a writer would
                // be left on a fifo with no reader.
                closeFifoKeeper();
                respawnStopped = true;
                return;
            }"""

# Reviewed round 2, F5: a respawn scheduled by an earlier lifecycle must not act on a later one.
RESPAWNTIMER_OLD = """        setTimeout(function() {

            if (run === false)
                return;

            // In case of error, cleanup the FIFO before starting, so it won't be
            // kept in wait state and stall the whole pipeline"""
RESPAWNTIMER_NEW = """        // v11 (review round 2, F5): capture the generation this respawn belongs to. stop() and
        // closeFifoKeeper() advance it, so a timer left over from a previous lifecycle returns
        // instead of acting on the current one.
        const respawnGeneration = keeperGeneration;
        setTimeout(function() {

            if (run === false || respawnGeneration !== keeperGeneration)
                return;

            // In case of error, cleanup the FIFO before starting, so it won't be
            // kept in wait state and stall the whole pipeline"""

# Reviewed round 2, F1: a restart must invalidate anything still in flight from the last lifecycle.
START_OLD = """        run = true;

        // Reset respawn state on explicit start
        consecutiveRespawns = 0;
        respawnStopped = false;"""
START_NEW = """        run = true;

        // v11: a new lifecycle invalidates anything still in flight from the previous one -
        // in-flight keeper opens, retry timers, and already-scheduled respawn timers.
        keeperGeneration++;

        // Reset respawn state on explicit start
        consecutiveRespawns = 0;
        respawnStopped = false;"""

PAIRS = [
    (REQUIRES_OLD, REQUIRES_NEW),
    (CONST_OLD, CONST_NEW),
    (STATE_OLD, STATE_NEW),
    (SPAWN_OLD, SPAWN_NEW),
    (STOP_OLD, STOP_NEW),
    (RESPAWNSTOP_OLD, RESPAWNSTOP_NEW),
    (RESPAWNTIMER_OLD, RESPAWNTIMER_NEW),
    (START_OLD, START_NEW),
]

# --- preconditions ------------------------------------------------------------------
assert 'fifoKeeperFd' not in src, 'already patched (v11)'
assert 'require("fs")' not in src, 'fs is already required - adjust this patch'
assert 'cleanExitAfterPlayback' in src, 'this file is not at v8+ - patch the earlier steps first'
assert 'Reviewed finding 7' in src, 'this file is not at v9 - patch v9 first'
for old, _ in PAIRS:
    assert src.count(old) == 1, 'anchor not uniquely located (%d matches):\n%s' % (src.count(old), old[:140])

out = src
for old, new in PAIRS:
    out = out.replace(old, new)

# --- postconditions -----------------------------------------------------------------
assert out.count('require("fs")') == 1, 'fs require not added exactly once'
assert out.count('const cdFifo = "/tmp/fusiondspfifo";') == 1
assert out.count('generation !== keeperGeneration') == 2, 'expected: the open guard + the callback supersede check'
assert out.count('keeperGeneration++') == 2, 'expected: closeFifoKeeper + start'
assert out.count('respawnGeneration !== keeperGeneration') == 1, 'the stale-respawn check is missing'
assert out.count('fifoKeeperOpening = true') == 1, 'the in-flight guard is never set'
assert out.count('fifoKeeperOpening = false') == 3, 'expected: declaration + reset on close + reset in the callback'
assert out.count('openFifoKeeper(Date.now() + 2000, keeperGeneration)') == 1, 'spawn does not pass the generation'
assert out.count('fs.constants.O_NONBLOCK') == 1, 'the non-blocking flag is missing'
assert "err.code === 'ENXIO'" in out, 'the retry-on-no-reader comparison is missing'
assert out.count('closeFifoKeeper();') == 2, 'expected exactly two close CALL sites'
assert out.count('cleanExitAfterPlayback') == 3, 'the v8/v9 logic was disturbed'
counts = {k: out.count(k) for k in ('keeperGeneration', 'openFifoKeeper', 'closeFifoKeeper', 'fifoKeeperOpening')}
print('  identifier counts: %s' % counts)
for k in counts:
    assert counts[k] >= 3, 'suspiciously few references to ' + k
assert len(out) > len(src), 'patch produced no growth'
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

print('=== PATCH v11 (PROPOSAL, fusiondsp camilladsp-js.js - fifo keeper, generation-guarded) ===')
print('  bytes: %d -> %d (%+d)' % (len(src), len(out), len(out) - len(src)))
print('  ' + syntax)
for l in difflib.unified_diff(src.splitlines(), out.splitlines(), 'v9', 'v11', lineterm='', n=1):
    print('    ' + l)
print('  staged sha256: ' + hashlib.sha256(out.encode()).hexdigest())
if emit:
    open(emit, 'w').write(out)
    print('  EMITTED staged bytes to ' + emit)
if apply:
    open(BK + 'camilladsp-js.v10.js', 'w').write(src)
    open(P, 'w').write(out)
    print('  APPLIED. live sha256: ' + hashlib.sha256(open(P).read().encode()).hexdigest())
else:
    print('  DRY RUN only - this is a review proposal, not a deployment')
