"""Local desktop controls for the existing socket-activated remote service."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from device_config import device_identity

ROOT = Path(__file__).resolve().parent
SOCKETS = ['jon-city-web.socket', 'jon-city-vnc.socket']


class RemoteController:
    def __init__(self, root=ROOT, run=None):
        self.root = Path(root)
        self.run = run or subprocess.run
        self.config_path = self.root / '.state/remote.json'

    def settings(self):
        if self.config_path.exists():
            return device_identity(json.loads(self.config_path.read_text()), self.root / 'device.json')
        device_path = self.root / 'device.json'
        device = json.loads(device_path.read_text()) if device_path.exists() else {}
        return dict(device_id=device.get('device_id', 'JON-CITY-001'),
                    location=device.get('location', ''),
                    listen_address=device.get('address', '192.168.50.1/24').split('/')[0],
                    web_port=8080, vnc_port=5901, max_clients=3,
                    display='auto', xauthority='',
                    novnc_dir='/usr/share/novnc', x11vnc='/usr/bin/x11vnc',
                    websockify='/usr/bin/websockify', cert='', key='')

    def status(self):
        try:
            result = self.run(['systemctl', 'is-active', *SOCKETS],
                              text=True, capture_output=True, timeout=8)
            states = result.stdout.splitlines()
            if len(states) == 2 and all(state == 'active' for state in states):
                config = self.settings()
                scheme = 'https' if config.get('cert') else 'http'
                host = config.get('hostname', config['listen_address'])
                return f"허용 중 · {scheme}://{host}:{config['web_port']}\nAP에 연결한 기기에서 접속하세요."
            if 'active' in states:
                return '일부 서비스만 실행 중 · 설정 후 다시 실행하세요.'
            if any(state == 'failed' for state in states):
                return '서비스 오류 · 설정 후 다시 실행하세요.'
            return '중지됨'
        except (OSError, ValueError, KeyError, subprocess.SubprocessError):
            return '서비스 상태를 확인할 수 없습니다.'

    def _command(self, command, **kwargs):
        result = self.run(command, text=True, capture_output=True, timeout=180, **kwargs)
        if result.returncode:
            raise RuntimeError((result.stderr or result.stdout).strip()[-1200:]
                               or '작업이 취소되었거나 실패했습니다.')
        return result

    def _admin(self, action, *args):
        command = ['/bin/bash', str(self.root / 'install.sh'), action, *args]
        if os.geteuid() != 0:
            command.insert(0, '/usr/bin/pkexec')
        return self._command(command)

    @staticmethod
    def validate_password(password, confirmation, saved):
        if password != confirmation:
            raise ValueError('비밀번호와 확인 입력이 일치하지 않습니다.')
        if not password:
            if not saved:
                raise ValueError('최초 실행 시 원격 접속 비밀번호를 입력하세요.')
            return
        if not re.fullmatch(r'[!-~]{8,63}', password):
            raise ValueError('원격 접속 비밀번호는 영문·숫자·기호 8~63자로 입력하세요(공백 제외). '
                             'VNC 인증은 앞 8자만 사용합니다.')

    def _password(self, config, password):
        # A new session prevents x11vnc from reading a controlling terminal.
        # Pass the password via stdin, never argv, environment or a plaintext file.
        # x11vnc and noVNC both use only the first eight characters for VNC auth.
        target = Path(config['password_file'])
        with tempfile.TemporaryDirectory(dir=target.parent) as directory:
            path = Path(directory) / 'vnc.passwd'
            self._command([config['x11vnc'], '-storepasswd', str(path)],
                          input=f'{password}\n{password}\ny\n', start_new_session=True)
            if not path.is_file() or path.stat().st_size != 8:
                raise RuntimeError('VNC 비밀번호 파일을 생성하지 못했습니다.')
            path.chmod(0o600)
            path.replace(target)

    def enable(self, settings, password='', confirmation='', autostart=False):
        old = self.settings()
        saved = Path(old.get('password_file', self.root / '.state/vnc.passwd')).is_file()
        self.validate_password(password, confirmation, saved)
        config = device_identity({**old, **settings}, self.root / 'device.json')
        if config['display'] == 'auto' and 'xauthority' not in settings:
            config['xauthority'] = ''
        command = [sys.executable, str(self.root / 'remote.py'), 'prepare']
        for key in ('device_id', 'location', 'listen_address', 'web_port', 'vnc_port',
                    'max_clients', 'display', 'xauthority', 'x11vnc', 'websockify'):
            command += ['--' + key.replace('_', '-'), str(config[key])]
        command += ['--novnc-dir', str(config.get('novnc_dir', '/usr/share/novnc'))]
        for key in ('cert', 'key'):
            if config.get(key):
                command += ['--' + key, config[key]]
        self._command(command)
        # A first headless install may have no desktop until the next boot.
        # The installer prepares Xorg; VNC verifies the session on connection.
        prepared = json.loads(self.config_path.read_text())
        if password:
            self._password(prepared, password)
        self._admin('remote-enable', 'on' if autostart else 'off')
        return self.status()

    def stop(self):
        self._admin('stop')
        return self.status()
