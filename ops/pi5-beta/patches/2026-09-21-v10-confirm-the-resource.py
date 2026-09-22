import sys, hashlib, difflib, subprocess, tempfile, os

# v10 - response to review round 2.
#
# ROUND-2 BLOCKING FINDING: "Confirmation criterion can resolve true before FIFO ownership is
# actually released." Correct. handoffAudioDevice() confirmed go-librespot's PLAYER STATE
# (/status -> stopped || paused || !track), which is a proxy for release, not proof of it. A
# paused player can still hold the fifo open.
#
# THE FIX: confirm the RESOURCE, not the state. The fifo is what actually collides, so we ask
# who holds it. Every agent in this stack runs as the same uid (both go-librespot and the
# volumio core run as `volumio`), so go-librespot's descriptor table is readable from the
# plugin. This removes /status from the decision entirely, which also disposes of round-2
# findings 2 and 3 by construction:
#   F2 (no settled guard / late callbacks re-entering) - an explicit settled flag.
#   F3 ("!b" treats a malformed /status body as released, a fail-open) - no body is parsed
#       any more; there is nothing to fail open on.
#   F4 (the "definitely idle" fast path bypassed confirmation on possibly stale plugin state)
#       - the fast path now also requires that go-librespot does NOT hold the fifo.
#   F6 (the handoff invocation was outside a try/catch, so a synchronous throw rejected
#       instead of reaching the refuse path) - wrapped, and converted to a refusal.
#   F5 (a missing method refuses playback) - unchanged and intended; noted, not softened. It
#       is the correct fail-closed answer, and both files ship together.
#
# NOT addressed: F1's stronger form is now addressed above; nothing from round 2 is left
# unhandled except F5, which is a deliberate refusal contract.

apply = '--apply' in sys.argv
BK = 'DEVICE_HOME/pi5-fix-backup-20260921-021521/'

SPOP = '/data/plugins/music_service/spop/index.js'
MPD = '/volumio/app/plugins/music_service/mpd/index.js'

edits = []


# --------------------------------------------------- spop: ground-truth predicate + rework
SPOP_HANDOFF_OLD = """ControllerSpotify.prototype.handoffAudioDevice = function () {
    var self = this;
    var defer = libQ.defer();
    var deadline = Date.now() + 3000;

    self.logger.info('Handoff: pausing Spotify to release the audio device');
    try {
        self.sendSpotifyLocalApiCommand('/player/pause');
    } catch (e) {
        self.logger.error('Handoff: could not send the pause: ' + e);
        defer.resolve(false);
        return defer.promise;
    }

    var attempt = function () {
        superagent.get(spotifyLocalApiEndpointBase + '/status')
            .accept('application/json')
            .timeout({ response: 1500, deadline: 2000 })
            .then(function (res) {
                var b = res && res.body;
                // paused, stopped, or nothing loaded all mean it is not writing the fifo
                var released = !b || b.stopped === true || b.paused === true || !b.track;
                if (released) {
                    self.logger.info('Handoff: Spotify released the audio device');
                    defer.resolve(true);
                } else if (Date.now() < deadline) {
                    setTimeout(attempt, 100);
                } else {
                    self.logger.error('Handoff: Spotify did not release the audio device in time');
                    defer.resolve(false);
                }
            })
            .catch(function (error) {
                if (Date.now() < deadline) {
                    setTimeout(attempt, 100);
                } else {
                    self.logger.error('Handoff: could not confirm the release: ' + error);
                    defer.resolve(false);
                }
            });
    };
    attempt();
    return defer.promise;
};"""

SPOP_HANDOFF_NEW = """// Does go-librespot still hold the shared audio device open?
//
// This is the GROUND TRUTH for the resource that actually collides. The plugin's player
// state, and go-librespot's own /status, are only proxies for it: independent review (round 2,
// 2026-09-21) correctly rejected "status says paused" as proof of release, because a paused
// player can still hold the fifo. The fifo is the collision, so we look at who holds it.
//
// Reads /proc, which is why it is safe here: go-librespot and the volumio core both run as
// the same user, so its descriptor table is readable. Returns TRUE when the answer cannot be
// determined, so every caller fails closed rather than assuming a release.
ControllerSpotify.prototype.fifoHeldByGoLibrespot = function () {
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
};

// Hand the audio device to another service, and report whether the device was ACTUALLY
// released. Resolves true only when go-librespot no longer holds the fifo open - i.e. the
// invariant itself, not a proxy for it.
ControllerSpotify.prototype.handoffAudioDevice = function () {
    var self = this;
    var defer = libQ.defer();
    var start = Date.now();
    var deadline = start + 3000;
    var settled = false;

    // Round-2 finding 2: a late callback must not re-enter or schedule more work.
    var finish = function (released, why) {
        if (settled) {
            return;
        }
        settled = true;
        if (released) {
            self.logger.info('Handoff: audio device released - ' + why);
        } else {
            self.logger.error('Handoff: audio device NOT released - ' + why);
        }
        defer.resolve(released);
    };

    var attempt = function () {
        if (settled) {
            return;
        }
        if (!self.fifoHeldByGoLibrespot()) {
            finish(true, 'go-librespot no longer holds the fifo');
            return;
        }
        if (Date.now() >= deadline) {
            finish(false, 'go-librespot still holds the fifo after ' + (Date.now() - start) + ' ms');
            return;
        }
        setTimeout(attempt, 100);
    };

    if (!self.fifoHeldByGoLibrespot()) {
        finish(true, 'go-librespot was not holding the fifo');
        return defer.promise;
    }

    self.logger.info('Handoff: pausing Spotify to release the audio device');
    try {
        self.sendSpotifyLocalApiCommand('/player/pause');
    } catch (e) {
        finish(false, 'could not send the pause: ' + e);
        return defer.promise;
    }

    setTimeout(attempt, 100);
    return defer.promise;
};"""


# ------------------------------------------- mpd: fast path needs ground truth; wrap the call
MPD_FASTPATH_OLD = """  var cs = null;
  var releaseNeeded = false;
  try {
    cs = self.commandRouter.pluginManager.getPlugin('music_service', 'spop');
    if (cs && typeof cs.stop === 'function') {
      var csm = self.commandRouter.stateMachine;
      var spopStopped = !!(cs.state && cs.state.status === 'stop');
      var definitelyIdle = spopStopped && !(csm && csm.isVolatile === true);
      releaseNeeded = !definitelyIdle;
    }
  } catch (e) {
    self.logger.error('MPD taking over: could not inspect the spop plugin: ' + e);
  }"""

MPD_FASTPATH_NEW = """  var cs = null;
  var releaseNeeded = false;
  try {
    cs = self.commandRouter.pluginManager.getPlugin('music_service', 'spop');
    if (cs && typeof cs.stop === 'function') {
      var csm = self.commandRouter.stateMachine;
      var spopStopped = !!(cs.state && cs.state.status === 'stop');
      // v10 (2026-09-21): the "definitely idle" shortcut no longer trusts plugin state alone.
      // Independent review (round 2) pointed out it could start local playback on stale spop
      // state while go-librespot still held the fifo. The shortcut now also requires the
      // ground truth: go-librespot must NOT hold the fifo. Without the predicate we do not
      // take the shortcut at all - we go through the confirming handoff.
      var fifoFree = (typeof cs.fifoHeldByGoLibrespot === 'function')
        ? !cs.fifoHeldByGoLibrespot()
        : false;
      var definitelyIdle = spopStopped && !(csm && csm.isVolatile === true) && fifoFree;
      releaseNeeded = !definitelyIdle;
    }
  } catch (e) {
    self.logger.error('MPD taking over: could not inspect the spop plugin: ' + e);
  }"""

MPD_CALL_OLD = """  self.logger.info('MPD taking over: releasing the audio device from spop');
  var handoff;
  if (typeof cs.handoffAudioDevice === 'function') {
    handoff = cs.handoffAudioDevice();
  } else {"""

MPD_CALL_NEW = """  self.logger.info('MPD taking over: releasing the audio device from spop');
  var handoff;
  if (typeof cs.handoffAudioDevice === 'function') {
    // Round-2 finding 6: a synchronous throw here must become a refusal, not a rejected
    // promise on a path that never reaches the toast.
    try {
      handoff = cs.handoffAudioDevice();
    } catch (e) {
      self.logger.error('MPD taking over: handoffAudioDevice threw: ' + e);
      handoff = libQ.resolve(false);
    }
  } else {"""


def patch(path, pairs, checks_before, checks_after, label):
    src = open(path).read()
    for old, new in pairs:
        assert src.count(old) == 1, '%s: anchor not uniquely located (%d matches):\n%s' % (
            label, src.count(old), old[:140])
    for c in checks_before:
        assert c in src, '%s: precondition missing: %s' % (label, c)
    out = src
    for old, new in pairs:
        out = out.replace(old, new)
    for c in checks_after:
        assert c in out, '%s: postcondition missing: %s' % (label, c)
    with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False) as t:
        t.write(out)
        staged = t.name
    try:
        r = subprocess.run(['node', '--check', staged], capture_output=True, text=True)
        assert r.returncode == 0, '%s: node --check FAILED:\n%s' % (label, r.stderr)
    finally:
        os.unlink(staged)
    print('=== %s ===' % label)
    print('  bytes: %d -> %d (%+d)   node --check OK' % (len(src), len(out), len(out) - len(src)))
    for l in difflib.unified_diff(src.splitlines(), out.splitlines(), 'before', 'after', lineterm='', n=1):
        print('    ' + l)
    print('  staged sha256: ' + hashlib.sha256(out.encode()).hexdigest())
    return out


spop_now = open(SPOP).read()
mpd_now = open(MPD).read()
assert 'fifoHeldByGoLibrespot' not in spop_now, 'spop already patched (v10)'
assert 'fifoHeldByGoLibrespot' not in mpd_now, 'mpd already patched (v10)'
assert 'Handoff: Spotify released the audio device' in spop_now, 'spop is not at v9 - patch v9 first'
assert '_mpdStartLocalPlayback' in mpd_now, 'mpd is not at v9 - patch v9 first'

spop_out = patch(
    SPOP,
    [(SPOP_HANDOFF_OLD, SPOP_HANDOFF_NEW)],
    ['handoffAudioDevice', 'fs.readFileSync', 'var fs ='],
    ['fifoHeldByGoLibrespot', 'settled', 'no longer holds the fifo'],
    'spop/index.js - confirm the RESOURCE (fifo), not the player state',
)
# /status must no longer participate in the RELEASE DECISION. Scoped to the handoff function -
# spop legitimately uses /status elsewhere (hasActiveDaemonSession), so a global absence check
# would be wrong.
_h = spop_out[spop_out.index('ControllerSpotify.prototype.handoffAudioDevice = function () {'):]
_h = _h[:_h.index('\n};')]
assert '/status' not in _h, 'a /status dependency survived inside the handoff'
assert 'superagent' not in _h, 'the handoff still makes an HTTP call'
assert spop_out.count('fifoHeldByGoLibrespot') == 5, 'expected: 1 definition + 4 uses'

mpd_out = patch(
    MPD,
    [(MPD_FASTPATH_OLD, MPD_FASTPATH_NEW), (MPD_CALL_OLD, MPD_CALL_NEW)],
    ['_mpdStartLocalPlayback', 'definitelyIdle'],
    ['fifoHeldByGoLibrespot', 'handoffAudioDevice threw'],
    'mpd/index.js - ground-truth fast path + wrapped handoff call',
)
assert mpd_out.count('fifoHeldByGoLibrespot') == 2, 'expected: typeof check + the call'

if apply:
    open(BK + 'spop-index.v9.js', 'w').write(spop_now)
    open(BK + 'mpd-index.v9.js', 'w').write(mpd_now)
    open(SPOP, 'w').write(spop_out)
    open(MPD, 'w').write(mpd_out)
    print()
    for p in (SPOP, MPD):
        print('  APPLIED %s -> %s' % (hashlib.sha256(open(p).read().encode()).hexdigest()[:16], p))
else:
    print('\n  DRY RUN only')
