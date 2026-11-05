import copy
import json
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from config import DEFAULT, Store, validate
from controller import Controller, Debouncer
from network import Network, fields
from protocol import ACK, DLE, NAK, Decoder, crosspoint, frame, route


class ProtocolTests(unittest.TestCase):
    def test_known_standard_crosspoint(self):
        self.assertEqual(crosspoint(1, 1).hex(), '1002020000000005f91003')
        events = Decoder().feed(crosspoint(129, 258, 2, 3))
        self.assertEqual(events, [('data', bytes([2, 0x12, 0x21, 1, 0]))])

    def test_dle_and_fragmentation(self):
        packet = frame([16] * 16)  # Both data and byte count need escaping.
        decoder, events = Decoder(), []
        for byte in packet + bytes([DLE, ACK]):
            events.extend(decoder.feed(bytes([byte])))
        self.assertEqual(events, [('data', bytes([16] * 16)), ('ack', None)])
        self.assertIn(b'\x10\x10', frame([239]))  # Escaped checksum (0x10).

    def test_bad_checksum_rejected(self):
        packet = bytearray(frame([2, 0, 0, 0, 0]))
        packet[2] ^= 1
        self.assertEqual(Decoder().feed(packet)[0][0], 'invalid')

    def test_limits(self):
        for args in [(0, 1), (1025, 1), (1, 0), (1, 1, 17, 1)]:
            with self.assertRaises(ValueError):
                crosspoint(*args)

    def fake_router(self, response, timeout=0.2):
        listener = socket.socket()
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        received, errors = [], []

        def worker():
            try:
                with listener:
                    conn, _ = listener.accept()
                    with conn:
                        received.append(conn.recv(1024))
                        if response:
                            conn.sendall(response[:1])
                            time.sleep(0.01)
                            conn.sendall(response[1:])
                        else:
                            time.sleep(timeout + 0.1)
            except Exception as error:
                errors.append(error)
        thread = threading.Thread(target=worker)
        thread.start()
        return port, received, errors, thread

    def test_real_socket_fragmented_ack(self):
        port, received, errors, thread = self.fake_router(bytes([DLE, ACK]))
        route('127.0.0.1', port, 17, 42, timeout=0.2)
        thread.join()
        self.assertFalse(errors)
        self.assertEqual(received, [crosspoint(17, 42)])

    def test_nak_and_timeout_not_reported_as_success(self):
        for response in [bytes([DLE, NAK]), b'']:
            port, _, errors, thread = self.fake_router(response)
            with self.assertRaises((ConnectionError, TimeoutError, socket.timeout)):
                route('127.0.0.1', port, 1, 1, timeout=0.2)
            thread.join()
            self.assertFalse(errors)


class ControlTests(unittest.TestCase):
    def test_debounce_and_latched_release(self):
        debounce = Debouncer(False, 0)
        self.assertIsNone(debounce.sample(True, 1))
        self.assertIsNone(debounce.sample(False, 1.01))
        self.assertIsNone(debounce.sample(True, 1.02))
        self.assertIsNone(debounce.sample(True, 1.04))
        self.assertIs(debounce.sample(True, 1.07), True)
        self.assertIsNone(debounce.sample(True, 1.2))
        self.assertIsNone(debounce.sample(False, 1.3))
        self.assertIs(debounce.sample(False, 1.35), False)

    def test_held_contact_has_no_startup_edge(self):
        debounce = Debouncer(True, 0)
        self.assertIsNone(debounce.sample(True, 10))

    def test_config_and_latest_contact_wins(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / 'settings.json')
            control = Controller(store, True)
            self.assertFalse(control.select(0))
            value = copy.deepcopy(DEFAULT)
            value['router']['host'] = '192.168.1.1'
            value['armed'] = True
            control.save(value)
            self.assertTrue(control.select(0))
            self.assertTrue(control.select(7))
            self.assertEqual(control.pending[2], 7)
            control.save(value)
            self.assertIsNone(control.pending)
            self.assertEqual(Store(store.path).get(), value)
            self.assertEqual(store.path.stat().st_mode & 0o777, 0o600)

    def test_config_rejects_invalid_values(self):
        for router, key, value in [(True, 'host', 'shell;command'), (True, 'port', True),
                                   (True, 'output', 1025), (False, 'armed', 'yes')]:
            config = copy.deepcopy(DEFAULT)
            (config['router'] if router else config)[key] = value
            with self.assertRaises(ValueError):
                validate(config)
        config = copy.deepcopy(DEFAULT)
        config['armed'] = True
        with self.assertRaises(ValueError):
            validate(config)


class NetworkTests(unittest.TestCase):
    def test_nmcli_escaped_ssid(self):
        self.assertEqual(fields(r'News\:Truck\\A:92:WPA2'), ['News:Truck\\A', '92', 'WPA2'])

    def test_join_failure_restores_hotspot_without_erasing_known_profiles(self):
        network = Network('sample-password')
        network.ap_uuid = 'ap-uuid'
        network.lock.acquire()
        calls = []

        def fake_nm(*args, **kwargs):
            calls.append(args)
            if args[:2] == ('connection', 'up') and args[2] != 'ap-uuid':
                raise RuntimeError('bad password')
            return ''

        with patch.object(network, 'active', return_value='ap-uuid'), patch('network.nm', fake_nm):
            network._join('My Wi-Fi', 'wrong-password')
        self.assertEqual(network.snapshot()['mode'], 'hotspot')
        self.assertFalse(network.snapshot()['busy'])
        self.assertFalse(network.lock.locked())
        self.assertIn(('connection', 'up', 'ap-uuid', 'ifname', 'wlan0'), calls)
        deletions = [args for args in calls if args[:2] == ('connection', 'delete')]
        self.assertEqual(len(deletions), 1)
        self.assertNotEqual(deletions[0][2], 'ap-uuid')

    def test_scan_keeps_colons_and_strongest_signal(self):
        network = Network('sample-password')
        with patch('network.nm', return_value='Truck\\:A:25:WPA2\nTruck\\:A:88:WPA2\nOpen:10:'):
            result = network.scan()
        self.assertEqual(result[0], {'ssid': 'Truck:A', 'signal': 88, 'security': 'WPA2'})

    def test_input_validation_and_busy(self):
        network = Network('sample-password')
        for ssid, password in [('', ''), ('A' * 33, ''), ('A', 'short')]:
            with self.assertRaises(ValueError):
                network.join(ssid, password)
        network.lock.acquire()
        with self.assertRaises(ValueError):
            network.join('A', 'long-password')
        network.lock.release()


if __name__ == '__main__':
    unittest.main()
