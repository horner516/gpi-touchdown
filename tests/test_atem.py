import copy
import socket
import struct
import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from atem_client import AtemManager
from config import DEFAULT, validate


def command(name, data):
    return struct.pack('!HH4s', 8 + len(data), 0, name.encode()) + data


def input_properties(source, name, available=True):
    data = bytearray(36)
    struct.pack_into('!H', data, 0, source)
    data[2:22] = name.encode()[:20].ljust(20, b'\0')
    data[22:26] = name.encode()[:4].ljust(4, b'\0')
    data[34] = int(available)
    return command('InPr', data)


class FakeAtem:
    """Real UDP handshake/state simulator; only CAuS control is accepted."""
    def __init__(self, confirm=True):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(('127.0.0.1', 0))
        self.sock.settimeout(0.02)
        self.port = self.sock.getsockname()[1]
        self.confirm = confirm
        self.commands, self.errors = [], []
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run)
        self.thread.start()

    def packet(self, flags, packet_id, body=b''):
        return struct.pack('!6H', (flags << 11) | (12 + len(body)), 0x1234,
                           0, 0, 0, packet_id) + body

    def run(self):
        address, initial, packet_id, last_ping = None, False, 1, 0
        try:
            while not self.stop.is_set():
                try:
                    data, peer = self.sock.recvfrom(2048)
                except socket.timeout:
                    data = b''
                if data:
                    flags = data[0] >> 3
                    if flags & 2:
                        address, initial, packet_id = peer, False, 1
                        self.sock.sendto(self.packet(2, 0, bytes([2, 0, 0, 1, 0, 0, 0, 0])), peer)
                    elif flags & 16 and not initial:
                        topology = bytes([1, 4, 2, 2, 1, 0, 0, 0, 0, 0])
                        body = (command('_ver', struct.pack('!HH', 2, 30)) +
                                command('_pin', b'Test ATEM'.ljust(44, b'\0')) +
                                command('_top', topology) +
                                input_properties(0, 'Black') + input_properties(1, 'Camera 1') +
                                input_properties(41, 'Camera 41') +
                                input_properties(2, 'Unavailable', False) +
                                input_properties(10010, 'Program') +
                                command('AuxS', struct.pack('!BBH', 0, 0, 1)) +
                                command('AuxS', struct.pack('!BBH', 1, 0, 1)) +
                                command('InCm', b'\0\0\0\0'))
                        self.sock.sendto(self.packet(1, packet_id, body), peer)
                        initial = True
                        last_ping = time.monotonic()
                    if flags & 1 and len(data) > 12:
                        name, payload = data[16:20].decode(), data[20:24]
                        self.commands.append((name, payload))
                        if name == 'CAuS' and self.confirm:
                            packet_id += 1
                            self.sock.sendto(self.packet(1, packet_id,
                                command('AuxS', bytes([payload[1], 0]) + payload[2:4])), peer)
                if address and initial and time.monotonic() - last_ping > 0.1:
                    packet_id += 1
                    self.sock.sendto(self.packet(1, packet_id), address)
                    last_ping = time.monotonic()
        except Exception as error:
            self.errors.append(error)

    def close(self):
        self.stop.set()
        self.thread.join(2)
        self.sock.close()


class AtemTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeAtem()
        self.manager = AtemManager(None)
        self.router = {'protocol': 'atem', 'host': '127.0.0.1', 'port': self.fake.port}
        self.manager.configure(self.router)
        deadline = time.monotonic() + 3
        while not self.manager.snapshot()['connected'] and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(self.manager.snapshot()['connected'], self.manager.snapshot())

    def tearDown(self):
        self.manager.configure({'protocol': 'swp08', 'host': '', 'port': 8910})
        self.fake.close()
        self.assertFalse(self.fake.errors)

    def test_handshake_and_model_source_discovery(self):
        state = self.manager.snapshot()
        self.assertEqual(state['model'], 'Test ATEM')
        self.assertEqual(state['aux_count'], 2)
        self.assertEqual([s['id'] for s in state['sources']], [0, 1, 41, 10010])
        self.assertEqual(self.fake.commands, [])

    def test_program_source_uses_raw_id_and_aux_is_zero_based_on_wire(self):
        message = self.manager.select('127.0.0.1', self.fake.port, 10010, 2)
        self.assertIn('readback confirms', message)
        self.assertEqual(self.fake.commands, [('CAuS', bytes([1, 1, 0x27, 0x1a]))])
        self.assertEqual(self.manager.snapshot()['aux_sources']['2'], 10010)

    def test_black_source_zero_and_larger_switcher_input(self):
        self.manager.select('127.0.0.1', self.fake.port, 0, 1)
        self.manager.select('127.0.0.1', self.fake.port, 41, 1)
        self.assertEqual([p[1][2:] for p in self.fake.commands], [b'\0\0', b'\0)'])

    def test_already_selected_is_reported_without_extra_command(self):
        self.assertIn('already reports', self.manager.select('127.0.0.1', self.fake.port, 1, 1))
        self.assertEqual(self.fake.commands, [])

    def test_rejects_unavailable_sources_auxes_and_wrong_target(self):
        for source, aux in [(2, 1), (10010, 3), (65535, 1)]:
            with self.assertRaises(ValueError):
                self.manager.select('127.0.0.1', self.fake.port, source, aux)
        with self.assertRaises(ConnectionError):
            self.manager.select('127.0.0.2', self.fake.port, 10010, 1)
        self.assertEqual(self.fake.commands, [])

    def test_no_readback_is_failure_and_not_replayed_on_reconnect(self):
        self.fake.confirm = False
        with self.assertRaises(TimeoutError):
            self.manager.select('127.0.0.1', self.fake.port, 10010, 1, timeout=0.12)
        self.assertFalse(self.manager.snapshot()['connected'])
        self.assertEqual(len(self.fake.commands), 1)
        self.manager.configure(self.router)
        time.sleep(0.15)
        self.assertEqual(len(self.fake.commands), 1)

    def test_switching_protocol_closes_socket_and_discards_connection(self):
        client = self.manager.client
        self.manager.configure({'protocol': 'videohub', 'host': '127.0.0.1', 'port': 9990})
        self.assertIsNone(self.manager.client)
        self.assertEqual(client._udp._socket.fileno(), -1)
        with self.assertRaises(ConnectionError):
            self.manager.select('127.0.0.1', self.fake.port, 10010, 1)

    def test_configuration_accepts_atem_ids_without_relaxing_router_limits(self):
        config = copy.deepcopy(DEFAULT)
        config['router'].update(protocol='atem', port=9910, output=2)
        config['contacts'][0]['source'] = 0
        config['contacts'][1]['source'] = 10010
        self.assertEqual(validate(config), config)
        config['router']['protocol'] = 'videohub'
        with self.assertRaises(ValueError):
            validate(config)


if __name__ == '__main__':
    unittest.main()
