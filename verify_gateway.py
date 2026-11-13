#!/usr/bin/env python3
"""Loopback-only transport regression test; does not access the real desktop."""
import base64
import json
import os
from pathlib import Path
import queue
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

ROOT = Path(__file__).resolve().parent


def read_exact(connection, size):
    data = b''
    while len(data) < size:
        part = connection.recv(size - len(data))
        if not part:
            raise AssertionError('Unexpected EOF')
        data += part
    return data


def headers(connection):
    data = b''
    while not data.endswith(b'\r\n\r\n'):
        data += read_exact(connection, 1)
        if len(data) > 16384:
            raise AssertionError('HTTP headers too large')
    return data


def websocket(port, origin):
    connection = socket.create_connection(('127.0.0.1', port), timeout=15)
    nonce = base64.b64encode(secrets.token_bytes(16)).decode()
    request = (f'GET /websockify HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n'
               f'Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {nonce}\r\n'
               f'Sec-WebSocket-Version: 13\r\nSec-WebSocket-Protocol: binary\r\n'
               f'Origin: {origin}\r\n\r\n')
    connection.sendall(request.encode())
    return connection, headers(connection)


def main():
    clients = []
    process = None
    connections = queue.Queue()
    releases = queue.Queue()
    backend = socket.socket()
    backend.bind(('127.0.0.1', 0))
    backend.listen(10)

    def handle_target(connection):
        with connection:
            try:
                connection.sendall(b'RFB 003.008\n')
                while connection.recv(1024):
                    pass
            except OSError:
                pass
            finally:
                releases.put(True)

    def accept_targets():
        while True:
            try:
                connection, _ = backend.accept()
            except OSError:
                return
            connections.put(True)
            threading.Thread(target=handle_target, args=(connection,), daemon=True).start()

    threading.Thread(target=accept_targets, daemon=True).start()
    with tempfile.TemporaryDirectory() as temporary:
        temporary = Path(temporary)
        listener = socket.socket()
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
        listener.close()
        origin = f'http://127.0.0.1:{port}'
        config = dict(listen_address='127.0.0.1', web_port=port,
                      vnc_port=backend.getsockname()[1], web_root=str(ROOT / 'public'),
                      cert='', key='', connection_security=dict(
                          database=str(temporary / 'attempts.sqlite3'),
                          allowed_origins=[origin], session_seconds=10))
        config_path = temporary / 'remote.json'
        config_path.write_text(json.dumps(config))
        with open(temporary / 'gateway.log', 'wb') as log:
            process = subprocess.Popen([sys.executable, str(ROOT / 'gateway.py'),
                                        '--config', str(config_path)],
                                       stdout=log, stderr=log, start_new_session=True)
            try:
                for _ in range(50):
                    try:
                        with urllib.request.urlopen(origin + '/', timeout=1) as response:
                            assert response.status == 200
                        break
                    except OSError:
                        if process.poll() is not None:
                            raise AssertionError((temporary / 'gateway.log').read_text())
                        time.sleep(0.1)
                else:
                    raise AssertionError('Gateway did not start')
                client, result = websocket(port, 'https://attacker.invalid')
                clients.append(client)
                assert b' 403 ' in result
                client.close()
                assert connections.empty(), 'Rejected origin reached VNC backend'
                print('Cross-origin WebSocket rejected before VNC: PASS', flush=True)
                for _ in range(5):
                    client, result = websocket(port, origin)
                    clients.append(client)
                    assert b' 101 ' in result, result
                    frame = read_exact(client, 2)
                    assert frame[0] == 0x82, frame
                    assert read_exact(client, frame[1]).startswith(b'RFB ')
                    connections.get(timeout=2)
                    if len(clients) > 2:
                        client.close()
                client, result = websocket(port, origin)
                clients.append(client)
                assert b' 429 ' in result
                client.close()
                assert connections.empty(), 'Rate-limited request reached VNC backend'
                print('Sixth attempt rejected before VNC across forked workers: PASS', flush=True)
                held = clients[1]
                frame = read_exact(held, 2)
                assert frame[0] == 0x88, frame
                payload = read_exact(held, frame[1])
                assert b'expired' in payload, payload
                held.close()
                for _ in range(5):
                    releases.get(timeout=3)
                print('Session expiry closes WebSocket and downstream connection: PASS', flush=True)
            finally:
                for client in clients:
                    client.close()
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)
                    process.wait(timeout=5)
                backend.close()


if __name__ == '__main__':
    main()
