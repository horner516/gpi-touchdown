"""Pro-Bel SW-P-08 and Blackmagic Videohub clients; UI numbering starts at one."""
import socket
import time

DLE, STX, ETX, ACK, NAK = 16, 2, 3, 6, 21


def frame(data):
    body = bytes(data)
    body += bytes((len(body), -(sum(body) + len(body)) & 255))
    return bytes((DLE, STX)) + body.replace(b'\x10', b'\x10\x10') + bytes((DLE, ETX))


def crosspoint(source, destination, matrix=1, level=1):
    if not (1 <= source <= 1024 and 1 <= destination <= 1024
            and 1 <= matrix <= 16 and 1 <= level <= 16):
        raise ValueError('Standard SW-P-08 supports inputs/outputs 1–1024, matrices/levels 1–16')
    source, destination = source - 1, destination - 1
    return frame((2, ((matrix - 1) << 4) | (level - 1),
                  (source >> 7) | ((destination >> 7) << 4),
                  destination & 127, source & 127))


class Decoder:
    """Incremental parser, including fragmented ACKs and escaped frame bytes."""
    def __init__(self):
        self.buffer = bytearray()

    def feed(self, data):
        self.buffer.extend(data)
        events = []
        while len(self.buffer) >= 2:
            if self.buffer[0] != DLE:
                del self.buffer[0]
                continue
            kind = self.buffer[1]
            if kind in (ACK, NAK):
                events.append(('ack' if kind == ACK else 'nak', None))
                del self.buffer[:2]
                continue
            if kind != STX:
                del self.buffer[0]
                continue
            payload, i, complete = bytearray(), 2, False
            while i < len(self.buffer):
                value = self.buffer[i]
                if value == DLE:
                    if i + 1 >= len(self.buffer):
                        break
                    following = self.buffer[i + 1]
                    if following == ETX:
                        valid = (len(payload) >= 2 and payload[-2] == len(payload) - 2
                                 and sum(payload) & 255 == 0)
                        events.append(('data' if valid else 'invalid', bytes(payload[:-2])))
                        del self.buffer[:i + 2]
                        complete = True
                        break
                    if following != DLE:
                        del self.buffer[0]
                        complete = True
                        break
                    i += 1
                payload.append(value)
                i += 1
            if not complete:
                if len(self.buffer) > 1024:
                    self.buffer.clear()
                    events.append(('invalid', b''))
                break
        return events


def videohub_command(source, destination):
    if not (type(source) is int and type(destination) is int
            and 1 <= source <= 1024 and 1 <= destination <= 1024):
        raise ValueError('Inputs and outputs must be integers from 1 to 1024')
    return ('VIDEO OUTPUT ROUTING:\n%d %d\n\n' % (destination - 1, source - 1)).encode('ascii')


def route_videohub(host, port, source, destination, timeout=1.5):
    """Ignore the initial state dump; only the command's ACK counts as acceptance.

    Do not unlock outputs or retry commands. An ACK is not route readback.
    """
    command = videohub_command(source, destination)
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.sendall(command)
        deadline, buffer = time.monotonic() + timeout, b''
        while time.monotonic() < deadline:
            sock.settimeout(max(0.01, deadline - time.monotonic()))
            try:
                data = sock.recv(4096)
            except socket.timeout as error:
                raise TimeoutError('Videohub acknowledgment timed out') from error
            if not data:
                raise ConnectionError('Videohub closed connection before acknowledgment')
            buffer = (buffer + data).replace(b'\r\n', b'\n')
            if len(buffer) > 1024 * 1024:
                raise ConnectionError('Videohub response block is too large')
            while b'\n\n' in buffer:
                block, buffer = buffer.split(b'\n\n', 1)
                block = block.strip()
                if block == b'ACK':
                    return
                if block == b'NAK':
                    raise ConnectionError('Videohub rejected command (NAK); check output locks and input/output numbers')
        raise TimeoutError('Videohub acknowledgment timed out')


def route(host, port, source, destination, matrix=1, level=1, timeout=1.5, protocol='swp08'):
    """ACK means transport accepted; it is not crosspoint readback confirmation.

    No delayed retry: replaying a stale contact later could select the wrong source.
    """
    if protocol == 'videohub':
        return route_videohub(host, port, source, destination, timeout)
    if protocol != 'swp08':
        raise ValueError('Unsupported router protocol')
    packet = crosspoint(source, destination, matrix, level)
    decoder = Decoder()
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.sendall(packet)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            sock.settimeout(max(0.01, deadline - time.monotonic()))
            data = sock.recv(4096)
            if not data:
                raise ConnectionError('Router closed connection before acknowledgment')
            for kind, _ in decoder.feed(data):
                if kind == 'data':
                    sock.sendall(bytes((DLE, ACK)))
                elif kind == 'invalid':
                    sock.sendall(bytes((DLE, NAK)))
                elif kind == 'ack':
                    return
                elif kind == 'nak':
                    raise ConnectionError('Router rejected command (NAK)')
        raise TimeoutError('Router acknowledgment timed out')
