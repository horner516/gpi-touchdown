import base64
import copy
import http.client
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from config import DEFAULT, Store
from controller import Controller
from network import Network
from server import make_server


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.store = Store(Path(cls.temp.name) / 'config.json')
        cls.control = Controller(cls.store, True)
        cls.server = make_server(cls.store, cls.control, Network('ap-secret'),
                                 {'web_password': 'test-secret'}, '127.0.0.1', 0)
        cls.thread = threading.Thread(target=cls.server.serve_forever)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.temp.cleanup()

    def request(self, method, path, value=None, auth=True, csrf=True):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=2)
        headers = {}
        if auth:
            headers['Authorization'] = 'Basic ' + base64.b64encode(b'admin:test-secret').decode()
        if csrf:
            headers['X-Touchdown'] = '1'
        body = json.dumps(value) if value is not None else None
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        status, data = response.status, response.read()
        conn.close()
        return status, data

    def test_auth_required(self):
        for path in ['/', '/api/state']:
            self.assertEqual(self.request('GET', path, auth=False)[0], 401)

    def test_atem_configuration_and_discovery_status(self):
        value = copy.deepcopy(DEFAULT)
        value['router'].update(protocol='atem', port=9910, output=2, host='192.168.1.9')
        value['contacts'][0]['source'] = 10010
        value['contacts'][1]['source'] = 0
        self.assertEqual(self.request('POST', '/api/config', value)[0], 200)
        status, body = self.request('GET', '/api/state')
        self.assertEqual(status, 200)
        result = json.loads(body)
        self.assertEqual(result['config'], value)
        self.assertFalse(result['control']['atem']['connected'])
        self.assertEqual(result['control']['atem']['sources'], [])

    def test_page_and_state(self):
        status, body = self.request('GET', '/')
        self.assertEqual(status, 200)
        self.assertIn(b'GPI Touchdown', body)
        status, body = self.request('GET', '/api/state')
        self.assertEqual(status, 200)
        self.assertEqual(len(json.loads(body)['config']['contacts']), 8)
        self.assertNotIn(b'ap-secret', body)
        self.assertNotIn(b'test-secret', body)

    def test_post_requires_csrf_header(self):
        self.assertEqual(self.request('POST', '/api/config', DEFAULT, csrf=False)[0], 403)

    def test_invalid_setting_does_not_replace_configuration(self):
        old = self.store.get()
        value = copy.deepcopy(DEFAULT)
        value['router']['output'] = -1
        self.assertEqual(self.request('POST', '/api/config', value)[0], 400)
        self.assertEqual(self.store.get(), old)

    def test_save_and_manual_route(self):
        value = copy.deepcopy(DEFAULT)
        value['router']['host'] = '192.168.1.1'
        value['contacts'][3]['source'] = 19
        value['armed'] = True
        self.assertEqual(self.request('POST', '/api/config', value)[0], 200)
        self.assertIsNone(self.control.pending)
        self.assertEqual(self.request('POST', '/api/test', {'contact': 4})[0], 202)
        self.assertEqual(self.control.pending[2], 3)
        self.assertEqual(self.control.pending[3]['contacts'][3]['source'], 19)

    def test_videohub_configuration_round_trip(self):
        value = copy.deepcopy(DEFAULT)
        value['router'].update(protocol='videohub', port=9990, host='192.168.1.9')
        self.assertEqual(self.request('POST', '/api/config', value)[0], 200)
        status, body = self.request('GET', '/api/state')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['config'], value)


if __name__ == '__main__':
    unittest.main()
