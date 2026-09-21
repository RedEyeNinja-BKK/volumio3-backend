import sys, hashlib, difflib, subprocess, tempfile, os

# v11 (PROPOSAL - reviewed before deployment) - stop camilladsp exiting on every handover.
#
# WHY. Measured on the device with the ALSA hardware pointer, not with camilladsp's own log
# (which was shown to be stale and is not a usable counter): a handover costs
#   RUNNING -> SETUP (50 ms) -> CLOSED (251 ms) -> PREPARED -> RUNNING
# i.e. about 351 ms of silence. The trace also showed WHY: the DAC (card1, the I2S DAC) is
# opened by camilladsp, not by the players. Players write into the fifo; the hardware stream
# belongs to the DSP engine. So the gap is a camilladsp LIFETIME problem: camilladsp's capture
# is a RawFile read of the fifo, and when the last writer closes it the read hits EOF, the
# process exits cleanly (code 0) and the DAC stream it owned is torn down and rebuilt.
#
# WHAT. Hold a write-only descriptor on the fifo for as long as camilladsp is meant to be
# running. A writer on the file means the read never sees EOF, so the engine survives a
# handover. Measured with a manual keeper: camilladsp survived a stop with zero respawns, the
# DAC stayed in PAUSED rather than CLOSED, and the handover gap fell from 351 ms to 201 ms.
#
# THE OPEN MUST BE NON-BLOCKING. Opening a fifo for writing blocks until a reader exists, and
# doing that on the plugin's event loop would freeze Volumio. With O_NONBLOCK the open fails
# with ENXIO instead, so we retry on a short timer until camilladsp has opened the read end.
#
# LIFETIME. The keeper is closed when the engine is deliberately stopped, and when respawning
# has been abandoned - so we can never leave a writer on a fifo with no reader, which would
# make a later writer block on open.
#
# NOT DEPLOYED. This is a proposal: it changes the audio-path lifecycle of a third-party
# plugin and should be reviewed before it runs. Nothing here is applied by the review round.

apply = '--apply' in sys.argv
BK = '/home/volumio/pi5-fix-backup-20260921-021521/'
P = '/data/plugins/audio_interface/fusiondsp/camilladsp-js.js'
src = open(P).read()

REQUIRES_OLD = """const { execSync } = require("child_process");
const { spawn } = require("child_process");"""
REQUIRES_NEW = """const { execSync } = require("child_process");
const { spawn } = require("child_process");
const fs = require("fs");"""

CONST_OLD = """    const cdPathConfig = "/data/configuration/audio_interface/fusiondsp/camilladsp.yml";"""
CONST_NEW = CONST_OLD + """
    const cdFifo = "/tmp/fusiondspfifo";"""

STATE_OLD = """    let consecutiveRespawns = 0;
    let lastSpawnTime = 0;
    let respawnStopped = false;"""
STATE_NEW = STATE_OLD + """

    // v11: the fifo keeper. See the header of this patch for the measurement and the reasoning.
    let fifoKeeperFd = null;
    let fifoKeeperTimer = null;

    let closeFifoKeeper = function() {
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

    let openFifoKeeper = function(deadline) {
        if (fifoKeeperFd !== null || run === false)
            return;

        // O_NONBLOCK is load-bearing: without it this open blocks until camilladsp has opened
        // the read end, freezing the plugin's event loop.
        const flags = fs.constants.O_WRONLY | fs.constants.O_NONBLOCK;

        fs.open(cdFifo, flags, function(err, fd) {
            if (run === false) {
                if (fd !== undefined && fd !== null) {
                    try { fs.closeSync(fd); } catch (e) { /* nothing to close */ }
                }
                return;
            }
            if (!err) {
                fifoKeeperFd = fd;
                logger.info(`camilladsp fifo keeper holding the fifo open (fd ${fd})`);
                return;
            }
            // ENXIO means no reader has the fifo open yet - expected until camilladsp starts.
            if (err.code === 'ENXIO' && Date.now() < deadline) {
                fifoKeeperTimer = setTimeout(function() { openFifoKeeper(deadline); }, 50);
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
        openFifoKeeper(Date.now() + 2000);"""

STOP_OLD = """        let pid;

        try {

            if (camilla === null)
                return;"""
STOP_NEW = """        let pid;

        try {

            // v11: release the keeper first, so a deliberately stopped engine never leaves a
            // writer on a fifo with no reader.
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
                // v11: respawning is abandoned, so the keeper must go too - otherwise a writer
                // would be left on a fifo with no reader.
                closeFifoKeeper();
                respawnStopped = true;
                return;
            }"""

PAIRS = [
    (REQUIRES_OLD, REQUIRES_NEW),
    (CONST_OLD, CONST_NEW),
    (STATE_OLD, STATE_NEW),
    (SPAWN_OLD, SPAWN_NEW),
    (STOP_OLD, STOP_NEW),
    (RESPAWNSTOP_OLD, RESPAWNSTOP_NEW),
]

# --- preconditions ------------------------------------------------------------------
assert 'fifoKeeperFd' not in src, 'already patched (v11)'
assert 'require("fs")' not in src, 'fs is already required - adjust this patch'
assert 'cleanExitAfterPlayback' in src, 'this file is not at v8+ - patch the earlier steps first'
assert 'Reviewed finding 7' in src, 'this file is not at v9 - patch v9 first'
for old, _ in PAIRS:
    assert src.count(old) == 1, 'anchor not uniquely located (%d matches):\n%s' % (src.count(old), old[:120])

out = src
for old, new in PAIRS:
    out = out.replace(old, new)

# --- postconditions -----------------------------------------------------------------
assert out.count('require("fs")') == 1, 'fs require not added exactly once'
assert out.count('const cdFifo = "/tmp/fusiondspfifo";') == 1
assert out.count('openFifoKeeper') == 3, 'expected: definition + call + retry'
assert out.count('closeFifoKeeper') == 3, 'expected: definition + 2 call sites (stop, respawn-abandoned)'
assert out.count('fs.constants.O_NONBLOCK') == 1, 'the non-blocking flag is missing'
assert out.count('ENXIO') == 2, 'expected: one comment + the retry comparison'
assert out.count('cleanExitAfterPlayback') == 3, 'the v8/v9 logic was disturbed'
assert 'callback: self.libRespotGoUnsetVolatile' not in out, 'wrong file'
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

print('=== PATCH v11 (PROPOSAL, fusiondsp camilladsp-js.js - fifo keeper) ===')
print('  bytes: %d -> %d (%+d)' % (len(src), len(out), len(out) - len(src)))
print('  ' + syntax)
for l in difflib.unified_diff(src.splitlines(), out.splitlines(), 'v10', 'v11', lineterm='', n=1):
    print('    ' + l)
print('  staged sha256: ' + hashlib.sha256(out.encode()).hexdigest())
if apply:
    open(BK + 'camilladsp-js.v10.js', 'w').write(src)
    open(P, 'w').write(out)
    print('  APPLIED. live sha256: ' + hashlib.sha256(open(P).read().encode()).hexdigest())
else:
    print('  DRY RUN only - this is a review proposal, not a deployment')
