"""NetworkManager owns wlan0. AP and station are deliberately exclusive."""
import os
import subprocess
import threading
import time
import uuid

AP = 'touchdown-setup'
IFACE = 'wlan0'


def fields(line):
    result, current, escape = [], '', False
    for char in line:
        if escape:
            current += char
            escape = False
        elif char == '\\':
            escape = True
        elif char == ':':
            result.append(current)
            current = ''
        else:
            current += char
    result.append(current)
    return result


def nm(*args, timeout=45):
    proc = subprocess.run(['nmcli', '--wait', '30', *args], capture_output=True,
                          text=True, timeout=timeout, env={**os.environ, 'LC_ALL': 'C'})
    if proc.returncode:
        # Do not expose arguments or raw stderr (which can include a Wi-Fi key).
        raise RuntimeError('NetworkManager could not complete the operation')
    return proc.stdout.strip()


class Network:
    def __init__(self, password):
        self.password = password
        self.lock = threading.Lock()
        self.status_lock = threading.Lock()
        self.status = {'mode': 'starting', 'message': 'Checking saved networks', 'busy': False}

    def update(self, **value):
        with self.status_lock:
            self.status.update(value)

    def snapshot(self):
        with self.status_lock:
            return dict(self.status)

    def active(self):
        rows = nm('-t', '-f', 'UUID,TYPE,DEVICE', 'connection', 'show', '--active')
        return next((fields(row)[0] for row in rows.splitlines()
                     if fields(row)[-1] == IFACE), '')

    def profiles(self):
        rows = nm('-t', '-f', 'UUID,TYPE', 'connection', 'show')
        return [fields(row)[0] for row in rows.splitlines()
                if fields(row)[-1] == '802-11-wireless']

    def setup(self):
        nm('radio', 'wifi', 'on')
        nm('device', 'set', IFACE, 'managed', 'yes')
        rows = nm('-t', '-f', 'NAME,UUID', 'connection', 'show')
        self.ap_uuid = next((fields(row)[1] for row in rows.splitlines()
                             if fields(row)[0] == AP), '')
        if not self.ap_uuid:
            self.ap_uuid = str(uuid.uuid4())
            nm('connection', 'add', 'type', 'wifi', 'ifname', IFACE, 'con-name', AP,
               'connection.uuid', self.ap_uuid, 'ssid', 'GPI-Touchdown')
        nm('connection', 'modify', self.ap_uuid,
           '802-11-wireless.mode', 'ap', '802-11-wireless.band', 'bg',
           '802-11-wireless.channel', '6', 'connection.autoconnect', 'no',
           '802-11-wireless-security.key-mgmt', 'wpa-psk',
           '802-11-wireless-security.psk', self.password,
           'ipv4.method', 'shared', 'ipv4.addresses', '192.168.50.1/24',
           'ipv4.never-default', 'yes', 'ipv6.method', 'disabled')

    def hotspot(self, message='Join GPI-Touchdown and open http://192.168.50.1'):
        nm('connection', 'up', self.ap_uuid, 'ifname', IFACE)
        self.update(mode='hotspot', message=message)

    def scan(self):
        if not self.lock.acquire(blocking=False):
            raise ValueError('A Wi-Fi operation is already running')
        try:
            rows = nm('-t', '-f', 'SSID,SIGNAL,SECURITY', 'device', 'wifi', 'list',
                      'ifname', IFACE, '--rescan', 'yes')
            found = {}
            for row in rows.splitlines():
                ssid, signal, security = fields(row)
                if ssid and ssid != 'GPI-Touchdown':
                    item = {'ssid': ssid, 'signal': int(signal), 'security': security}
                    if ssid not in found or int(signal) > found[ssid]['signal']:
                        found[ssid] = item
            return sorted(found.values(), key=lambda item: -item['signal'])
        finally:
            self.lock.release()

    def join(self, ssid, password):
        if not isinstance(ssid, str) or not 1 <= len(ssid.encode()) <= 32 or '\x00' in ssid:
            raise ValueError('SSID must contain 1–32 bytes')
        if not isinstance(password, str) or (password and not 8 <= len(password) <= 63):
            raise ValueError('Wi-Fi password must contain 8–63 characters, or be empty for open Wi-Fi')
        if not self.lock.acquire(blocking=False):
            raise ValueError('A Wi-Fi operation is already running')
        self.update(busy=True, message='Connecting; setup Wi-Fi will disconnect briefly')
        threading.Thread(target=self._join, args=(ssid, password), daemon=True).start()

    def _join(self, ssid, password):
        new_uuid = str(uuid.uuid4())
        old = ''
        try:
            old = self.active()
            nm('connection', 'add', 'type', 'wifi', 'ifname', IFACE,
               'con-name', 'touchdown-' + new_uuid[:8], 'connection.uuid', new_uuid,
               'ssid', ssid, 'connection.autoconnect', 'no', 'ipv4.method', 'auto')
            if password:
                nm('connection', 'modify', new_uuid, 'wifi-sec.key-mgmt', 'wpa-psk',
                   'wifi-sec.psk', password)
            # A separate profile preserves a previously working password until success.
            nm('connection', 'up', new_uuid, 'ifname', IFACE)
            nm('connection', 'modify', new_uuid, 'connection.autoconnect', 'yes',
               'connection.autoconnect-priority', '100')
            self.update(mode='station', message='Connected to ' + ssid)
        except Exception:
            try:
                nm('connection', 'delete', new_uuid)
            except Exception:
                pass
            try:
                if old and old != self.ap_uuid:
                    nm('connection', 'up', old, 'ifname', IFACE)
                    self.update(mode='station', message='Connection failed; previous network restored')
                else:
                    self.hotspot('Connection failed. Check the Wi-Fi name and password, then retry.')
            except Exception:
                try:
                    self.hotspot('Connection failed; setup hotspot restored')
                except Exception:
                    self.update(mode='error', message='Wi-Fi unavailable; check via SSH or reboot')
        finally:
            self.update(busy=False)
            self.lock.release()

    def run(self):
        while True:
            try:
                with self.lock:
                    if not hasattr(self, 'ap_uuid'):
                        self.setup()
                    current = self.active()
                    if current:
                        self.update(mode='hotspot' if current == self.ap_uuid else 'station')
                    else:
                        self.update(mode='searching', message='Trying saved Wi-Fi networks')
                        # NetworkManager autoconnects saved profiles; allow a bounded grace period.
                        for _ in range(30):
                            current = self.active()
                            if current:
                                break
                            time.sleep(1)
                        if not current:
                            self.hotspot()
                        else:
                            self.update(mode='hotspot' if current == self.ap_uuid else 'station',
                                        message='Wi-Fi connected')
            except Exception:
                self.update(mode='error', message='Wi-Fi setup error; retrying automatically')
            time.sleep(5)
