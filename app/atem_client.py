"""Persistent ATEM aux connection; all control calls are limited to CAuS."""
import logging
import threading
import time


def create_client(port):
    # Pinned PyATEMMax 1.0b9 is bundled with its original source and GPL license.
    from PyATEMMax import ATEMMax
    from PyATEMMax.ATEMConstant import ATEMConstant
    from PyATEMMax.ATEMProtocol import ATEMProtocol
    # Extend the library's lookup tables for larger switchers, before allocating state.
    for n in range(1, 129):
        ATEMProtocol.videoSources._values.setdefault('input%d' % n, ATEMConstant('input%d' % n, n))
    for n in range(1, 65):
        ATEMProtocol.auxChannels._values.setdefault('auxChannel%d' % n, ATEMConstant('auxChannel%d' % n, n - 1))
    client = ATEMMax()
    client.setLogLevel(logging.ERROR)
    # Both protocol instances use the library's standard port unless overridden.
    client.atem.UDPPort = port
    client._udp.atem.UDPPort = port
    return client


def close_client(client):
    if client is not None:
        try:
            client.disconnect()
        finally:
            # The pinned library stops its workers but intentionally leaves the UDP fd open.
            udp = getattr(client, '_udp', None)
            sock = getattr(udp, '_socket', None)
            if sock is not None:
                sock.close()


def source_options(client):
    options = []
    for key, item in client.inputProperties._data.items():
        if item.longName and item.availability.auxiliary:
            options.append({'id': key.value, 'name': item.longName, 'short_name': item.shortName})
    return sorted(options, key=lambda item: item['id'])


def ready(client):
    worker = getattr(client, '_commsThread', None)
    return bool(client is not None and client.connected and (worker is None or worker.is_alive()))


class AtemManager:
    def __init__(self, store, factory=create_client):
        self.store, self.factory = store, factory
        self.lock = threading.RLock()
        self.client = None
        self.target = None
        self.message = 'ATEM is not selected'

    def configure(self, router):
        target = ((router['host'], router['port'])
                  if router.get('protocol') == 'atem' and router['host'] else None)
        with self.lock:
            worker = getattr(self.client, '_commsThread', None)
            if target == self.target and (target is None or
                    (self.client is not None and (worker is None or worker.is_alive()))):
                return
            old, self.client, self.target = self.client, None, target
            close_client(old)
            if target is None:
                self.message = 'ATEM is not selected'
                return
            candidate = self.factory(target[1])
            try:
                candidate.connect(target[0], connTimeout=5)
            except Exception:
                close_client(candidate)
                raise
            self.client = candidate
            self.message = 'Connecting to ATEM; waiting for switcher state'

    def run(self):
        while True:
            try:
                self.configure(self.store.get()['router'])
            except Exception:
                logging.exception('ATEM connection setup failed')
                with self.lock:
                    self.message = 'ATEM connection failed; retrying'
            time.sleep(0.5)

    def snapshot(self):
        with self.lock:
            client = self.client
            connected = ready(client)
            value = {'connected': connected, 'message': self.message, 'model': '',
                     'aux_count': 0, 'sources': [], 'aux_sources': {}}
            if connected:
                value.update(message='Connected', model=client.atemModel,
                             aux_count=client.topology.auxBusses, sources=source_options(client))
                for n in range(min(client.topology.auxBusses, 64)):
                    value['aux_sources'][str(n + 1)] = client.auxSource[n].input.value
            elif client is not None:
                value['message'] = 'Connecting or disconnected; no contact commands are queued for reconnect'
            return value

    def select(self, host, port, source, aux, timeout=1.5):
        with self.lock:
            client = self.client
            if self.target != (host, port) or not ready(client):
                raise ConnectionError('ATEM is not connected yet; wait for Connected, then close the contact again')
            if type(aux) is not int or not 1 <= aux <= min(client.topology.auxBusses, 64):
                raise ValueError('That aux is not available on the connected ATEM')
            if source not in {item['id'] for item in source_options(client)}:
                raise ValueError('That source is not reported as available for ATEM aux routing')
            index = aux - 1
            if client.auxSource[index].input.value == source:
                return 'ATEM already reports aux %d on source %d' % (aux, source)
            client.setAuxSourceInput(index, source)
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if not ready(client):
                    raise ConnectionError('ATEM disconnected before aux readback')
                if client.auxSource[index].input.value == source:
                    return 'ATEM readback confirms aux %d on source %d' % (aux, source)
                time.sleep(0.02)
            # Drop the session so failed selections cannot carry into a reconnect.
            self.client = None
            close_client(client)
            raise TimeoutError('ATEM did not confirm the aux selection; close the contact again to retry')
