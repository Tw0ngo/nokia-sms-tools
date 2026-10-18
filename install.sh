#!/bin/bash
# Installs the Nokia SMS server and starts it. Run it again to update.
# Usage: sudo ./install.sh
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "Please run it with sudo: sudo ./install.sh"; exit 1; }
HERE=$(dirname "$(readlink -f "$0")")
command -v python3 >/dev/null || apt-get install -y python3

echo "== copying files to /opt/nokia-sms-tools"
install -d -m 755 /opt/nokia-sms-tools/server /opt/nokia-sms-tools/web
install -m 644 "$HERE/server/nokia_sms_server.py" /opt/nokia-sms-tools/server/
install -m 644 "$HERE/web/index.html" /opt/nokia-sms-tools/web/

if [ ! -e /etc/nokia-sms/config.ini ]; then
  echo "== creating the settings file /etc/nokia-sms/config.ini"
  install -d -m 755 /etc/nokia-sms
  install -m 600 "$HERE/server/config.example.ini" /etc/nokia-sms/config.ini
else
  echo "== keeping your settings in /etc/nokia-sms/config.ini"
fi

echo "== starting the service"
install -m 644 "$HERE/server/nokia-sms.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable nokia-sms.service
systemctl restart nokia-sms.service
sleep 1
PORT=$(sed -n 's/^listen *= *.*:\([0-9]*\).*/\1/p' /etc/nokia-sms/config.ini | head -1)
PORT=${PORT:-8095}
if python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:$PORT/api/status', timeout=5)" 2>/dev/null; then
  echo
  echo "Done! Open http://$(hostname -I 2>/dev/null | awk '{print $1}'):$PORT in a browser."
  echo "Settings: sudo nano /etc/nokia-sms/config.ini, then: sudo systemctl restart nokia-sms"
else
  echo "Something went wrong, here is what the server said:"
  journalctl -u nokia-sms -n 20 --no-pager
fi
