import sys, hashlib, difflib
apply = '--apply' in sys.argv
BK = '/home/volumio/pi5-fix-backup-20260921-021521/'
P = '/data/plugins/music_service/spop/index.js'
src = open(P).read()

OLD = "        callback: self.libRespotGoUnsetVolatile()"
NEW = """        // NOTE (pi5-beta fix 2026-09-21): the parentheses were removed here on purpose.
        //
        // The callback is invoked BY THE CORE, from CoreStateMachine.unSetVolatile(), at the
        // moment volatile is unset - see the core's own comment on this field: "This function
        // will be called on volatile stop". Writing `self.libRespotGoUnsetVolatile()` CALLS it
        // here instead and registers the returned promise as the callback, so:
        //   (a) the body runs at setVolatile time, when currentVolumioState is still the local
        //       track that is playing, so its guard `status !== 'stop'` is satisfied and it
        //       schedules self.stop(); measured: Spotify is stopped ~0.5 s after it takes the
        //       device over, so it can never survive a volatile takeover;
        //   (b) the registered "callback" is a promise, not a function, so the core's
        //       volatileCallback.call() cannot work when volatile really is unset.
        // Passing the function reference restores the intended behaviour. Rollback:
        // /home/volumio/pi5-fix-backup-20260921-021521/spop-index.v4.js
        callback: self.libRespotGoUnsetVolatile"""

assert src.count(OLD) == 1, 'the called-callback line is not uniquely located'
assert 'spop-index.v4.js' not in src, 'already patched'
out = src.replace(OLD, NEW)
assert out.count('callback: self.libRespotGoUnsetVolatile\n') == 1
assert 'callback: self.libRespotGoUnsetVolatile()' not in out
assert len(out) != len(src)

print('=== PATCH v5 (spop, removes one pair of parentheses) ===')
print('  bytes: %d -> %d (%+d)' % (len(src), len(out), len(out) - len(src)))
for l in difflib.unified_diff(src.splitlines(), out.splitlines(), 'v4', 'v5', lineterm='', n=2):
    print('    ' + l)
print('  staged sha256: ' + hashlib.sha256(out.encode()).hexdigest())
if apply:
    open(BK + 'spop-index.v4.js', 'w').write(src)
    open(P, 'w').write(out)
    print('  APPLIED. live sha256: ' + hashlib.sha256(open(P).read().encode()).hexdigest())
else:
    print('  DRY RUN only')
