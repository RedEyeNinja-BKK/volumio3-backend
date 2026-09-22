import sys, hashlib, difflib
P = '/data/plugins/music_service/spop/index.js'
apply = '--apply' in sys.argv
src = open(P).read()
start = src.index('// Release the shared audio device')
end = src.index('ControllerSpotify.prototype.identifyPlaybackMode')
old = src[start:end]
assert old.count('ControllerSpotify.prototype.freeAudioDevice') == 1, 'v1 function not uniquely located'
assert 'stopPlaybackTimer' not in old, 'unexpected: disarm already present'

NEW = '''// Release the shared audio device so an incoming Spotify session does not have to share
// the FusionDSP fifo with a local player. FusionDSP has a SINGLE fifo input, so a
// still-running local player interleaves with Spotify into one stream, and the DSP
// samplerate follows whichever client opened the fifo last - a local DSD stream written
// into a fifo the DSP has switched to Spotify's 44.1 kHz is inaudible.
//
// The state machine MUST be disarmed before the other service is stopped: syncState reads
// a service 'stop' arriving while currentStatus === 'play' as "the track finished" and
// advances the queue, restarting the local player straight back onto the device that was
// just freed. CoreStateMachine.prototype.stop() avoids that by setting currentStatus =
// 'stop' before calling serviceStop(); calling a plugin's stop() directly skips that step,
// so the same disarm is repeated here.
//
// Stopped through the mpd plugin directly, NOT via volumioStop()/volumioPause(): those are
// volatile-aware and would stop Spotify instead of the local player.
ControllerSpotify.prototype.freeAudioDevice = function () {
    var self = this;
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
        self.logger.info('Spotify taking over from ' + current.service + ': freeing the audio device');

        var sm = self.commandRouter.stateMachine;
        if (sm) {
            try {
                sm.currentStatus = 'stop';
                sm.currentSeek = 0;
                if (typeof sm.stopPlaybackTimer === 'function') {
                    sm.stopPlaybackTimer();
                }
            } catch (e) {
                self.logger.error('freeAudioDevice: could not disarm the state machine: ' + e);
            }
        }

        var mpdPlugin = self.commandRouter.pluginManager.getPlugin('music_service', 'mpd');
        if (mpdPlugin && typeof mpdPlugin.stop === 'function') {
            mpdPlugin.stop();
        } else {
            self.logger.info('freeAudioDevice: mpd plugin not available, nothing stopped');
        }
    } catch (e) {
        self.logger.error('freeAudioDevice failed: ' + e);
    }
};

'''
out = src[:start] + NEW + src[end:]
assert out.count('ControllerSpotify.prototype.freeAudioDevice = function') == 1
assert out.count('sm.currentStatus = \'stop\';') == 1
assert 'sm.stopPlaybackTimer()' in out
assert 'self.freeAudioDevice();' in out, 'call site in will_play must survive'
assert len(out) != len(src)
print('  bytes: %d -> %d (+%d)' % (len(src), len(out), len(out)-len(src)))
print('  diff:')
for l in difflib.unified_diff(old.splitlines(), NEW.splitlines(), 'v1', 'v2', lineterm='', n=1):
    print('    ' + l)
open('/tmp/spop-index.v2.js','w').write(out)
print('  staged sha256: ' + hashlib.sha256(out.encode()).hexdigest())
if apply:
    open('DEVICE_HOME/pi5-fix-backup-20260921-021521/spop-index.v1.js','w').write(src)
    open(P,'w').write(out)
    open(P+'.bak-v2','w').write(src)
    print('  APPLIED. live sha256: ' + hashlib.sha256(open(P).read().encode()).hexdigest())
else:
    print('  DRY RUN only')
