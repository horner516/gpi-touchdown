#!/bin/bash
# Apply a Touchdown application update without reinstalling the OS or settings.
set -euo pipefail
ARCHIVE=$(realpath "${1:?Supply the Touchdown update tar.gz}")
[[ $EUID == 0 && -f /opt/gpi-touchdown/server.py && -f /var/lib/gpi-touchdown/credentials.json ]]
WORK=$(mktemp -d /opt/touchdown-update.XXXXXX)
trap 'rm -rf -- "$WORK"' EXIT
mkdir "$WORK/incoming"
tar -xzf "$ARCHIVE" -C "$WORK/incoming"
cp -a /opt/gpi-touchdown "$WORK/app"
for file in config.py controller.py protocol.py server.py atem_client.py index.html THIRD_PARTY.txt; do
    install -m 644 "$WORK/incoming/app/$file" "$WORK/app/$file"
done
cp -a "$WORK/incoming/app/PyATEMMax" "$WORK/app/"
cp -a "$WORK/incoming/tests" "$WORK/tests"
python3 -m unittest discover -s "$WORK/tests" -v
PYTHONPATH="$WORK/app" python3 - <<'PY'
from config import Store
Store('/var/lib/gpi-touchdown/config.json')
print('Existing configuration validated without rewriting it')
PY
python3 -m compileall -q "$WORK/app"
BACKUP="/opt/gpi-touchdown.before-blackmagic-$(date +%Y%m%d-%H%M%S)"
[[ ! -e "$BACKUP" ]]
systemctl stop gpi-touchdown
mv /opt/gpi-touchdown "$BACKUP"
if mv "$WORK/app" /opt/gpi-touchdown && systemctl start gpi-touchdown; then
    sleep 3
    if systemctl is-active --quiet gpi-touchdown; then
        echo "Touchdown updated. Previous application retained at $BACKUP"
        exit 0
    fi
fi
systemctl stop gpi-touchdown || true
mv /opt/gpi-touchdown "$WORK/failed-app"
mv "$BACKUP" /opt/gpi-touchdown
systemctl start gpi-touchdown
echo 'Update failed; previous application restored.' >&2
exit 1
