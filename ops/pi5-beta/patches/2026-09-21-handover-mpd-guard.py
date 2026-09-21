import sys, hashlib, difflib
P='/volumio/app/plugins/music_service/mpd/index.js'
apply='--apply' in sys.argv
src=open(P).read()
anchor="""ControllerMpd.prototype.clearAddPlayTrack = function (track) {
  var self = this;

  var sections = track.uri.split('/');"""
assert src.count(anchor)==1, 'anchor not unique'
assert 'MPD taking over' not in src, 'already patched'
GUARD = """ControllerMpd.prototype.clearAddPlayTrack = function (track) {
  var self = this;

  // Spotify (spop) is a volatile service that shares FusionDSP's single fifo input with us.
  // If it still holds the device when local playback starts, both services write into that
  // one fifo and the DSP samplerate follows whichever of them opened it last, so a local
  // DSD stream (384 kHz) lands in a fifo the DSP has switched to Spotify's 44.1 kHz and is
  // inaudible. Ask Spotify to release the device first; its stop() pauses go-librespot,
  // which closes its fifo handle. The state machine cannot do this for us here: its stop()
  // only releases the volatile service, and spop does not always have that flag set.
  try {
    var cs = self.commandRouter.pluginManager.getPlugin('music_service', 'spop');
    if (cs && typeof cs.stop === 'function') {
      var csm = self.commandRouter.stateMachine;
      var spotifyActive = (csm && csm.isVolatile === true) ||
                          (cs.state && cs.state.status === 'play');
      if (spotifyActive) {
        self.logger.info('MPD taking over: releasing the audio device from spop');
        cs.stop();
      }
    }
  } catch (e) {
    self.logger.error('Could not release the audio device from spop: ' + e);
  }

  var sections = track.uri.split('/');"""
out=src.replace(anchor,GUARD)
assert out.count('MPD taking over')==1
assert out.count("var sections = track.uri.split('/');")==1
assert 'cs.stop();' in out and "cs.state.status === 'play'" in out
print('  bytes: %d -> %d (+%d)'%(len(src),len(out),len(out)-len(src)))
for l in difflib.unified_diff(src.splitlines(),out.splitlines(),'before','after',lineterm='',n=2):
    if l.startswith(('+','-')) and not l.startswith(('+++','---')): print('    '+l)
print('  staged sha256: '+hashlib.sha256(out.encode()).hexdigest())
if apply:
    open('/home/volumio/pi5-fix-backup-20260921-021521/mpd-index.js.orig','w').write(src)
    open(P,'w').write(out)
    print('  APPLIED. live sha256: '+hashlib.sha256(open(P).read().encode()).hexdigest())
else: print('  DRY RUN only')
