#!/bin/bash
# blank-display.sh - darken the panel when the kiosk stops as part of a system
# power-down, so a stopped machine does not sit showing a frozen frame.
#
# WHY: Volumio's UI power-off is a software stop (systemctl power-off, with a /sbin
# fallback). It brings the OS down but does NOT remove the 5V input, so the panel keeps
# its last frame and the machine looks "on" when it is not.
#
# Runs as the kiosk's own user (User=volumio). No privileges required - xset talks to
# our own X session.
#
# Test without powering the machine down:  BLANK_FORCE=1 /home/volumio/blank-display.sh
# Restore afterwards:                      /home/volumio/blank-display.sh --unblank

LOG=/data/display-blank.log
say() { echo "$(date -Is) $*" >> "$LOG" 2>/dev/null; }

# Resolve the live X display from the kiosk browser itself: the display number is NOT
# stable across boots (seen on both :0 and :1), so never hard-code it.
kiosk_env() {
  local pid
  pid=$(pgrep -f 'chromium.*localhost:4004' | head -1)
  [ -z "$pid" ] && pid=$(pgrep -f 'chromium.*--kiosk' | head -1)
  [ -n "$pid" ] && [ -r "/proc/$pid/environ" ] || return 1
  KPID="$pid"
  KXA=$(tr '\0' '\n' < "/proc/$pid/environ" | sed -n 's/^XAUTHORITY=//p')
  KD=$(tr '\0' '\n' < "/proc/$pid/environ" | sed -n 's/^DISPLAY=//p')
  [ -n "$KD" ]
}

unblank() {
  if kiosk_env; then
    XAUTHORITY="$KXA" DISPLAY="$KD" /usr/bin/xset dpms force on 2>>"$LOG"
    say "unblank requested on $KD"
  else
    say "unblank: no kiosk display found"
  fi
  exit 0
}
[ "${1:-}" = "--unblank" ] && unblank

# Only act when the system is actually going down; a plain kiosk restart should not
# leave the panel dark.
if [ "${BLANK_FORCE:-0}" != "1" ]; then
  SYSSTATE=$(systemctl is-system-running 2>/dev/null)
  case "$SYSSTATE" in
    stopping|maintenance|offline) : ;;
    *) say "system state '$SYSSTATE' is not a power-down - not blanking"; exit 0 ;;
  esac
fi

if kiosk_env; then
  if XAUTHORITY="$KXA" DISPLAY="$KD" /usr/bin/xset dpms force off 2>>"$LOG"; then
    STATE=$(XAUTHORITY="$KXA" DISPLAY="$KD" /usr/bin/xset q 2>/dev/null | grep -i "Monitor is" | head -1)
    say "blanked via DPMS on $KD -> ${STATE:-unknown}"
    exit 0
  fi
  say "xset DPMS off failed on $KD"
fi

# Fallback if the X session is already gone (best effort; firmware path)
if /usr/bin/vcgencmd display_power 0 >>"$LOG" 2>&1; then
  say "blanked via vcgencmd display_power 0"
else
  say "no blanking mechanism succeeded (X already down?)"
fi
exit 0
