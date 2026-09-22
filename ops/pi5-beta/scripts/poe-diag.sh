#!/bin/bash
# poe-diag.sh — capture power + WiFi bring-up evidence at boot.
#
# WHY: when this board is powered over PoE, Wi-Fi reportedly never comes up, and the
# box is then unreachable (SSH is over Wi-Fi; the PoE router carries no data). This
# script writes the evidence to /data BEFORE that happens, so it can be read after
# switching back to the normal supply.
#
# ARM:   sudo systemctl enable poe-diag.service   (or run once: sudo systemctl start poe-diag)
# DISARM: sudo systemctl disable poe-diag.service
#
# It is read-only apart from writing its own report file.

set -u
OUT="/data/poe-diag-$(date +%Y%m%d-%H%M%S).txt"

{
  echo "=== poe-diag $(date -Is) ==="
  echo "--- uptime / model ---"
  uptime
  cat /proc/device-tree/model 2>/dev/null; echo
  echo "--- power state (the decisive lines) ---"
  vcgencmd get_throttled 2>/dev/null
  vcgencmd measure_volts core 2>/dev/null
  echo "--- voltage events since boot ---"
  dmesg -T 2>/dev/null | grep -iE "undervoltage|voltage normalised" | head -40
  echo "   (total undervoltage events: $(dmesg -T 2>/dev/null | grep -ci 'undervoltage detected'))"
  echo "--- WiFi driver / firmware bring-up ---"
  dmesg -T 2>/dev/null | grep -iE "brcmfmac|brcm|cfg80211|firmware" | head -40
  echo "--- interfaces ---"
  ip -br addr 2>/dev/null
  echo "--- routes ---"
  ip route 2>/dev/null
  echo "--- rfkill ---"
  rfkill list 2>/dev/null
  echo "--- wireless modules / services ---"
  lsmod 2>/dev/null | grep -E "brcmfmac|cfg80211|brcmutil"
  systemctl is-active wpa_supplicant 2>/dev/null | sed 's/^/wpa_supplicant: /'
  systemctl is-active dhcpcd 2>/dev/null | sed 's/^/dhcpcd: /'
  echo "--- eth0 link ---"
  echo "operstate: $(cat /sys/class/net/eth0/operstate 2>/dev/null)"
  ethtool eth0 2>/dev/null | grep -iE "speed|link detected"
  echo "--- tail of dmesg ---"
  dmesg -T 2>/dev/null | tail -30
} > "$OUT" 2>&1

chmod 0644 "$OUT"
echo "poe-diag written: $OUT"
