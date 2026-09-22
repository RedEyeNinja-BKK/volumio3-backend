import sys, hashlib, difflib, subprocess, tempfile, os

# v12 - response to review round 3 (REJECT: 2 blocking).
#
# F1 (blocking): "go-librespot-only ownership is not the required mutual-exclusion predicate ...
# a temporal check, not ownership acquisition." F2 (blocking): a stale closed-and-reopened
# descriptor can be misattributed as free.
#
# THE HONEST POSITION, and it is not "fixed":
#
# The reviewer is right that a /proc scan cannot deliver atomic exclusion, and the reason is
# structural, not a coding slip. I investigated closing it properly and the design does not
# permit it:
#   * A real lock would need BOTH writers to take it. The writers are MPD and go-librespot,
#     and neither takes a lock we could join, so there is no common lock to acquire.
#   * The obvious stronger predicate - "does ANY process hold the fifo for writing" - is NOT
#     implementable from this plugin. MPD runs as its own user (`mpd`) and the core runs as
#     `volumio`, so MPD's /proc/<pid>/fdinfo is unreadable here; I cannot classify its access
#     mode. Worse, failing closed on every unreadable /proc entry would flag unrelated system
#     processes and refuse all playback.
#   * So the check is necessarily TARGETED at the one process this handoff is about -
#     go-librespot - which does run as the same user and is therefore readable.
#
# What that leaves is a check-to-use window: a writer that opens the fifo after the final scan
# and before the caller's own open is not detected. That window cannot be removed at this
# layer. It is now BOUNDED, DOCUMENTED IN THE CODE, and not claimed as proof.
#
# What v12 does fix, all of it cheaply and without weakening the fail-closed direction:
#   F3 exact comm matching is brittle -> also match on the resolved executable.
#   F4 the scan was synchronous and unbounded -> explicit pid/fd/time caps; any cap exceeded
#      returns "held", so the caller fails closed rather than proceeding on a truncated scan.
#   F5 a slow-but-successful release becomes a refusal -> deadline raised 3s -> 5s, and the
#      refusal message now carries the elapsed time so the operator can tell the two apart.
#   F6 indexOf could match a lookalike path (e.g. /tmp/fusiondspfifo.backup) -> exact compare.
# F1/F2 are NOT closed and are not claimed to be. They are recorded as an accepted limitation
# with the reasoning above, which is what the round-2 review itself allowed for when it said a
# bounded proxy, documented as such, was acceptable in the absence of an external guarantee.

apply = '--apply' in sys.argv
BK = 'DEVICE_HOME/pi5-fix-backup-20260921-021521/'
P = '/data/plugins/music_service/spop/index.js'
src = open(P).read()

PRED_OLD = """ControllerSpotify.prototype.fifoHeldByGoLibrespot = function () {
    var self = this;
    var prefix = '/tmp/fusiondspfifo';
    var pids = [];

    try {
        fs.readdirSync('/proc').forEach(function (entry) {
            if (!/^[0-9]+$/.test(entry)) {
                return;
            }
            try {
                var comm = fs.readFileSync('/proc/' + entry + '/comm', 'utf8').trim();
                if (comm === 'go-librespot') {
                    pids.push(entry);
                }
            } catch (err) {
                // the process vanished between readdir and read - not a holder we can see
            }
        });
    } catch (err) {
        self.logger.error('fifoHeldByGoLibrespot: cannot scan /proc: ' + err);
        return true;
    }

    for (var i = 0; i < pids.length; i++) {
        var dir = '/proc/' + pids[i] + '/fd';
        var fds;
        try {
            fds = fs.readdirSync(dir);
        } catch (err) {
            self.logger.error('fifoHeldByGoLibrespot: cannot read ' + dir + ': ' + err);
            return true;
        }
        for (var j = 0; j < fds.length; j++) {
            try {
                if (fs.readlinkSync(dir + '/' + fds[j]).indexOf(prefix) !== -1) {
                    return true;
                }
            } catch (err) {
                // fd closed between readdir and readlink - nothing to see
            }
        }
    }
    return false;
};"""

PRED_NEW = """// Does the Spotify backend still hold the shared fifo open?
//
// WHAT THIS IS, EXACTLY: a bounded proxy, not proof. It answers "does a go-librespot process
// currently have the fifo open", which is not the same proposition as "no competing writer
// will interleave with the player I am about to start".
//
// Review round 3 (2026-09-21) rejected an earlier version of this for exactly that reason, and
// the objection is correct. It is also not closable at this layer, and the code should say so
// rather than imply otherwise:
//   * A lock would need BOTH writers to take it. The writers are MPD and go-librespot; neither
//     takes a lock this plugin could join, so there is no common lock to acquire.
//   * "Does ANY process hold the fifo for writing" is not implementable here: MPD runs as user
//     `mpd` and this plugin runs as `volumio`, so MPD's /proc/<pid>/fdinfo is unreadable and
//     its access mode cannot be determined. Failing closed on every unreadable /proc entry
//     would flag unrelated system processes and refuse all playback.
//   * So the check is necessarily TARGETED at go-librespot, the one process this handoff is
//     about, which is readable because it shares our uid.
//
// RESIDUAL WINDOW, STATED NOT HIDDEN: a writer that opens the fifo after the final scan and
// before the caller's own open is not detected. The scan runs in single-digit milliseconds and
// the caller opens immediately after, but the window is real and is not claimed to be closed.
//
// The scan is CAPPED. Any cap exceeded - or any inability to read what we need - returns true,
// i.e. "assume it is still held", so every caller fails closed rather than acting on a
// truncated view.
ControllerSpotify.prototype.fifoHeldByGoLibrespot = function () {
    var self = this;
    var fifoPath = '/tmp/fusiondspfifo';
    // Caps: bounded so this can never stall the plugin's event loop (review round 3, F4).
    var maxPids = 4000;
    var maxFds = 20000;
    var scanDeadline = Date.now() + 400;
    var pids = [];
    var entries;
    var scannedPids = 0;
    var scannedFds = 0;

    try {
        entries = fs.readdirSync('/proc');
    } catch (err) {
        self.logger.error('fifoHeldByGoLibrespot: cannot scan /proc: ' + err);
        return true;
    }

    // Find candidate processes. Name matching is deliberately permissive (review round 3, F3):
    // an exact comm match, or a resolved executable whose name mentions librespot, which
    // survives a wrapper or a renamed binary.
    for (var e = 0; e < entries.length; e++) {
        var entry = entries[e];
        if (!/^[0-9]+$/.test(entry)) {
            continue;
        }
        if (++scannedPids > maxPids || Date.now() > scanDeadline) {
            self.logger.error('fifoHeldByGoLibrespot: scan budget exhausted; assuming held');
            return true;
        }
        var name = null;
        try {
            name = fs.readFileSync('/proc/' + entry + '/comm', 'utf8').trim();
        } catch (err) {
            continue;   // vanished, or another user's process we are not looking at
        }
        var isCandidate = (name === 'go-librespot');
        if (!isCandidate) {
            try {
                var exe = fs.readlinkSync('/proc/' + entry + '/exe');
                isCandidate = (exe.toLowerCase().indexOf('librespot') !== -1);
            } catch (err) {
                // no /proc/<pid>/exe permission - we can only judge by the name
            }
        }
        if (isCandidate) {
            pids.push(entry);
        }
    }

    for (var i = 0; i < pids.length; i++) {
        var dir = '/proc/' + pids[i] + '/fd';
        var fds;
        try {
            fds = fs.readdirSync(dir);
        } catch (err) {
            self.logger.error('fifoHeldByGoLibrespot: cannot read ' + dir + ': ' + err);
            return true;
        }
        for (var j = 0; j < fds.length; j++) {
            if (++scannedFds > maxFds || Date.now() > scanDeadline) {
                self.logger.error('fifoHeldByGoLibrespot: scan budget exhausted; assuming held');
                return true;
            }
            try {
                // Exact comparison (review round 3, F6): a lookalike such as
                // /tmp/fusiondspfifo.backup must not count as the shared fifo.
                if (fs.readlinkSync(dir + '/' + fds[j]) === fifoPath) {
                    return true;
                }
            } catch (err) {
                // fd closed between readdir and readlink - nothing to see
            }
        }
    }
    return false;
};"""

DEADLINE_OLD = """    var start = Date.now();
    var deadline = start + 3000;
    var settled = false;"""
DEADLINE_NEW = """    var start = Date.now();
    // 5 s, not 3 s (review round 3, F5): a release that is real but slow must not be
    // indistinguishable from one that failed for any longer than necessary.
    var deadline = start + 5000;
    var settled = false;"""

MSG_OLD = """        if (Date.now() >= deadline) {
            finish(false, 'go-librespot still holds the fifo after ' + (Date.now() - start) + ' ms');
            return;
        }"""
MSG_NEW = """        if (Date.now() >= deadline) {
            finish(false, 'go-librespot still holds the fifo after ' + (Date.now() - start) +
                ' ms - treating a release this slow as a failure');
            return;
        }"""

PAIRS = [(PRED_OLD, PRED_NEW), (DEADLINE_OLD, DEADLINE_NEW), (MSG_OLD, MSG_NEW)]

# --- preconditions ------------------------------------------------------------------
assert 'fifoHasUnexpectedWriter' not in src, 'unexpected prior patch'
assert 'bounded proxy, not proof' not in src, 'already patched (v12)'
assert 'fifoHeldByGoLibrespot' in src, 'this file is not at v10/v11 - patch v10 first'
for old, _ in PAIRS:
    assert src.count(old) == 1, 'anchor not uniquely located (%d):\n%s' % (src.count(old), old[:120])

out = src
for old, new in PAIRS:
    out = out.replace(old, new)

# --- postconditions -----------------------------------------------------------------
assert out.count('bounded proxy, not proof') == 1, 'the honest framing comment is missing'
assert out.count('=== fifoPath') == 1, 'the exact-match comparison is missing'
assert out.count('scan budget exhausted') == 2, 'the scan caps are missing'
assert out.count('maxPids') == 2 and out.count('maxFds') == 2, 'scan caps not applied'
assert out.count('deadline = start + 5000') == 1, 'the deadline was not raised'
assert out.count('fifoHeldByGoLibrespot') == 7, 'predicate occurrences changed unexpectedly'
assert out.count("indexOf(prefix)") == 0, 'the loose indexOf match survived'
assert len(out) > len(src), 'patch produced no growth'

with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False) as t:
    t.write(out)
    staged = t.name
try:
    r = subprocess.run(['node', '--check', staged], capture_output=True, text=True)
    assert r.returncode == 0, 'node --check FAILED on the staged bytes:\n' + r.stderr
    syntax = 'node --check OK'
finally:
    os.unlink(staged)

print('=== PATCH v12 (spop - bounded, exact, honestly-framed confirmation) ===')
print('  bytes: %d -> %d (%+d)' % (len(src), len(out), len(out) - len(src)))
print('  ' + syntax)
for l in difflib.unified_diff(src.splitlines(), out.splitlines(), 'v10', 'v12', lineterm='', n=1):
    print('    ' + l)
print('  staged sha256: ' + hashlib.sha256(out.encode()).hexdigest())
if apply:
    open(BK + 'spop-index.v10.js', 'w').write(src)
    open(P, 'w').write(out)
    print('  APPLIED. live sha256: ' + hashlib.sha256(open(P).read().encode()).hexdigest())
else:
    print('  DRY RUN only')
