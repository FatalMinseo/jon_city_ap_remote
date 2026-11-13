import json
import os
from pathlib import Path
import secrets
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import argparse
sys.path.insert(0, str(Path(__file__).resolve().parent))
import remote
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

root = Path(__file__).resolve().parent
parser = argparse.ArgumentParser(description='로컬 읽기 전용 원격 접속 통합 검증')
parser.add_argument('--runtime-root', default='/')
parser.add_argument('--xauthority', default=f'/run/user/{os.getuid()}/gdm/Xauthority')
args = parser.parse_args()
runtime = Path(args.runtime_root)
env = {**os.environ, 'LD_LIBRARY_PATH': str(runtime / 'usr/lib/aarch64-linux-gnu'),
       'PYTHONPATH': str(runtime / 'usr/lib/python3/dist-packages')}
x11vnc = str(runtime / 'usr/bin/x11vnc')

def receive(connection, count):
    data = b''
    while len(data) < count:
        part = connection.recv(count - len(data))
        if not part:
            raise RuntimeError('VNC connection closed early')
        data += part
    return data

with tempfile.TemporaryDirectory() as temporary:
    temporary = Path(temporary)
    password = secrets.token_hex(4)
    passwd_file = temporary / 'vnc.passwd'
    subprocess.run([x11vnc, '-storepasswd', password, str(passwd_file)],
                   env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    config = dict(x11vnc=x11vnc, display=':0', device_id='JON-CITY-TEST',
                  xauthority=args.xauthority, password_file=str(passwd_file))
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen(1)
    client = socket.create_connection(listener.getsockname(), timeout=10)
    accepted, _ = listener.accept()
    with open(temporary / 'vnc.log', 'wb') as log:
        # Reproduce StandardError=journal: fd 2 is a Unix stream socket, not a file.
        log_reader, log_writer = socket.socketpair()
        def collect_logs():
            with log_reader:
                while True:
                    data = log_reader.recv(8192)
                    if not data:
                        break
                    log.write(data)
        collector = threading.Thread(target=collect_logs, daemon=True)
        collector.start()
        process = subprocess.Popen(remote.vnc_command(config), stdin=accepted, stdout=accepted,
                                   stderr=log_writer, env=env)
        log_writer.close()
        accepted.close()
        listener.close()
        try:
            assert receive(client, 12).startswith(b'RFB ')
            client.sendall(b'RFB 003.008\n')
            security_count = receive(client, 1)[0]
            security_types = receive(client, security_count)
            assert 2 in security_types and 1 not in security_types, security_types
            client.sendall(b'\x02')
            challenge = receive(client, 16)
            key = bytes(int(f'{byte:08b}'[::-1], 2) for byte in password.encode())
            encryptor = Cipher(algorithms.TripleDES(key), modes.ECB()).encryptor()
            client.sendall(encryptor.update(challenge) + encryptor.finalize())
            assert receive(client, 4) == b'\0' * 4
            client.sendall(b'\x01')
            header = receive(client, 24)
            width, height = struct.unpack('>HH', header[:4])
            name_length = struct.unpack('>I', header[20:24])[0]
            assert receive(client, name_length).decode() == 'JON-CITY-TEST'
            client.sendall(struct.pack('>BBHHHH', 3, 0, 0, 0, width, height))
            assert receive(client, 1) == b'\x00'
            status = Path(f'/proc/{process.pid}/status').read_text()
            rss = next(line for line in status.splitlines() if line.startswith('VmRSS:'))
            print(f'VNC password authentication + framebuffer: PASS ({width}x{height}; {rss})')
        finally:
            client.close()
            try:
                code = process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                raise RuntimeError('VNC process did not exit after disconnection')
        print(f'VNC process exits on disconnect: PASS (exit={code})')
        collector.join(timeout=5)
        assert not collector.is_alive(), 'Journal socket did not close'
        log.flush()
        assert (temporary / 'vnc.log').stat().st_size > 0, 'Missing VNC diagnostics'
        print('Journal-compatible stderr socket retains diagnostics: PASS')
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(('127.0.0.1', 0))
    listener.listen(8)
    port = listener.getsockname()[1]
    config.update(websockify=str(runtime / 'usr/bin/websockify'), web_root=str(runtime / 'usr/share/novnc'),
                  listen_address='127.0.0.1', web_port=port, vnc_port=5901)
    command = remote.server_command(config, True)
    command[command.index('--idle-timeout') + 1] = '2'
    with open(temporary / 'web.log', 'wb') as log:
        process = subprocess.Popen(command, stdin=listener, stdout=log, stderr=log, env=env)
        try:
            time.sleep(1)
            for path in ('/vnc.html', '/app/ui.js', '/core/rfb.js'):
                with urllib.request.urlopen(f'http://127.0.0.1:{port}{path}', timeout=5) as response:
                    assert response.status == 200 and len(response.read()) > 100
            print('Inherited web socket + offline noVNC assets: PASS')
            process.wait(timeout=12)
            assert process.returncode == 0
            print('Web gateway exits after idle: PASS')
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
            listener.close()
