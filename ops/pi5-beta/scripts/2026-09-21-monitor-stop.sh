#!/bin/bash
# Stop every running pi5 monitor instance, reliably.
#
# Why this exists - three earlier cleanup methods all failed, and each failure caused real
# damage before it was noticed:
#   1. `ps -eo comm,args | grep -E 'journalctl|sampler|scrot'` cannot see the bash loop whose
#      args are just "bash /data/pi5-mon/start.sh", and scrot exists for ~50 ms every 2 s.
#   2. An fd scan for holders of the monitor dir only sees processes holding one of those
#      files open at that instant; the sampler opens them per-write.
#   3. Killing by cmdline leaves the child journalctl behind: its cmdline contains the
#      redirect's TARGET but not the path, so it matches no pattern and is reparented to
#      init, still writing to journal.log.
#
# Result of those failures: restarting the monitor ACCUMULATED instances (three were once
# found at once, plus orphaned journal streams), all writing the same state.sig - which made
# the screen capture fire every tick instead of on change.
#
# This enumerates /proc/*/cmdline directly with patterns assembled at runtime, excludes its
# own ancestry, and kills whole PROCESS GROUPS so children (journalctl, scrot) go too.

python3 - <<'PYK'
import os, signal

# Assembled at runtime so this script and its caller cannot match their own command line.
pat_run = '/data/' + 'pi5' + '-' + 'mon'                       # start.sh, sampler.py
pat_journal = 'journalctl -f -o ' + 'short-precise'            # the journal stream

# Our own ancestry: self, parents, up to init. Never touch these.
ancestry = set()
p = os.getpid()
while p and p > 1:
    ancestry.add(p)
    try:
        p = int(open('/proc/%d/stat' % p).read().split()[3])
    except Exception:
        break

targets = {}
for pid in sorted(os.listdir('/proc')):
    if not pid.isdigit():
        continue
    ipid = int(pid)
    if ipid in ancestry:
        continue
    try:
        cl = open('/proc/%s/cmdline' % pid, 'rb').read().decode('utf8', 'replace').replace('\0', ' ').strip()
    except Exception:
        continue
    if not cl:
        continue
    if pat_run in cl or pat_journal in cl:
        try:
            pgid = os.getpgid(ipid)
        except Exception:
            pgid = None
        targets[ipid] = (cl[:78], pgid)

if not targets:
    print('  no monitor instances running')
else:
    for ipid, (cl, pgid) in sorted(targets.items()):
        print('  found pid=%d pgid=%s  %s' % (ipid, pgid, cl))

# Kill by process group where the group is distinct, so journalctl/scrot children go too.
# NEVER kill our own process group: if this script's caller happens to share it, a group kill
# would take out the shell that invoked us.
my_pgid = os.getpgid(os.getpid())
groups_done = set()
for ipid, (cl, pgid) in sorted(targets.items()):
    if pgid and pgid != my_pgid and pgid not in groups_done:
        try:
            os.killpg(pgid, signal.SIGKILL)
            groups_done.add(pgid)
            print('  killed group %d' % pgid)
        except Exception:
            pass
    try:
        os.kill(ipid, signal.SIGKILL)
        print('  killed pid %d' % ipid)
    except Exception as e:
        print('  could not kill %d: %s' % (ipid, e))
PYK

sleep 2
# Verify with the same reliable method, plus an explicit orphan check.
python3 - <<'PYK'
import os
pat_run = '/data/' + 'pi5' + '-' + 'mon'
pat_journal = 'journalctl -f -o ' + 'short-precise'
ancestry = set()
p = os.getpid()
while p and p > 1:
    ancestry.add(p)
    try:
        p = int(open('/proc/%d/stat' % p).read().split()[3])
    except Exception:
        break
left = []
for pid in os.listdir('/proc'):
    if not pid.isdigit() or int(pid) in ancestry:
        continue
    try:
        cl = open('/proc/%s/cmdline' % pid, 'rb').read().decode('utf8', 'replace').replace('\0', ' ').strip()
    except Exception:
        continue
    if cl and (pat_run in cl or pat_journal in cl):
        left.append((pid, cl[:70]))
for pid, cl in left:
    print('  STILL RUNNING %s  %s' % (pid, cl))
print('  verified: %d monitor processes remain' % len(left))
PYK
