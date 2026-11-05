import copy
import ipaddress
import json
import os
import tempfile
import threading
from pathlib import Path

PINS = [5, 6, 13, 19, 26, 16, 20, 21]
DEFAULT = {
    'router': {'protocol': 'swp08', 'host': '', 'port': 8910, 'output': 1, 'matrix': 1, 'level': 1},
    'contacts': [{'name': 'GPI %d' % (n + 1), 'source': n + 1, 'enabled': True}
                 for n in range(8)],
    'armed': False,
}


def integer(value, low, high, label):
    if type(value) is not int or not low <= value <= high:
        raise ValueError('%s must be an integer from %d to %d' % (label, low, high))
    return value


def validate(value):
    if not isinstance(value, dict):
        raise ValueError('Configuration must be an object')
    router = value.get('router', {})
    if not isinstance(router, dict):
        raise ValueError('Router configuration must be an object')
    host = router.get('host', '')
    if host:
        try:
            ipaddress.IPv4Address(host)
        except (ValueError, TypeError):
            raise ValueError('Enter the router IPv4 address')
    elif host != '':
        raise ValueError('Invalid router address')
    protocol = router.get('protocol', 'swp08')  # Migrate existing Ultrix settings in memory.
    if protocol not in ('swp08', 'videohub', 'atem'):
        raise ValueError('Choose Ultrix / SW-P-08, Blackmagic Videohub, or ATEM aux')
    result = {'router': {'host': host, 'protocol': protocol}, 'contacts': []}
    for key, low, high in [('port', 1, 65535), ('output', 1, 64 if protocol == 'atem' else 1024),
                           ('matrix', 1, 16), ('level', 1, 16)]:
        result['router'][key] = integer(router.get(key), low, high, key)
    contacts = value.get('contacts')
    if not isinstance(contacts, list) or len(contacts) != 8:
        raise ValueError('Exactly eight contacts are required')
    for item in contacts:
        if not isinstance(item, dict):
            raise ValueError('Invalid contact')
        name = item.get('name')
        if not isinstance(name, str) or not 1 <= len(name) <= 40:
            raise ValueError('Contact names must contain 1–40 characters')
        if type(item.get('enabled')) is not bool:
            raise ValueError('Contact enabled must be true or false')
        result['contacts'].append({'name': name, 'enabled': item['enabled'],
                                  'source': integer(item.get('source'), 0 if protocol == 'atem' else 1,
                                                    65535 if protocol == 'atem' else 1024, 'Input/source')})
    if type(value.get('armed')) is not bool:
        raise ValueError('Armed must be true or false')
    if value['armed'] and not host:
        raise ValueError('Set the router address before enabling routing')
    result['armed'] = value['armed']
    return result


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.value = copy.deepcopy(DEFAULT)
        if self.path.exists():
            self.value = validate(json.loads(self.path.read_text()))

    def get(self):
        with self.lock:
            return copy.deepcopy(self.value)

    def save(self, value):
        value = validate(value)
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, temp = tempfile.mkstemp(dir=self.path.parent)
            try:
                with os.fdopen(fd, 'w') as stream:
                    json.dump(value, stream, indent=2)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temp, self.path)
                directory = os.open(self.path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            finally:
                if os.path.exists(temp):
                    os.unlink(temp)
            self.value = value
