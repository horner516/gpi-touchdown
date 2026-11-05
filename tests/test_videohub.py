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
from protocol import route, videohub_command


class VideohubTests(unittest.TestCase):
    def exchange(self, responses, outcome=None, pause=False):
        received, errors = [], []
        listener = socket.socket()
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        port = listener.getsockname()[1]

        def server():
            try:
                with listener:
                    conn, _ = listener.accept()
                    with conn:
                        conn.settimeout(2)
                        conn.sendall(b'PROTOCOL PREAMBLE:\nVersion: 2.3\n\n')
                        command = b''
                        while b'\n\n' not in command:
                            command += conn.recv(1024)
                        received.append(command)
                        for part in responses:
                            conn.sendall(part)
                            time.sleep(0.005)
                        if pause:
                            time.sleep(0.2)
            except Exception as error:
                errors.append(error)

        thread = threading.Thread(target=server)
        thread.start()
        try:
            if outcome:
                with self.assertRaises(outcome):
                    route('127.0.0.1', port, 8, 3, protocol='videohub', timeout=0.1)
            else:
                route('127.0.0.1', port, 8, 3, protocol='videohub', timeout=0.5)
        finally:
            thread.join(3)
            self.assertFalse(thread.is_alive())
        self.assertFalse(errors)
        # Exactly one routing command; no forced unlock or delayed retry.
        self.assertEqual(received, [b'VIDEO OUTPUT ROUTING:\n2 7\n\n'])

    def test_wire_numbers_and_blank_line(self):
        self.assertEqual(videohub_command(1, 1), b'VIDEO OUTPUT ROUTING:\n0 0\n\n')
        self.assertEqual(videohub_command(1024, 1024), b'VIDEO OUTPUT ROUTING:\n1023 1023\n\n')
        for source, destination in [(0, 1), (1, 0), (1025, 1), (True, 1), (1.5, 2)]:
            with self.assertRaises(ValueError):
                videohub_command(source, destination)

    def test_fragmented_ack_after_initial_dump_and_unknown_blocks(self):
        self.exchange([b'VIDEO OUTPUT ROUTING:\r\n2 7\r\n\r\n',
                       b'FUTURE STATUS:\nUnknown field\n\n',
                       b'A', b'CK\r', b'\n\r', b'\n'])

    def test_nak_is_failure(self):
        self.exchange([b'NAK\n\n'], ConnectionError)

    def test_closed_connection_is_failure(self):
        self.exchange([], ConnectionError)

    def test_initial_matching_route_does_not_count_as_ack(self):
        self.exchange([b'VIDEO OUTPUT ROUTING:\n2 7\n\n'], TimeoutError, pause=True)

    def test_unknown_protocol_never_opens_socket(self):
        with patch('protocol.socket.create_connection') as connect:
            with self.assertRaises(ValueError):
                route('127.0.0.1', 9990, 1, 1, protocol='unknown')
            connect.assert_not_called()

    def test_legacy_settings_migrate_without_changing_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'config.json'
            old = copy.deepcopy(DEFAULT)
            del old['router']['protocol']
            old['router']['host'] = '192.168.1.10'
            old['contacts'][2]['source'] = 27
            text = json.dumps(old)
            path.write_text(text)
            migrated = Store(path).get()
            self.assertEqual(migrated['router']['protocol'], 'swp08')
            self.assertEqual(migrated['contacts'], old['contacts'])
            self.assertEqual(path.read_text(), text)

    def test_protocol_validation_and_persistence(self):
        config = copy.deepcopy(DEFAULT)
        config['router'].update(protocol='videohub', port=9990)
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / 'config.json')
            store.save(config)
            self.assertEqual(Store(store.path).get(), config)
        config['router']['protocol'] = 'unknown'
        with self.assertRaises(ValueError):
            validate(config)


if __name__ == '__main__':
    unittest.main()
