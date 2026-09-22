#!/bin/bash
# Disk-backed, bounded, IDEMPOTENT monitor for the pi5-beta audio path.
#
# /tmp on this device is RAM. Never write frames there - the first version of this monitor
# filled a 3.9G tmpfs to 93% (3.6 GB of RAM) in 1h40m.
#
# Idempotent: it stops any previous instance first, via stop.sh, whose enumeration is
# reliable where a `ps | grep` or an fd scan is not. Without that, each restart silently
# added another instance - three were once found running at the same time, all writing the
# same state.sig, which made the screen capture fire every tick instead of on change.

MON=/data/pi5-mon
mkdir -p "$MON/shots"

# Kill predecessors BEFORE this script's own children exist. stop.sh excludes its own
# ancestry, and this script is its parent, so this instance is safe.
[ -x "$MON/stop.sh" ] && "$MON/stop.sh"

echo "$$" > "$MON/monitor.pid"
export DISPLAY=:0 XAUTHORITY=DEVICE_HOME/.Xauthority
date '+START %Y-%m-%d %H:%M:%S.%3N' > "$MON/started.txt"

stdbuf -oL journalctl -f -o short-precise --no-pager > "$MON/journal.log" 2>&1 &
echo $! > "$MON/journal.pid"

python3 "$MON/sampler.py" &
echo $! > "$MON/sampler.pid"

(
  last=''; lastshot=0
  while true; do
    sig=$(cat "$MON/state.sig" 2>/dev/null)
    now=$(date +%s)
    # Capture on a state TRANSITION, plus a 60s heartbeat. state.sig is built from stable
    # fields only - deliberately not mpc's live position, which changes every second.
    if [ "$sig" != "$last" ] || [ $((now - lastshot)) -ge 60 ]; then
      E=$(date +%s.%3N); I=$(date '+%H:%M:%S.%3N')
      f=shot_${E}.png
      if scrot -o -t 55 "$MON/shots/$f" 2>/dev/null; then
        echo "$E $I SHOT $f sig=[$sig]" >> "$MON/timeline.log"
        last="$sig"
      else
        echo "$E $I SHOT_FAIL" >> "$MON/timeline.log"
      fi
      lastshot=$now
      # ring buffer: keep the newest 400 frames only
      ls -1t "$MON"/shots/*.png 2>/dev/null | tail -n +401 | xargs -r rm -f
    fi
    sleep 2
  done
) &
echo $! > "$MON/screens.pid"

wait
