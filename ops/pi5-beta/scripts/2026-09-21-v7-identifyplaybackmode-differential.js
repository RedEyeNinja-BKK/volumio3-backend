'use strict';
// Differential test of the SHIPPED bytes: v6 (backup) vs v7 (live).
//
// Hermetic: every heavy dependency is stubbed, the socket is a fake whose pushState
// handler we invoke ourselves, and initializeSpotifyPlaybackInVolatileMode is replaced
// with a counter so the real setVolatile never runs. Nothing touches the production core.
//
// The argument shape is not assumed - it is DERIVED from each file's own call site, so the
// test cannot pass by us calling the function the way we wish it were called.

const fs = require('fs');
const Module = require('module');

const socketHandlers = {};
const fakeSocket = {
  on(ev, cb) { (socketHandlers[ev] = socketHandlers[ev] || []).push(cb); return this; },
  off() { return this; }, disconnect() { return this; }, emit() { return this; },
  connect() { return this; }, close() { return this; },
};
class VConf {
  constructor() { this.store = {}; }
  loadFile() { return true; } get(k) { return this.store[k]; }
  set(k, v) { this.store[k] = v; } has() { return false; } delete() {}
}
const chain = () => ({ accept() { return this; }, send() { return this; }, timeout() { return this; },
                       then() { return this; }, catch() { return this; }, off() { return this; } });
const STUBS = {
  'kew': { defer: () => ({ resolve() {}, reject() {}, promise: {} }),
           resolve: () => ({ then() {} }), all: () => ({ then() {} }), reject: () => ({ then() {} }) },
  'v-conf': VConf,
  'fs-extra': { existsSync: () => false, readFileSync: () => '', writeFileSync() {},
                ensureDirSync() {}, pathExistsSync: () => false },
  'superagent': { get: chain, post: chain },
  'ws': class { constructor() {} on() {} },
  'spotify-web-api-node': class { constructor() {} },
  'socket.io-client': { connect: () => fakeSocket },
  'node-cache': class { constructor() {} get() {} set() {} },
  './utils/extendedSpotifyApi': { fetchPagedData: () => {}, rateLimitedCall: () => {} },
};
const origLoad = Module._load;
Module._load = function (request) {
  if (Object.prototype.hasOwnProperty.call(STUBS, request)) return STUBS[request];
  return origLoad.apply(this, arguments);
};

// how does THIS file's <case> branch call identifyPlaybackMode?
function callShape(file, caseName) {
  const src = fs.readFileSync(file, 'utf8');
  const m = src.match(new RegExp("case '" + caseName + "':([\\s\\S]*?)break;"));
  if (!m) throw new Error('case ' + caseName + ' not found in ' + file);
  const c = m[1].match(/identifyPlaybackMode\(event\.data([^)]*)\)/);
  if (!c) throw new Error('call site not found for case ' + caseName + ' in ' + file);
  return c[1].trim(); // '' or ', true' / ', false'
}

function invoke(inst, extra, data) {
  if (extra === '') return inst.identifyPlaybackMode(data);
  return inst.identifyPlaybackMode(data, extra.replace(/^,\s*/, '') === 'true');
}

function load(file) {
  delete require.cache[require.resolve(file)];
  // Clear the fake socket's handlers first: the fake is a singleton, so without this a
  // later load would invoke the EARLIER module's pushState listener and set that module's
  // currentVolumioState, leaving this one unset.
  for (const k of Object.keys(socketHandlers)) delete socketHandlers[k];
  const ControllerSpotify = require(file);
  const inst = new ControllerSpotify({
    coreCommand: {}, configManager: {},
    logger: { info() {}, error() {}, verbose() {} },
  });
  let claims = 0;
  ControllerSpotify.prototype.initializeSpotifyPlaybackInVolatileMode = function () { claims++; };
  inst.startSocketStateListener();          // registers the real pushState listener on our fake socket
  const push = (socketHandlers.pushState || [])[0];
  if (!push) throw new Error('pushState handler never registered for ' + file);
  // The router is on the LOCAL player - the exact condition measured at 12:44:12.
  push({ service: 'mpd', status: 'play', volatile: false });
  return { inst, claimsSoFar: () => claims };
}

const LIVE = '/data/plugins/music_service/spop/index.js';
const V6 = '/home/volumio/pi5-fix-backup-20260921-021521/spop-index.v6.js';

const pauseShapeV6 = callShape(V6, 'paused');
const pauseShapeV7 = callShape(LIVE, 'paused');
const playShapeV6 = callShape(V6, 'playing');
const playShapeV7 = callShape(LIVE, 'playing');

console.log('call shape derived from each file\'s own bytes:');
console.log('  v6  paused case -> identifyPlaybackMode(event.data' + pauseShapeV6 + ')');
console.log('  v7  paused case -> identifyPlaybackMode(event.data' + pauseShapeV7 + ')');
console.log('  v6  playing case-> identifyPlaybackMode(event.data' + playShapeV6 + ')');
console.log('  v7  playing case-> identifyPlaybackMode(event.data' + playShapeV7 + ')');
console.log('');
console.log('router state injected in every run: service=mpd status=play volatile=false');
console.log('play_origin in every Spotify event: "your_library"  (a phone-started session)');
console.log('');

// All three probes run against ONE instance, with ONE injected router state, so the paused
// result cannot be an artefact of a missing state: the paired takeover probe in the same
// instance proves the claim machinery is live.
function probeVersion(file, label) {
  const { inst, claimsSoFar } = load(file);
  const pauseShape = callShape(file, 'paused');
  const playShape = callShape(file, 'playing');

  let n = claimsSoFar();
  invoke(inst, pauseShape, { play_origin: 'your_library' });
  const cPause = claimsSoFar() - n;

  n = claimsSoFar();
  invoke(inst, playShape, { play_origin: 'your_library' });
  const cPlay = claimsSoFar() - n;

  n = claimsSoFar();
  invoke(inst, playShape, { play_origin: 'go-librespot' });
  const cLocal = claimsSoFar() - n;

  console.log('  ' + label);
  console.log('      paused,  origin=your_library   -> claim fired: ' + cPause);
  console.log('      playing, origin=your_library   -> claim fired: ' + cPlay + '   (state was injected: claim machinery is live)');
  console.log('      playing, origin=go-librespot   -> claim fired: ' + cLocal);
  return { cPause, cPlay, cLocal };
}

let failures = 0;
const expect = (got, want, what) => {
  const ok = got === want;
  if (!ok) failures++;
  console.log('      -> ' + (ok ? 'PASS' : 'FAIL') + '  ' + what + ' (want ' + want + ', got ' + got + ')');
};

console.log('=== v6 (the backup bytes, what was live before this deploy) ===');
const r6 = probeVersion(V6, 'v6 (as shipped)');
expect(r6.cPlay, 1, 'sanity: the takeover path claims, so the injected state is real');
expect(r6.cPause, 1, 'v6 REPRODUCES THE BUG: a pause re-claims volatile ownership');
expect(r6.cLocal, 0, 'control: Volumio-mode playback never claimed');
console.log('');
console.log('=== v7 (the bytes now live on the device) ===');
const r7 = probeVersion(LIVE, 'v7 (as shipped)');
expect(r7.cPlay, 1, 'the takeover path still claims (unchanged behaviour)');
expect(r7.cPause, 0, 'v7 FIXES THE BUG: a pause no longer claims volatile ownership');
expect(r7.cLocal, 0, 'control: Volumio-mode playback still claims nothing');
console.log('');
console.log(failures === 0 ? 'ALL EXPECTATIONS MET' : ('FAILURES: ' + failures));
process.exit(failures === 0 ? 0 : 1);
