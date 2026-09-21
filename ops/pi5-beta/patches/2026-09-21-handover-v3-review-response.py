import sys, hashlib, difflib
apply = '--apply' in sys.argv
BK = '/home/volumio/pi5-fix-backup-20260921-021521/'

def show(old, new, n=1):
    for l in difflib.unified_diff(old.splitlines(), new.splitlines(), 'v2', 'v3', lineterm='', n=n):
        print('    ' + l)

# ============================ patch 1: spop ============================
S = '/data/plugins/music_service/spop/index.js'
src = open(S).read()
start = src.index('// Release the shared audio device')
end = src.index('ControllerSpotify.prototype.identifyPlaybackMode')
old = src[start:end]
assert old.count('ControllerSpotify.prototype.freeAudioDevice') == 1, 'v2 function not uniquely located'
assert "sm.currentStatus = 'stop';" in old, 'expected the v2 body here'

NEW = '''// Release the shared audio device so an incoming Spotify session does not have to share
// the FusionDSP fifo with a local player. FusionDSP has a SINGLE fifo input, so a
// still-running local player interleaves with Spotify into one stream, and the DSP
// samplerate follows whichever client opened the fifo last - a local DSD stream written
// into a fifo the DSP has switched to Spotify's 44.1 kHz is inaudible.
//
// The state machine MUST be disarmed before the other service is stopped: syncState reads
// a service 'stop' arriving while currentStatus === 'play' as "the track finished" and
// advances the queue, restarting the local player straight back onto the device that was
// just freed. CoreStateMachine.prototype.stop() avoids that, but only on its NON-volatile
// branch: it opens with `if (this.isVolatile) { return this.serviceStop(); }`, and during a
// Spotify takeover isVolatile is true (the plugin has just called setVolatile), so the core
// would stop SPOTIFY and would touch none of MPD's state. Repeating the disarm here is
// therefore not a partial copy of stop() - on this path stop() performs no bookkeeping for
// the local service at all. That is also why unSetVolatile() is deliberately NOT called:
// clearing the volatile flag here would drop Spotify's ownership of the device.
//
// Stopped through the mpd plugin directly, NOT via volumioStop()/volumioPause(): those are
// volatile-aware and would stop Spotify instead of the local player.
ControllerSpotify.prototype.freeAudioDevice = function () {
    var self = this;
    // Re-entrancy guard. The body below is synchronous from the state read to the stop
    // call, so under Node's single-threaded event loop two 'will_play' events cannot
    // interleave inside it; this flag makes that property explicit and keeps it true if the
    // body ever gains asynchronous work.
    if (self._freeingAudioDevice) {
        return;
    }
    self._freeingAudioDevice = true;
    var sm = self.commandRouter.stateMachine;
    var prevStatus = null;
    try {
        var current = self.commandRouter.volumioGetState();
        if (!current || current.service === 'spop') {
            return;
        }
        // 'pause' is included deliberately: it is not established whether a paused MPD
        // still holds its output PCM open, and stopping an already-idle player is
        // harmless. Only 'stop' is genuinely nothing to do.
        if (current.status !== 'play' && current.status !== 'pause') {
            return;
        }

        // Resolve and validate the stopper BEFORE touching any state. Disarming while the
        // mpd plugin is unavailable would leave the state machine claiming 'stop' while MPD
        // may still hold the fifo - the inverse of the inconsistency this exists to prevent.
        var mpdPlugin = self.commandRouter.pluginManager.getPlugin('music_service', 'mpd');
        if (!mpdPlugin || typeof mpdPlugin.stop !== 'function') {
            self.logger.info('freeAudioDevice: mpd plugin not available, nothing disarmed');
            return;
        }

        self.logger.info('Spotify taking over from ' + current.service + ': freeing the audio device');

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
        }

        try {
            mpdPlugin.stop();
        } catch (e) {
            // MPD may still be holding the fifo, so do not leave the state machine claiming
            // that it stopped: put the pre-takeover status back and report loudly. That
            // reopens the queue-walk window this disarm closes, but a state machine that
            // lies about which service owns the device is the worse of the two failures.
            self.logger.error('freeAudioDevice: MPD stop failed, state restored: ' + e);
            if (sm && prevStatus !== null) {
                try { sm.currentStatus = prevStatus; } catch (e2) {}
            }
        }
    } catch (e) {
        self.logger.error('freeAudioDevice failed: ' + e);
    } finally {
        self._freeingAudioDevice = false;
    }
};

'''
out = src[:start] + NEW + src[end:]
assert out.count('ControllerSpotify.prototype.freeAudioDevice = function') == 1
assert out.count("sm.currentStatus = 'stop';") == 1
assert out.count('self._freeingAudioDevice') == 3
assert "prevStatus = sm.currentStatus;" in out and "sm.currentSeek = 0;" in out
assert 'self.freeAudioDevice();' in out, 'call site in will_play must survive'
assert len(out) != len(src)
print('=== PATCH 1 (spop) ===')
print('  bytes: %d -> %d (%+d)' % (len(src), len(out), len(out) - len(src)))
show(old, NEW, 1)
print('  staged sha256: ' + hashlib.sha256(out.encode()).hexdigest())
if apply:
    open(BK + 'spop-index.v2.js', 'w').write(src)
    open(S, 'w').write(out)
    print('  APPLIED. live sha256: ' + hashlib.sha256(open(S).read().encode()).hexdigest())

# ============================ patch 2: mpd ============================
P = '/volumio/app/plugins/music_service/mpd/index.js'
src2 = open(P).read()
a2 = "  // Spotify (spop) is a volatile service that shares FusionDSP's single fifo input with us."
b2 = "  var sections = track.uri.split('/');"
i = src2.index(a2)
j = src2.index(b2, i)
old2 = src2[i:j + len(b2)]
assert old2.count('MPD taking over') == 1, 'v2 guard not uniquely located'
assert 'spotifyActive' in old2, 'expected the v2 predicate here'

NEW2 = '''  // Spotify (spop) is a volatile service that shares FusionDSP's single fifo input with us.
  // If it still holds the device when local playback starts, both services write into that
  // one fifo and the DSP samplerate follows whichever of them opened it last, so a local
  // DSD stream (384 kHz) lands in a fifo the DSP has switched to Spotify's 44.1 kHz and is
  // inaudible. Ask Spotify to release the device first; its stop() pauses go-librespot,
  // which closes its fifo handle. The state machine cannot do this for us here: its stop()
  // only releases the volatile service, and spop does not always have that flag set.
  //
  // DEFAULT TO RELEASING. The release is skipped only when spop explicitly reports 'stop'
  // and is not volatile. A missing or stale spop state counts as "may still be playing":
  // falling through into local playback while Spotify holds the fifo is the exact collision
  // this guard exists to prevent, so "cannot prove Spotify is idle" must not mean "start
  // anyway". Calling stop() on an idle spop sends one pause to go-librespot and is otherwise
  // inert - measured, see the record.
  try {
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
  }

  var sections = track.uri.split('/');'''
out2 = src2[:i] + NEW2 + src2[j + len(b2):]
assert out2.count('MPD taking over') == 1
assert out2.count("var sections = track.uri.split('/');") == 1
assert 'definitelyIdle' in out2 and 'spopStopped' in out2
assert 'spotifyActive' not in out2, 'v2 predicate must be gone'
assert len(out2) != len(src2)
print()
print('=== PATCH 2 (mpd) ===')
print('  bytes: %d -> %d (%+d)' % (len(src2), len(out2), len(out2) - len(src2)))
show(old2, NEW2, 1)
print('  staged sha256: ' + hashlib.sha256(out2.encode()).hexdigest())
if apply:
    open(BK + 'mpd-index.v2.js', 'w').write(src2)
    open(P, 'w').write(out2)
    print('  APPLIED. live sha256: ' + hashlib.sha256(open(P).read().encode()).hexdigest())

if not apply:
    print()
    print('DRY RUN only - nothing written')
