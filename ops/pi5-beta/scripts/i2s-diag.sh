#!/bin/bash
# i2s-diag.sh — read-only evidence capture for the I2S DAC control-plane investigation.
#
# SAFETY: this script performs NO writes outside its own output file. It changes no
# configuration, loads/unloads no modules and touches no device. It is safe to run at
# any time, including while audio is playing.
#
# Purpose: produce an objective before/after snapshot for each remediation experiment,
# so "did the I2C control plane come alive" is answered by the same measurement every
# time rather than by recollection.
#
# Usage:   i2s-diag.sh [output-file]
#          default output: /data/i2s-diag-<timestamp>.txt
#
# NOTE: the captured output contains the device hostname and local paths. Sanitize it
# before attaching to anything public.

set -u

OUT="${1:-/data/i2s-diag-$(date +%Y%m%d-%H%M%S).txt}"
CODEC_I2C_ADDR="1-0048"       # the i-sabre codec client: bus-<addr>
CARD_NAME="DAC"               # ALSA card name used by the Audiophonics profile
ALT_CARD_NAME="sndrpihifiberry"

exec > >(tee "$OUT") 2>&1

hr() { printf '\n=== %s ===\n' "$1"; }

echo "# i2s-diag — I2S DAC control-plane snapshot"
echo "# generated: $(date '+%Y-%m-%d %H:%M:%S %z')"
echo "# host: $(hostname)"
echo "# output file: $OUT"

hr "uptime / kernel"
uptime
uname -a

hr "boot config — I2S-relevant lines"
if [ -r /boot/config.txt ]; then
  grep -nE 'dtoverlay|dtparam|param=' /boot/config.txt || echo "(no dtoverlay/dtparam/param lines)"
  echo "-- sha256: $(sha256sum /boot/config.txt | cut -d' ' -f1)"
else
  echo "(/boot/config.txt not readable)"
fi
if [ -r /boot/userconfig.txt ]; then
  echo
  echo "-- userconfig.txt --"
  grep -nE 'dtoverlay|dtparam|param=' /boot/userconfig.txt || echo "(no dtoverlay/dtparam/param lines)"
  echo "-- sha256: $(sha256sum /boot/userconfig.txt | cut -d' ' -f1)"
else
  echo "(/boot/userconfig.txt not readable)"
fi

hr "ALSA cards"
cat /proc/asound/cards 2>/dev/null || echo "(no /proc/asound/cards)"
echo
aplay -l 2>/dev/null | grep -E '^card' || echo "(aplay -l produced no card lines)"

hr "sound card present?"
if [ -d "/sys/class/sound/card1" ]; then
  echo "card1 present; /proc/asound/card1:"
  ls /proc/asound/card1/ 2>/dev/null
else
  echo "card1 NOT present"
fi
echo "-- expected primary card name: $CARD_NAME (sabre profile) or $ALT_CARD_NAME (hifiberry-dac)"

hr "I2C bus 1 — instantiated clients"
for d in /sys/bus/i2c/devices/*; do
  n=$(basename "$d")
  [ -e "$d/name" ] && echo "  $n = $(cat "$d/name" 2>/dev/null)"
done
echo "-- codec driver dir:"
ls -d /sys/bus/i2c/drivers/*sabre* 2>/dev/null || echo "  (no sabre i2c driver bound/present)"

hr "runtime overlays (boot-firmware overlays do NOT appear here)"
if command -v dtoverlay >/dev/null 2>&1; then
  sudo -n /usr/bin/dtoverlay -l 2>/dev/null || echo "(dtoverlay -l needs privilege / not permitted)"
else
  echo "(dtoverlay not installed)"
fi

hr "CODEC I2C HEALTH — the decisive measurement"
if command -v dmesg >/dev/null 2>&1; then
  total=$(dmesg | grep -c "i-sabre-codec-i2c")
  echo "  total i-sabre I2C messages in dmesg : $total"
  echo "  (every one observed so far has been an error; a healthy boot has 0)"
  echo
  echo "  failures by register:"
  for r in 00000001 00000002 00000010 00000020 00000021 00000022 00000024; do
    c=$(dmesg | grep -c "register: \[0x$r\]")
    [ "$c" -gt 0 ] && printf "    reg 0x%s : %s\n" "${r:6:2}" "$c"
  done
  echo
  echo "  chip identification lines (FFFFFF87 == -121, i.e. a FAILED read):"
  dmesg | grep -E "Audiophonics (Device ID|API revision)" || echo "    (none — good sign)"
else
  echo "(dmesg unavailable)"
fi

hr "Volumio audio configuration"
CFG=/data/configuration/audio_interface/alsa_controller/config.json
if [ -r "$CFG" ]; then
  python3 - "$CFG" <<'PY' 2>/dev/null || cat "$CFG"
import json,sys
d=json.load(open(sys.argv[1]))
for k in ("outputdevicename","outputdevicecardname","outputdevice","mixer_type","mixer",
          "resampling","resampling_target_samplerate","resampling_quality"):
    if k in d: print(f"  {k} = {d[k].get('value')!r}")
PY
else
  echo "  ($CFG not readable)"
fi

hr "FusionDSP / CamillaDSP state"
if pgrep -f camilladsp >/dev/null 2>&1; then
  echo "  CamillaDSP: RUNNING (pid $(pgrep -f camilladsp | tr '\n' ' '))"
  YML=/data/configuration/audio_interface/fusiondsp/camilladsp.yml
  [ -r "$YML" ] && grep -m1 -E '^\s*samplerate:' "$YML" | sed 's/^/  active /'
  [ -r "$YML" ] && echo "  config sha256: $(sha256sum "$YML" | cut -d' ' -f1)"
else
  echo "  CamillaDSP: not running (FusionDSP inactive)"
fi
F=/tmp/fusiondsp_stream_params.log
[ -r "$F" ] && echo "  last FIFO stream params (rate,format,channels,width): $(cat "$F")"

hr "current hardware parameters"
for p in /proc/asound/card1/pcm0p/sub0/hw_params /proc/asound/card1/pcm0p/sub0/status; do
  echo "-- $p"
  cat "$p" 2>/dev/null | sed 's/^/   /' || echo "   (not present)"
done

hr "END"
echo "Snapshot written to: $OUT"
echo "Sanitize before publishing: it contains the hostname and local paths."
