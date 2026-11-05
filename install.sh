#!/bin/bash
# Run on Raspberry Pi OS Bookworm 32-bit; do not run on your Mac.
set -euo pipefail
SOURCE_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
if [[ $(uname -s) != Linux || $EUID != 0 ]]; then
    echo 'Run this installer with sudo on Raspberry Pi OS, not on macOS.' >&2
    exit 1
fi
if [[ $(dpkg --print-architecture) != armhf ]]; then
    echo 'This build targets Raspberry Pi OS 32-bit (armhf) for the original Zero W.' >&2
    exit 1
fi
if [[ ${TOUCHDOWN_SKIP_APT:-0} != 1 ]]; then
    apt-get update
    apt-get install -y --no-install-recommends python3 python3-gpiozero python3-lgpio \
        network-manager dnsmasq-base avahi-daemon openssh-server rfkill iw
fi
install -d -m 755 /opt/gpi-touchdown
install -m 644 "$SOURCE_DIR"/app/*.py "$SOURCE_DIR"/app/index.html /opt/gpi-touchdown/
cp -a "$SOURCE_DIR/app/PyATEMMax" /opt/gpi-touchdown/
install -m 644 "$SOURCE_DIR/app/THIRD_PARTY.txt" /opt/gpi-touchdown/
install -d -m 700 /var/lib/gpi-touchdown
if [[ ! -f /var/lib/gpi-touchdown/credentials.json ]]; then
    if [[ -n ${TOUCHDOWN_CREDENTIALS_FILE:-} ]]; then
        install -m 600 "$TOUCHDOWN_CREDENTIALS_FILE" /var/lib/gpi-touchdown/credentials.json
    else
        python3 - <<'PY'
import json, os, secrets
path = '/var/lib/gpi-touchdown/credentials.json'
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, 'w') as stream:
    json.dump({'ap_password': secrets.token_hex(8), 'web_password': secrets.token_hex(8)}, stream)
PY
    fi
fi
if ! id admin >/dev/null 2>&1; then
    useradd --create-home --shell /bin/bash admin
fi
usermod -aG sudo,gpio admin
if [[ -z ${TOUCHDOWN_SSH_PASSWORD:-} ]]; then
    read -rsp 'Choose an SSH password for admin: ' TOUCHDOWN_SSH_PASSWORD
    printf '\n'
fi
[[ ${#TOUCHDOWN_SSH_PASSWORD} -ge 8 ]] || { echo 'Choose at least eight characters.' >&2; exit 1; }
printf 'admin:%s\n' "$TOUCHDOWN_SSH_PASSWORD" | chpasswd
unset TOUCHDOWN_SSH_PASSWORD
install -d -m 755 /etc/ssh/sshd_config.d
cat > /etc/ssh/sshd_config.d/00-touchdown.conf <<'EOF'
PasswordAuthentication yes
PermitRootLogin no
EOF
chmod 644 /etc/ssh/sshd_config.d/00-touchdown.conf
install -m 644 "$SOURCE_DIR/systemd/gpi-touchdown.service" /etc/systemd/system/
printf 'WIFI_COUNTRY=%s\n' "${TOUCHDOWN_COUNTRY:-US}" > /etc/default/gpi-touchdown
systemctl enable NetworkManager avahi-daemon ssh gpi-touchdown
if [[ ${TOUCHDOWN_IMAGE_BUILD:-0} != 1 ]]; then
    raspi-config nonint do_hostname gpi-touchdown
    raspi-config nonint do_wifi_country "${TOUCHDOWN_COUNTRY:-US}"
fi
python3 - <<'PY'
import json, os
from pathlib import Path
creds = json.loads(Path('/var/lib/gpi-touchdown/credentials.json').read_text())
boot = Path('/boot/firmware')
if not boot.is_dir():
    boot = Path('/boot')
text = ('GPI TOUCHDOWN\n\nSetup Wi-Fi: GPI-Touchdown\nWi-Fi password: {ap_password}\n'
        'Setup web page: http://192.168.50.1\nWeb user: admin\nWeb password: {web_password}\n'
        'On your LAN: http://gpi-touchdown.local\n\nSSH user: admin\nSSH password: chosen during installation\n').format(**creds)
(boot / 'touchdown-credentials.txt').write_text(text)
if os.environ.get('TOUCHDOWN_IMAGE_BUILD') != '1':
    print(text)
PY
echo 'Installed. Reboot to start GPI Touchdown.'
