import sys, hashlib, difflib
apply = '--apply' in sys.argv
BK = 'DEVICE_HOME/pi5-fix-backup-20260921-021521/'
P = '/data/plugins/music_service/spop/index.js'
src = open(P).read()

# ---- change 1: freeAudioDevice must release based on MPD's real state, not the router's claim
start = src.index('// Release the shared audio device')
end = src.index('ControllerSpotify.prototype.identifyPlaybackMode')
old = src[start:end]
assert old.count('ControllerSpotify.prototype.freeAudioDevice') == 1, 'v3 function not uniquely located'
assert '_freeingAudioDevice' in old, 'expected the v3 body here'

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
// RELEASE UNCONDITIONALLY, and do not trust the router's service/status fields to decide.
// Measured on this device: during a takeover the router already reported
// `status=play service=spop volatile=true` while the local player was still audibly
// streaming - the UI showed the Spotify track, the sound was the local file, and the two
// writers broke the audio up. An earlier version returned early when the router said
// service === 'spop' and therefore did nothing in exactly the case it existed for.
// Stopping an already-idle MPD is a no-op, so the asymmetry is safe: a wrong guess here
// costs one redundant stop, while a missed release costs audible break-up and a dead UI.
//
// Stopped through the mpd plugin directly, NOT via volumioStop()/volumioPause(): those are
// volatile-aware and would stop Spotify instead of the local player.
ControllerSpotify.prototype.freeAudioDevice = function () {
    var self = this;
    // Re-entrancy guard. The body below is synchronous from the state read to the stop
    // call, so under Node's single-threaded event loop two events cannot interleave inside
    // it; this flag makes that property explicit and keeps it true if the body ever gains
    // asynchronous work.
    if (self._freeingAudioDevice) {
        return;
    }
    self._freeingAudioDevice = true;
    try {
        // Resolve and validate the stopper BEFORE touching any state. Disarming while the
        // mpd plugin is unavailable would leave the state machine claiming 'stop' while MPD
        // may still hold the fifo - the inverse of the inconsistency this exists to prevent.
        var mpdPlugin = self.commandRouter.pluginManager.getPlugin('music_service', 'mpd');
        if (!mpdPlugin || typeof mpdPlugin.stop !== 'function') {
            self.logger.info('freeAudioDevice: mpd plugin not available, nothing released');
            return;
        }

        // Logged, not trusted: this line is the evidence for what the router claimed at the
        // moment of the takeover.
        var current = self.commandRouter.volumioGetState() || {};
        self.logger.info('Spotify taking over (router said service=' + current.service +
                         ', status=' + current.status + '): releasing the audio device');

        var sm = self.commandRouter.stateMachine;
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

# ---- change 2: also fire the release from the 'playing' event
a2 = """        case 'playing':
            playbackStartConfirmed = true;
            self.state.status = 'play';
            self.identifyPlaybackMode(event.data);"""
assert src.count(a2) == 1, "the 'playing' case is not uniquely located"
n2 = """        case 'playing':
            playbackStartConfirmed = true;
            self.state.status = 'play';
            // Belt and braces: the release is armed on 'will_play' as well, but if that
            // event is ever absent or arrives out of order this is the event that always
            // follows Spotify actually starting. freeAudioDevice is idempotent and a
            // redundant stop of an idle MPD is a no-op.
            self.freeAudioDevice();
            self.identifyPlaybackMode(event.data);"""

out1 = src[:start] + NEW + src[end:]
out = out1.replace(a2, n2)

assert out.count('ControllerSpotify.prototype.freeAudioDevice = function') == 1
assert out.count('self.freeAudioDevice();') == 2, 'expected exactly the will_play call plus the new playing call'
assert "current.service === 'spop'" not in out, 'the router-trusting bail-out must be gone'
assert out.count("sm.currentStatus = 'stop';") == 1
assert 'self._freeingAudioDevice' in out
assert len(out) != len(src)

print('=== PATCH v4 (spop) ===')
print('  bytes: %d -> %d (%+d)' % (len(src), len(out), len(out) - len(src)))
for l in difflib.unified_diff(src.splitlines(), out.splitlines(), 'v3', 'v4', lineterm='', n=1):
    print('    ' + l)
print('  staged sha256: ' + hashlib.sha256(out.encode()).hexdigest())
open('/tmp/spop-index.v4.js', 'w').write(out)
if apply:
    open(BK + 'spop-index.v3.js', 'w').write(src)
    open(P, 'w').write(out)
    print('  APPLIED. live sha256: ' + hashlib.sha256(open(P).read().encode()).hexdigest())
else:
    print('  DRY RUN only')
