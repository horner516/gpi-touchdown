import logging
import threading
import time
from collections import deque

from config import PINS
from protocol import route
from atem_client import AtemManager


class Debouncer:
    def __init__(self, initial, now, interval=0.04):
        self.stable = self.candidate = bool(initial)
        self.since = now
        self.interval = interval

    def sample(self, value, now):
        value = bool(value)
        if value != self.candidate:
            self.candidate, self.since = value, now
        if self.candidate != self.stable and now - self.since >= self.interval:
            self.stable = self.candidate
            return self.stable
        return None


class Controller:
    def __init__(self, store, simulate=False):
        self.store, self.simulate = store, simulate
        self.atem = AtemManager(store)
        self.condition = threading.Condition(threading.RLock())
        self.pending = None
        self.generation = 0
        self.events = deque(maxlen=30)
        self.closed = [False] * 8
        self.gpio = 'Simulation; physical contacts unavailable' if simulate else 'Starting'
        self.last = None

    def event(self, message):
        with self.condition:
            self.events.appendleft({'time': time.strftime('%H:%M:%S'), 'message': message})
        logging.info(message)

    def snapshot(self):
        with self.condition:
            value = {'closed': list(self.closed), 'gpio': self.gpio,
                     'last': self.last, 'events': list(self.events), 'pins': PINS}
        value['atem'] = self.atem.snapshot()
        return value

    def save(self, value):
        with self.condition:
            self.store.save(value)
            self.generation += 1
            self.pending = None
        self.event('Settings saved; waiting for a new contact closure')

    def select(self, index, manual=False):
        with self.condition:
            value = self.store.get()
            contact = value['contacts'][index]
            if not value['armed'] or not contact['enabled']:
                return False
            self.pending = (time.monotonic(), self.generation, index, value)
            self.condition.notify()
        self.event('%s %d selected input %d' % ('Test' if manual else 'GPI', index + 1, contact['source']))
        return True

    def worker(self):
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.pending is not None)
                created, generation, index, value = self.pending
                self.pending = None
                if generation != self.generation or time.monotonic() - created > 2:
                    continue
            router, source = value['router'], value['contacts'][index]['source']
            result = {'contact': index + 1, 'source': source, 'output': router['output'],
                      'time': time.strftime('%H:%M:%S'), 'acknowledged': False}
            try:
                if router.get('protocol') == 'atem':
                    result['message'] = self.atem.select(router['host'], router['port'], source, router['output'])
                else:
                    route(router['host'], router['port'], source, router['output'],
                          router['matrix'], router['level'], protocol=router.get('protocol', 'swp08'))
                    result['message'] = 'Router ACK received (route readback not verified)'
                result['acknowledged'] = True
            except Exception as error:
                result['message'] = 'Route failed: ' + str(error)
            with self.condition:
                self.last = result
            self.event(result['message'])

    def poll(self):
        if self.simulate:
            return
        devices = []
        try:
            from gpiozero import DigitalInputDevice
            for pin in PINS:
                devices.append(DigitalInputDevice(pin, pull_up=True))
            # Let pull-ups and external contact wiring settle before establishing baseline.
            time.sleep(0.2)
            debouncers = [Debouncer(device.is_active, time.monotonic()) for device in devices]
            with self.condition:
                self.closed = [item.stable for item in debouncers]
                self.gpio = 'Ready — active-low dry contacts, 40 ms debounce'
            self.event('GPIO ready; contacts held at startup do not trigger a route')
            while True:
                now = time.monotonic()
                # Simultaneous closures are resolved in contact order: highest GPI wins.
                for index, (device, debounce) in enumerate(zip(devices, debouncers)):
                    changed = debounce.sample(device.is_active, now)
                    if changed is not None:
                        with self.condition:
                            self.closed[index] = changed
                        if changed:
                            self.select(index)
                time.sleep(0.01)
        except Exception:
            logging.exception('GPIO unavailable')
            with self.condition:
                self.gpio = 'ERROR — physical contact monitoring unavailable; restart after checking wiring'
            self.event('GPIO initialization or monitoring failed')
        finally:
            for device in devices:
                device.close()
