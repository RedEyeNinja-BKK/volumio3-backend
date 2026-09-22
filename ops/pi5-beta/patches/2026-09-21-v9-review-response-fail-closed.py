import sys, hashlib, difflib, subprocess, tempfile, os

# v9 - response to the independent review of the pi5-beta handover work.
# Verdict: APPROVE-WITH-FINDINGS, 1 blocking + 7 non-blocking. This patcher addresses the
# blocking finding and two of the non-blocking ones, across three files.
#
# FINDING 1 (BLOCKING) - "MPD takeover can proceed while Spotify remains the actual FIFO
# writer". The mpd guard called cs.stop(), swallowed any failure, and started local playback
# regardless. Two independent holes, both real:
#   (a) ControllerSpotify.prototype.stop() resolves immediately and never confirms that
#       go-librespot actually paused;
#   (b) it sends NO pause at all while ignoreStopEvent is set, which is a ~2 s window around
#       a volatile init - i.e. exactly when another service may be taking the device.
# Fix: spop gains handoffAudioDevice(), which forces the pause regardless of ignoreStopEvent
# and resolves true only once go-librespot's own /status reports it is not playing. The mpd
# guard awaits it and FAILS CLOSED - if the release is not confirmed, local playback does not
# start, and the operator gets a toast saying so.
#
# FINDING 2 (non-blocking) - freeAudioDevice's rollback was incomplete: it restored
# currentStatus but left currentSeek pinned at 0 and the playback timer stopped, so a failed
# stop left the state machine reporting 'play' with a wrong position and no timer - a worse
# lie than the one the rollback exists to avoid. Now restores every field it changed.
#
# FINDING 7 (non-blocking) - a trusted clean exit in camilladsp-js.js did not reset the
# failure counter, so a later early exit was counted as attempt N instead of attempt 1.
#
# NOT addressed here, deliberately (recorded as accepted residual, see the ops record):
#   F3 (re-entrancy guard drops a second release request) - the body is synchronous, so two
#      events cannot interleave inside it; making it queue would add state for no reachable case.
#   F4 (state marked play before the release) - ordering is required, because
#      identifyPlaybackMode runs from the same handler and needs the playing state.
#   F5 (no proof that a takeover always emits 'playing' first) - cannot be established without
#      a real Connect client; recorded as the one branch the device cannot drive.
#   F6/F7-adjacent clean-exit churn - bounded in practice: each cycle needs an external writer
#      to open and close the fifo, and the worst case is one camilladsp start per 2 s.
#   F8 (100 ms is not proven sufficient) - accepted; the alternative was the escalating delay.

apply = '--apply' in sys.argv
BK = 'DEVICE_HOME/pi5-fix-backup-20260921-021521/'

SPOP = '/data/plugins/music_service/spop/index.js'
MPD = '/volumio/app/plugins/music_service/mpd/index.js'
CAM = '/data/plugins/audio_interface/fusiondsp/camilladsp-js.js'

edits = {}   # path -> list of (old, new)


# ---------------------------------------------------------------- spop A: handoffAudioDevice
SPOP_STOP_OLD = """ControllerSpotify.prototype.stop = function () {
    this.logger.info('Spotify Stop');
    var defer = libQ.defer();

    this.debugLog('SPOTIFY STOP');
    this.debugLog(JSON.stringify(currentVolumioState))
    if (!ignoreStopEvent) {
        this.sendSpotifyLocalApiCommand('/player/pause');
    }

    defer.resolve('');
    return defer.promise;
};"""

SPOP_STOP_NEW = SPOP_STOP_OLD + """

// Hand the audio device to another service, and report whether that actually happened.
//
// stop() is NOT sufficient for this, for two independent reasons found by independent review
// on 2026-09-21:
//   1. while ignoreStopEvent is set - a window of about 2 s around a volatile init, which is
//      precisely when another service may be taking the device - it sends no pause at all;
//   2. it resolves immediately and never confirms that go-librespot paused, so a caller that
//      starts its own playback on the strength of that resolution can still collide with
//      Spotify on the single shared fifo.
// This forces the pause regardless of ignoreStopEvent and resolves true only once
// go-librespot's own /status reports it is no longer playing, so the caller can fail closed
// instead of assuming a release happened.
ControllerSpotify.prototype.handoffAudioDevice = function () {
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


# ------------------------------------------------------------------ spop B: complete rollback
SPOP_ROLLBACK_OLD = """        var sm = self.commandRouter.stateMachine;
        var prevStatus = null;
        if (sm) {
            try {
                prevStatus = sm.currentStatus;
                sm.currentStatus = 'stop';
                if (prevStatus === 'play') {
                    sm.currentSeek = 0;
                }
                if (typeof sm.stopPlaybackTimer === 'function') {
                    sm.stopPlaybackTimer();
                }
            } catch (e) {
                self.logger.error('freeAudioDevice: could not disarm the state machine: ' + e);
            }
        }"""

SPOP_ROLLBACK_NEW = """        var sm = self.commandRouter.stateMachine;
        var prevStatus = null;
        var prevSeek = null;
        var stoppedTimer = false;
        if (sm) {
            try {
                prevStatus = sm.currentStatus;
                prevSeek = sm.currentSeek;
                sm.currentStatus = 'stop';
                if (prevStatus === 'play') {
                    sm.currentSeek = 0;
                }
                if (typeof sm.stopPlaybackTimer === 'function') {
                    sm.stopPlaybackTimer();
                    stoppedTimer = true;
                }
            } catch (e) {
                self.logger.error('freeAudioDevice: could not disarm the state machine: ' + e);
            }
        }"""

SPOP_FAIL_OLD = """            self.logger.error('freeAudioDevice: MPD stop failed, state restored: ' + e);
            if (sm && prevStatus !== null) {
                try { sm.currentStatus = prevStatus; } catch (e2) {}
            }"""

SPOP_FAIL_NEW = """            // v9 (2026-09-21): the rollback must be COMPLETE. Independent review found that
            // only currentStatus was restored, so a failed stop left the state machine
            // reporting 'play' with the seek pinned at 0 and its playback timer stopped - a
            // worse lie than the one this block exists to avoid. Restore every field changed
            // above: status, seek, and the timer if we were the ones who stopped it.
            self.logger.error('freeAudioDevice: MPD stop failed, state restored: ' + e);
            if (sm) {
                try {
                    if (prevStatus !== null) {
                        sm.currentStatus = prevStatus;
                    }
                    if (prevSeek !== null) {
                        sm.currentSeek = prevSeek;
                    }
                    if (stoppedTimer && prevStatus === 'play' && typeof sm.startPlaybackTimer === 'function') {
                        sm.startPlaybackTimer(prevSeek);
                    }
                } catch (e2) {
                    self.logger.error('freeAudioDevice: could not fully restore the state machine: ' + e2);
                }
            }"""


# ------------------------------------------------------- mpd: fail-closed release before start
MPD_GUARD_OLD = """  try {
    var cs = self.commandRouter.pluginManager.getPlugin('music_service', 'spop');
    if (cs && typeof cs.stop === 'function') {
      var csm = self.commandRouter.stateMachine;
      var spopStopped = !!(cs.state && cs.state.status === 'stop');
      var definitelyIdle = spopStopped && !(csm && csm.isVolatile === true);
      if (!definitelyIdle) {
        self.logger.info('MPD taking over: releasing the audio device from spop');
        cs.stop();
      }
    }
  } catch (e) {
    self.logger.error('Could not release the audio device from spop: ' + e);
  }"""

MPD_GUARD_NEW = """  // v9 (2026-09-21): FAIL CLOSED. Independent review found that this guard logged a failed
  // release and then started local playback anyway - the exact collision it exists to prevent.
  // Two independent holes: cs.stop() swallows its own failures and resolves immediately
  // without confirming the pause, and it sends NO pause at all while spop's ignoreStopEvent is
  // set (a ~2 s window around a volatile init - precisely when another service may be taking
  // the device). spop's handoffAudioDevice() forces the pause and resolves true only once
  // go-librespot reports it is not playing; local playback starts only if that is confirmed.
  var cs = null;
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
  }

  if (!releaseNeeded) {
    return self._mpdStartLocalPlayback(track);
  }

  self.logger.info('MPD taking over: releasing the audio device from spop');
  var handoff;
  if (typeof cs.handoffAudioDevice === 'function') {
    handoff = cs.handoffAudioDevice();
  } else {
    // spop predates the confirming handoff. Best effort only, and say so plainly rather than
    // pretend the release was confirmed.
    self.logger.error('MPD taking over: spop has no handoffAudioDevice(); the release cannot be confirmed');
    try { cs.stop(); } catch (e) { self.logger.error('Could not release the audio device from spop: ' + e); }
    handoff = libQ.resolve(false);
  }

  return handoff.then(function (released) {
    if (released !== true) {
      self.logger.error('MPD taking over: Spotify did not confirm the release; NOT starting local playback');
      self.commandRouter.pushToastMessage('error', 'Playback',
        'Spotify still holds the audio device - local playback not started.');
      return '';
    }
    return self._mpdStartLocalPlayback(track);
  });
};

// The original body of clearAddPlayTrack, reached only once the audio device is free.
ControllerMpd.prototype._mpdStartLocalPlayback = function (track) {
  var self = this;"""


# ------------------------------------------------- camilladsp-js: reset streak on trusted exit
CAM_OLD = """        if (!cleanExitAfterPlayback) {

            // Increment consecutive respawn counter
            consecutiveRespawns++;"""

CAM_NEW = """        if (cleanExitAfterPlayback) {

            // Reviewed finding 7 (2026-09-21): a trusted clean exit is a COMPLETED session, so
            // clear any failure streak. Without this, a count left over from earlier early
            // exits makes a later early exit look like attempt N rather than attempt 1, and the
            // "this was a normal stop" semantics are only half applied.
            consecutiveRespawns = 0;

        } else {

            // Increment consecutive respawn counter
            consecutiveRespawns++;"""


# ================================================================ apply with per-file checks
def patch(path, pairs, checks_before, checks_after, label):
    src = open(path).read()
    for old, new in pairs:
        assert src.count(old) == 1, '%s: anchor not uniquely located (%d matches):\n%s' % (
            label, src.count(old), old[:120])
    for c in checks_before:
        assert c in src, '%s: precondition missing: %s' % (label, c)
    out = src
    for old, new in pairs:
        out = out.replace(old, new)
    for c in checks_after:
        assert c in out, '%s: postcondition missing: %s' % (label, c)
    assert len(out) > len(src), '%s: no growth' % label
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


# --- preconditions that must hold across the whole change --------------------------------
spop_pre = ["'use strict';", 'spotifyLocalApiEndpointBase', "require('kew')"]
mpd_pre = ['var libQ = require']

spop_src_now = open(SPOP).read()
mpd_src_now = open(MPD).read()
cam_src_now = open(CAM).read()

assert 'handoffAudioDevice' not in spop_src_now, 'spop already patched (v9)'
assert '_mpdStartLocalPlayback' not in mpd_src_now, 'mpd already patched (v9)'
assert 'cleanExitAfterPlayback' in cam_src_now, 'camilladsp-js is not at v8 - patch v8 first'
assert 'Reviewed finding 7' not in cam_src_now, 'camilladsp-js already patched (v9)'

spop_out = patch(
    SPOP,
    [(SPOP_STOP_OLD, SPOP_STOP_NEW), (SPOP_ROLLBACK_OLD, SPOP_ROLLBACK_NEW), (SPOP_FAIL_OLD, SPOP_FAIL_NEW)],
    spop_pre,
    ['handoffAudioDevice', 'var prevSeek = null;', 'var stoppedTimer = false;', 'startPlaybackTimer(prevSeek)'],
    'spop/index.js - confirming handoff + complete rollback',
)

mpd_out = patch(
    MPD,
    [(MPD_GUARD_OLD, MPD_GUARD_NEW)],
    mpd_pre,
    ['_mpdStartLocalPlayback', 'FAIL CLOSED', 'pushToastMessage'],
    'mpd/index.js - fail-closed release before local playback',
)
assert mpd_out.count('_mpdStartLocalPlayback') == 3, 'expected: 2 calls + definition'
assert mpd_out.count('ControllerMpd.prototype.clearAddPlayTrack = function (track) {') == 1
assert 'cs.stop();' not in mpd_out.replace('try { cs.stop(); }', ''), 'a silent best-effort stop survived'

cam_out = patch(
    CAM,
    [(CAM_OLD, CAM_NEW)],
    ['consecutiveRespawns', 'cleanExitAfterPlayback'],
    ['consecutiveRespawns = 0;'],
    'fusiondsp/camilladsp-js.js - reset the failure streak on a trusted clean exit',
)

if apply:
    open(BK + 'spop-index.v7.js', 'w').write(spop_src_now)
    open(BK + 'mpd-index.v3.js', 'w').write(mpd_src_now)
    open(BK + 'camilladsp-js.v8.js', 'w').write(cam_src_now)
    open(SPOP, 'w').write(spop_out)
    open(MPD, 'w').write(mpd_out)
    open(CAM, 'w').write(cam_out)
    print()
    for p in (SPOP, MPD, CAM):
        print('  APPLIED %s -> %s' % (hashlib.sha256(open(p).read().encode()).hexdigest()[:16], p))
else:
    print('\n  DRY RUN only')
