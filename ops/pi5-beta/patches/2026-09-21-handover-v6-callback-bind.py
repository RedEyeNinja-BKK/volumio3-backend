import sys, hashlib, difflib, subprocess, tempfile, os

# v6: v5 fixed the WRONG-TIME invocation by passing the function reference. That made the
# core actually invoke the callback - and invoking it kills the core.
#
# CoreStateMachine.unSetVolatile does `this.volatileCallback.call()` - a bare call with no
# thisArg. spop/index.js is 'use strict', so inside the callback `this` is undefined and
# `var self = this` yields undefined, so the very next statement throws:
#
#     TypeError: Cannot read properties of undefined (reading 'debugLog')
#         at ControllerSpotify.libRespotGoUnsetVolatile (spop/index.js:535:10)
#         at CoreStateMachine.unSetVolatile (statemachine.js:1555:27)
#         at CoreCommandRouter.volumioPlay (index.js:1406:21)
#
# The core's uncaught-exception handler then exits the process (systemd: status=1/FAILURE).
# .bind(self) makes the callback carry its own receiver, so it is correct whether the core
# calls it bare, with .call(), or as a method.

apply = '--apply' in sys.argv
BK = 'DEVICE_HOME/pi5-fix-backup-20260921-021521/'
P = '/data/plugins/music_service/spop/index.js'
src = open(P).read()

OLD = "        callback: self.libRespotGoUnsetVolatile\n"
NEW = """        // v6 (2026-09-21): .bind(self) is REQUIRED, not stylistic. The core invokes this
        // callback as `volatileCallback.call()` - bare, with no thisArg. This file is
        // 'use strict', so an unbound function would run with `this === undefined`, making
        // `var self = this` undefined and the next line throw
        // `TypeError: ... reading 'debugLog'`. That exception is uncaught and exits the
        // core (systemd status=1/FAILURE), observed twice on 2026-09-21 at 12:30:23 and
        // 12:31:08 on the volatile path v5 was meant to fix. Bind it so it carries its own
        // receiver however the core calls it. Rollback:
        // DEVICE_HOME/pi5-fix-backup-20260921-021521/spop-index.v5.js
        callback: self.libRespotGoUnsetVolatile.bind(self)
"""

# --- preconditions -----------------------------------------------------------------
assert src.count(OLD) == 1, 'the unbound callback line is not uniquely located'
assert 'libRespotGoUnsetVolatile.bind' not in src, 'already patched (v6)'
assert src.startswith("'use strict';"), 'file is not in strict mode - the whole rationale changes'
assert 'ControllerSpotify.prototype.libRespotGoUnsetVolatile = function () {' in src, \
    'the callback definition moved - re-read the source before trusting this patch'

out = src.replace(OLD, NEW)

# --- postconditions ----------------------------------------------------------------
assert out.count('callback: self.libRespotGoUnsetVolatile.bind(self)') == 1
assert 'callback: self.libRespotGoUnsetVolatile\n' not in out, 'an unbound registration survived'
assert len(out) == len(src) + (len(NEW) - len(OLD))
# the function that will now actually be called must still exist and still be a function literal
assert 'ControllerSpotify.prototype.libRespotGoUnsetVolatile = function () {' in out

# --- syntax check the staged bytes, not the live file -------------------------------
with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False) as t:
    t.write(out)
    staged = t.name
try:
    r = subprocess.run(['node', '--check', staged], capture_output=True, text=True)
    assert r.returncode == 0, 'node --check FAILED on the staged bytes:\n' + r.stderr
    syntax = 'node --check OK'
finally:
    os.unlink(staged)

print('=== PATCH v6 (spop, bind the volatile callback to its controller) ===')
print('  bytes: %d -> %d (%+d)' % (len(src), len(out), len(out) - len(src)))
print('  ' + syntax)
for l in difflib.unified_diff(src.splitlines(), out.splitlines(), 'v5', 'v6', lineterm='', n=2):
    print('    ' + l)
print('  staged sha256: ' + hashlib.sha256(out.encode()).hexdigest())
if apply:
    open(BK + 'spop-index.v5.js', 'w').write(src)
    open(P, 'w').write(out)
    print('  APPLIED. live sha256: ' + hashlib.sha256(open(P).read().encode()).hexdigest())
else:
    print('  DRY RUN only')
