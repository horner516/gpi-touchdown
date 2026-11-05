#!/usr/bin/env python3
"""Change an installed Touchdown portal password without reflashing the card."""
import getpass
import json
import os
import subprocess
import tempfile
from pathlib import Path


def main():
    if os.geteuid() != 0:
        raise SystemExit('Run with sudo on the GPI Touchdown Pi.')
    path = Path('/var/lib/gpi-touchdown/credentials.json')
    if not path.is_file():
        raise SystemExit('GPI Touchdown credentials were not found. No changes made.')
    password = getpass.getpass('New web password: ')
    if not 3 <= len(password) <= 64 or any(c in password for c in '\r\n\x00'):
        raise SystemExit('Use 3–64 characters without line breaks.')
    value = json.loads(path.read_text())
    value['web_password'] = password
    fd, name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)
    for boot in (Path('/boot/firmware'), Path('/boot')):
        note = boot / 'touchdown-credentials.txt'
        if note.is_file():
            lines = note.read_text().splitlines()
            note.write_text('\n'.join('Web password: ' + password if line.startswith('Web password:')
                                      else line for line in lines) + '\n')
            break
    subprocess.run(['systemctl', 'restart', 'gpi-touchdown'], check=True)
    print('Web password updated. Web username remains admin. Wi-Fi and SSH are unchanged.')


if __name__ == '__main__':
    main()
