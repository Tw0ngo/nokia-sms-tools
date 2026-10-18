#!/bin/bash
# Send a Nokia ringtone SMS through a phone on USB.
# Usage: sudo ./send-ringtone.sh <phone number> <ringtone.txt (RTTTL)>
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
[ $# -eq 2 ] || { echo "usage: $0 <number> <ringtone.txt>"; exit 1; }
NUMBER=$1 TONE=$(readlink -f "$2")
# The phone on USB that sends the SMS. Found by itself; to pick one: PHONE=/dev/serial/by-id/... ./send-...
if [ -z "${PHONE:-}" ]; then
  PHONE=$(ls /dev/serial/by-id/* 2>/dev/null | grep -i nokia | head -1 || true)
  PHONE=${PHONE:-$(ls /dev/serial/by-id/* /dev/ttyACM* 2>/dev/null | head -1 || true)}
fi
[ -n "$PHONE" ] && [ -e "$PHONE" ] || { echo "No phone found. Is it plugged in? See: ls /dev/serial/by-id/"; exit 1; }

command -v gammu >/dev/null || apt-get install -y gammu

CFG=$(mktemp)
printf '[gammu]\ndevice = %s\nconnection = at\n' "$PHONE" > "$CFG"
gammu -c "$CFG" identify
gammu -c "$CFG" sendsms RINGTONE "$NUMBER" "$TONE"
rm -f "$CFG"
