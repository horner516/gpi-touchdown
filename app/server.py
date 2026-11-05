#!/usr/bin/env python3
import argparse
import base64
import hmac
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from config import Store, integer
from controller import Controller
from network import Network


def make_server(store, control, network, credentials, host='0.0.0.0', port=80):
    page = Path(__file__).with_name('index.html').read_bytes()

    class Handler(BaseHTTPRequestHandler):
        server_version = 'GPI-Touchdown'

        def reply(self, code, data, content_type='application/json'):
            if content_type == 'application/json':
                data = json.dumps(data).encode()
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('X-Frame-Options', 'DENY')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(data)

        def auth(self):
            try:
                value = self.headers.get('Authorization', '')
                if not value.startswith('Basic '):
                    raise ValueError()
                decoded = base64.b64decode(value[6:], validate=True)
                expected = ('admin:' + credentials['web_password']).encode()
                if hmac.compare_digest(decoded, expected):
                    return True
            except (ValueError, TypeError):
                pass
            self.send_response(401)
            self.send_header('WWW-Authenticate', 'Basic realm="GPI Touchdown", charset="UTF-8"')
            self.send_header('Content-Length', '0')
            self.end_headers()
            return False

        def do_GET(self):
            if not self.auth():
                return
            try:
                if self.path == '/':
                    self.reply(200, page, 'text/html; charset=utf-8')
                elif self.path == '/api/state':
                    self.reply(200, {'config': store.get(), 'control': control.snapshot(),
                                     'network': network.snapshot()})
                elif self.path == '/api/wifi/scan':
                    self.reply(200, {'networks': network.scan()})
                else:
                    self.reply(404, {'error': 'Not found'})
            except (ValueError, RuntimeError, TimeoutError) as error:
                self.reply(400, {'error': str(error)})
            except Exception:
                logging.exception('GET failed')
                self.reply(500, {'error': 'Operation failed; check service logs'})

        def do_POST(self):
            if not self.auth():
                return
            # Cross-origin forms cannot set this header; no CORS grants are provided.
            if self.headers.get('X-Touchdown') != '1':
                self.reply(403, {'error': 'Missing request header'})
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 16384:
                    raise ValueError('Invalid request size')
                value = json.loads(self.rfile.read(length))
                if not isinstance(value, dict):
                    raise ValueError('Expected an object')
                if self.path == '/api/config':
                    control.save(value)
                    self.reply(200, {'ok': True})
                elif self.path == '/api/test':
                    index = integer(value.get('contact'), 1, 8, 'Contact') - 1
                    if not control.select(index, manual=True):
                        raise ValueError('Enable routing and this contact before testing')
                    self.reply(202, {'ok': True})
                elif self.path == '/api/wifi/join':
                    network.join(value.get('ssid'), value.get('password', ''))
                    self.reply(202, {'ok': True})
                else:
                    self.reply(404, {'error': 'Not found'})
            except (ValueError, TypeError, KeyError) as error:
                self.reply(400, {'error': str(error)})
            except Exception:
                logging.exception('POST failed')
                self.reply(500, {'error': 'Operation failed; check service logs'})

        def setup(self):
            super().setup()
            self.connection.settimeout(15)

        def log_message(self, fmt, *args):
            logging.info('%s %s', self.client_address[0], fmt % args)

    return ThreadingHTTPServer((host, port), Handler)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--state', default='/var/lib/gpi-touchdown')
    parser.add_argument('--simulate', action='store_true')
    parser.add_argument('--port', type=int, default=80)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
    state = Path(args.state)
    credentials = json.loads((state / 'credentials.json').read_text())
    store = Store(state / 'config.json')
    control = Controller(store, args.simulate)
    network = Network(credentials['ap_password'])
    for target in (control.worker, control.poll, control.atem.run):
        threading.Thread(target=target, daemon=True).start()
    if args.simulate:
        network.update(mode='simulation', message='Wi-Fi operations require Raspberry Pi OS')
    else:
        threading.Thread(target=network.run, daemon=True).start()
    make_server(store, control, network, credentials,
                host='127.0.0.1' if args.simulate else '0.0.0.0',
                port=args.port).serve_forever()


if __name__ == '__main__':
    main()
