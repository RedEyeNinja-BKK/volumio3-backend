import sys, hashlib, difflib, subprocess, tempfile, os

# v7: a PAUSE must not re-arm volatile ownership.
#
# Observed live on 2026-09-21 (monitor capture, section 17 of the pi5-beta record):
#
#   12:44:12.511  router=play/mpd/vol=False     <- MPD has taken the device, correctly
#   12:44:12.672  SPOTIFY: received: {"type":"paused","data":{...,"play_origin":"your_library"}}
#   12:44:12.672  info: Spotify is playing in volatile mode     <- for an event that means it STOPPED
#   12:44:14.761  router=pause/spop/vol=True    <- and it stays pinned there
#
# Chain: the mpd-side guard stops Spotify to release the device, so the plugin sends /player/pause.
# go-librespot emits a `paused` event whose play_origin is 'your_library'. The `paused` case calls
# identifyPlaybackMode, which classifies on play_origin ALONE - it is never told which event called it -
# sees the router on 'mpd', and therefore calls initializeSpotifyPlaybackInVolatileMode() ->
# setVolatile({service:'spop'}). The router is re-pinned to Spotify while MPD owns the fifo and keeps
# playing, until something unsets volatile. Measured persisting >2 minutes.
#
# A pause is not a takeover. Ownership is claimed only on an event that means Spotify is TAKING
# playback ('playing', which is the only claim site). The classification of isInVolatileMode itself is
# left exactly as it was: it is a module-level var read only inside this function, so preserving its
# assignment changes nothing.
#
# setVolatile has exactly ONE call path (initializeSpotifyPlaybackInVolatileMode <- this site), so this
# gate is a single choke point, not a partial fix.

apply = '--apply' in sys.argv
BK = 'DEVICE_HOME/pi5-fix-backup-20260921-021521/'
P = '/data/plugins/music_service/spop/index.js'
src = open(P).read()

SIG_OLD = "ControllerSpotify.prototype.identifyPlaybackMode = function (data) {"
SIG_NEW = "ControllerSpotify.prototype.identifyPlaybackMode = function (data, isTakeoverEvent) {"

PLAY_SITE_OLD = """            self.freeAudioDevice();
            self.identifyPlaybackMode(event.data);"""
PLAY_SITE_NEW = """            self.freeAudioDevice();
            // v7: only a 'playing' event means Spotify is TAKING playback, so this is the only site
            // allowed to claim volatile ownership.
            self.identifyPlaybackMode(event.data, true);"""

PAUSE_BLOCK_OLD = """        case 'paused':
            self.state.status = 'pause';
            self.identifyPlaybackMode(event.data);
            pushStateforEvent = true;
            break;"""
PAUSE_BLOCK_NEW = """        case 'paused':
            self.state.status = 'pause';
            // v7 (2026-09-21): pass false - a pause is NOT a takeover. Calling this with the pause
            // event was re-arming volatile ownership: our own mpd-side release pauses Spotify, that
            // pause carries play_origin 'your_library', the classification ran against a router that
            // had just moved to 'mpd', and setVolatile re-pinned 'spop' as the owner of a device MPD
            // was playing into. Measured: router stuck at pause/spop/volatile=True for over two
            // minutes while mpc advanced and held the only fifo write fd. See the record's section 17.
            self.identifyPlaybackMode(event.data, false);
            pushStateforEvent = true;
            break;"""

GATE_OLD = """    // Refactor in order to handle the case where current service is spop but not in volatile mode
    if ((isInVolatileMode && currentVolumioState.service !== 'spop') ||"""
GATE_NEW = """    // v7 (2026-09-21): ownership is claimed ONLY on a takeover event. See the 'paused' call site.
    // Without this gate a pause re-arms volatile against a router that has already moved to the local
    // player, and the router then misreports who owns the audio device.
    if (!isTakeoverEvent) {
        return;
    }

    // Refactor in order to handle the case where current service is spop but not in volatile mode
    if ((isInVolatileMode && currentVolumioState.service !== 'spop') ||"""

# --- preconditions ------------------------------------------------------------------
assert src.startswith("'use strict';"), 'file is not in strict mode - re-read before trusting this patch'
assert 'isTakeoverEvent' not in src, 'already patched (v7)'
assert src.count(SIG_OLD) == 1, 'identifyPlaybackMode signature not uniquely located'
assert src.count('self.identifyPlaybackMode(event.data);') == 2, \
    'expected exactly two bare call sites (playing + paused)'
assert src.count(PLAY_SITE_OLD) == 1, 'the playing call site is not uniquely located'
assert src.count(PAUSE_BLOCK_OLD) == 1, 'the paused case block is not uniquely located'
assert src.count(GATE_OLD) == 1, 'the ownership condition is not uniquely located'
# the choke point: setVolatile must still have exactly one call path
assert src.count('self.initializeSpotifyPlaybackInVolatileMode();') == 1, 'unexpected extra claim site'
assert src.count('stateMachine.setVolatile(') == 1, 'unexpected extra setVolatile call'

out = src
out = out.replace(PLAY_SITE_OLD, PLAY_SITE_NEW)
out = out.replace(PAUSE_BLOCK_OLD, PAUSE_BLOCK_NEW)
out = out.replace(GATE_OLD, GATE_NEW)
out = out.replace(SIG_OLD, SIG_NEW)

# --- postconditions -----------------------------------------------------------------
assert out.count('self.identifyPlaybackMode(event.data)') == 0, 'a bare (flagless) call site survived'
assert out.count('self.identifyPlaybackMode(event.data, true);') == 1
assert out.count('self.identifyPlaybackMode(event.data, false);') == 1
assert out.count('isTakeoverEvent') == 2, 'expected exactly: signature + gate'
assert out.count('currentVolumioState.service !== \'spop\'') == 1, 'ownership condition damaged'
assert out.count('self.initializeSpotifyPlaybackInVolatileMode();') == 1, 'claim site count changed'
assert out.count('callback: self.libRespotGoUnsetVolatile.bind(self)') == 1, 'v6 fix was lost'
assert out.count('self.freeAudioDevice();') == src.count('self.freeAudioDevice();'), \
    'freeAudioDevice call sites changed'
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

print('=== PATCH v7 (spop, a pause must not claim volatile ownership) ===')
print('  bytes: %d -> %d (%+d)' % (len(src), len(out), len(out) - len(src)))
print('  ' + syntax)
for l in difflib.unified_diff(src.splitlines(), out.splitlines(), 'v6', 'v7', lineterm='', n=2):
    print('    ' + l)
print('  staged sha256: ' + hashlib.sha256(out.encode()).hexdigest())
if apply:
    open(BK + 'spop-index.v6.js', 'w').write(src)
    open(P, 'w').write(out)
    print('  APPLIED. live sha256: ' + hashlib.sha256(open(P).read().encode()).hexdigest())
else:
    print('  DRY RUN only')
