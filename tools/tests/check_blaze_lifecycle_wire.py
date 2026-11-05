"""Opt-in live TLS/Fire2 lifecycle check against a built native Blaze binary.

python3 tools/tests/check_blaze_lifecycle_wire.py target/debug/blaze
Uses temporary certificates/state and an ephemeral loopback port, never live saves.
"""
import argparse
import json
import os
from pathlib import Path
import socket
import ssl
import struct
import subprocess
import tempfile
import time


def integer(value):
    result = bytearray([value & 0x3f])
    value >>= 6
    if value:
        result[0] |= 0x80
    while value:
        result.append((value & 0x7f) | (0x80 if value >> 7 else 0))
        value >>= 7
    return bytes(result)


def tag(name):
    packed = 0
    for character in name.upper().encode().ljust(4, b'\0'):
        packed = (packed << 6) | ((character & 0x40) >> 1) | (character & 0x1f)
    return packed.to_bytes(3, 'big')


def string(value):
    body = value.encode()
    return integer(len(body) + 1) + body + b'\0'


def field(name, kind, value):
    return tag(name) + bytes([kind]) + value


def launch(quest):
    attributes = {'playMode': '1', 'questId': str(quest), 'desiredDifficulty': '1', 'startPoint': '0'}
    return (field('CMGD', 3, field('GVER', 1, string('17000')) + b'\0') +
            field('SCNA', 5, b'\x01\x0c' + integer(len(attributes)) +
                  b''.join(string(key) + b'\x01\x00\x00\x01' + string(value) + b'\0'
                           for key, value in attributes.items())))


def frame(command, message, payload=b'', component=4, kind=0):
    return struct.pack('>IHHH', len(payload), 0, component, command) + message.to_bytes(3, 'big') + bytes([kind << 5, 0, 0]) + payload


def exact(stream, count):
    data = bytearray()
    while len(data) < count:
        chunk = stream.recv(count - len(data))
        if not chunk:
            raise EOFError('Blaze closed before a complete frame')
        data.extend(chunk)
    return bytes(data)


def exchange(stream, command, message, payload=b'', component=4, kind=0):
    stream.settimeout(3)
    stream.sendall(frame(command, message, payload, component, kind))
    replies = []
    while True:
        try:
            header = exact(stream, 16)
        except socket.timeout:
            break
        length, metadata, comp, cmd = struct.unpack('>IHHH', header[:10])
        assert length < 1 << 20 and metadata < 65536
        body = exact(stream, length + metadata)[metadata:]
        replies.append((comp, cmd, int.from_bytes(header[10:13], 'big'), header[13] >> 5, body))
        stream.settimeout(.08)
    assert any(reply[2] == message and reply[3] in (1, 5) for reply in replies), replies
    return replies


def scenario(replies):
    payload = next(reply[4] for reply in replies if reply[1] == 0x10 and reply[3] == 1)
    assert payload[:4] == field('MSID', 0, b'')
    first = payload[4]
    value, shift, position = first & 0x3f, 6, 5
    while first & 0x80:
        first = payload[position]
        value |= (first & 0x7f) << shift
        shift, position = shift + 7, position + 1
    return value


def check(binary):
    with tempfile.TemporaryDirectory(prefix='anthem-wire-') as temporary:
        root = Path(temporary)
        certificate, key = root / 'test.crt', root / 'test.key'
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                        '-keyout', str(key), '-out', str(certificate),
                        '-subj', '/CN=spring18.gosredirector.ea.com',
                        '-addext', 'subjectAltName=DNS:spring18.gosredirector.ea.com'],
                       check=True, capture_output=True, timeout=15)
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1', 0))
            port = reservation.getsockname()[1]
        env = {**os.environ, 'BLAZE_PORT': str(port), 'LISTEN_HOST': '127.0.0.1',
               'BLAZE_LOCAL_LIFECYCLE': '1', 'BLAZE_MISSION_PAIR': '0', 'BLAZE_SERVEUR_MISSION_ID': '2000000',
               'BLAZE_DELAI_NOTIFICATIONS_MS': '0', 'BLAZE_HUB_PARTAGE': '0', 'BLAZE_ESCOUADE_AUTO': '0',
               'BLAZE_PERSONAS': '', 'BLAZE_CAPTURES': str(root / 'captures'),
               'TLS_CERT': str(certificate), 'TLS_KEY': str(key), 'JOUEUR_ID': '1000', 'JOUEUR_NOM': 'Wire test'}
        context = ssl.create_default_context(cafile=str(certificate))
        streams = []
        def connect():
            stream = context.wrap_socket(socket.create_connection(('127.0.0.1', port), timeout=3),
                                         server_hostname='spring18.gosredirector.ea.com')
            streams.append(stream)
            return stream
        log = root / 'blaze.log'
        with log.open('wb') as output:
            server = subprocess.Popen([str(binary.resolve())], cwd=root, env=env, stdout=output, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 8
            while True:
                try:
                    active = connect()
                    break
                except ConnectionRefusedError:
                    if server.poll() is not None or time.monotonic() >= deadline:
                        raise RuntimeError(log.read_text())
                    time.sleep(.02)
            for index, quest in enumerate((1401555469, 680915601, 1401555469)):
                message = index * 10 + 1
                sid = scenario(exchange(active, 0x10, message, launch(quest)))
                assert sid == index + 1
                assert scenario(exchange(active, 0x10, message, launch(quest))) == sid
                exchange(active, 0x0f, message + 1, field('GID', 0, integer(sid + 1)))
                exchange(active, 0x16, message + 2, field('GID', 0, integer(sid + 1)))
            sid = scenario(exchange(active, 0x10, 40, launch(1401555469)))
            probe = connect()
            exchange(probe, 0, 1, component=0, kind=4)
            probe.close()
            assert scenario(exchange(active, 0x10, 40, launch(1401555469))) == sid
            replacement = connect()
            newer = scenario(exchange(replacement, 0x10, 50, launch(680915601)))
            assert newer == sid + 1
            # A stale socket is rejected; its teardown cannot remove the replacement.
            try:
                exchange(active, 0x16, 51, field('GID', 0, integer(sid + 1)))
            except (EOFError, OSError):
                pass
            active.close()
            assert scenario(exchange(replacement, 0x10, 50, launch(680915601))) == newer
            exchange(replacement, 0x11, 52, field('MSID', 0, integer(newer - 1)))  # stale cancellation
            assert scenario(exchange(replacement, 0x10, 50, launch(680915601))) == newer
            exchange(replacement, 0x11, 53, field('MSID', 0, integer(newer)))
            last = scenario(exchange(replacement, 0x10, 54, launch(1401555469)))
            assert last == newer + 1
            for stream in streams:
                stream.close()
            deadline = time.monotonic() + 3
            while True:
                events = [json.loads(line.partition(' = ')[2]) for line in log.read_text().splitlines()
                          if line.startswith('MISSION_EVENT = ')]
                if events and events[-1]['event'] == 'disconnected':
                    break
                if time.monotonic() >= deadline:
                    raise AssertionError(events)
                time.sleep(.01)
            assert [e['game_id'] for e in events if e['event'] == 'requested'] == list(range(2, 8)), events
            assert [(e['event'], e['game_id']) for e in events if e['event'] in ('disconnected', 'cancelled')] == [
                ('disconnected', 5), ('cancelled', 6), ('disconnected', 7)], events
            print('Live verified TLS/Fire2: 6 sessions; repeated launches, retransmission, exit, stale traffic, cancellation, probes and connection replacement passed')
        finally:
            for stream in streams:
                stream.close()
            server.terminate()
            server.wait(timeout=5)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('binary', type=Path)
    check(parser.parse_args().binary)
