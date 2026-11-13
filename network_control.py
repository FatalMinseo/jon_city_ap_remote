"""NetworkManager operations shared by the local Wi-Fi/AP window and tests."""
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import uuid

ROOT = Path(__file__).resolve().parent


def split_fields(line):
    """Decode nmcli's escaped colon/backslash fields without shell parsing."""
    fields, current, escaped = [], [], False
    for character in line:
        if escaped:
            current.append(character)
            escaped = False
        elif character == '\\':
            escaped = True
        elif character == ':':
            fields.append(''.join(current))
            current = []
        else:
            current.append(character)
    if escaped:
        current.append('\\')
    fields.append(''.join(current))
    return fields


class NMClient:
    def run(self, *arguments, change=False):
        command = ['/usr/bin/nmcli', '--wait', '20', *arguments]
        options = dict(text=True, encoding='utf-8', errors='replace',
                       capture_output=True, timeout=35,
                       env={**os.environ, 'LC_ALL': 'C.UTF-8'})
        result = subprocess.run(command, **options)
        if result.returncode and change and os.geteuid() != 0:
            message = result.stderr.lower()
            permission_error = any(word in message for word in (
                'not authorized', 'insufficient privileges', 'permission denied',
                'not permitted', 'policykit', 'authorization'))
            if permission_error:
                # Use the desktop's normal authentication dialog. Never store an
                # administrator password or install a passwordless sudo rule.
                result = subprocess.run(['/usr/bin/pkexec', *command],
                                        **{**options, 'timeout': 180})
        if result.returncode:
            message = result.stderr.strip()
            if len(message) > 500:
                message = message[:500] + '…'
            raise RuntimeError(message or 'NetworkManager 작업이 취소되었거나 실패했습니다.')
        return result.stdout.strip()


class NetworkController:
    def __init__(self, config_path=None, client=None, state_path=None):
        self.config_path = Path(config_path or ROOT / 'device.json')
        self.state_path = Path(state_path or ROOT / '.state/network-ui.json')
        self.client = client or NMClient()

    def configuration(self):
        if not self.config_path.exists():
            raise ValueError('AP 설정이 없습니다. 먼저 apctl.py configure로 장비를 설정하세요.')
        config = json.loads(self.config_path.read_text())
        if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,15}', config['interface']):
            raise ValueError('AP 설정의 무선 인터페이스 이름이 잘못되었습니다.')
        ipaddress.IPv4Interface(config['address'])
        return config

    def state(self):
        config = self.configuration()
        output = self.client.run('-t', '-f',
                                 'GENERAL.STATE,GENERAL.CONNECTION,GENERAL.CON-UUID,IP4.ADDRESS',
                                 'device', 'show', config['interface'])
        values = {}
        for line in output.splitlines():
            fields = split_fields(line)
            if len(fields) == 2:
                values[fields[0]] = fields[1]
        active_uuid = values.get('GENERAL.CON-UUID', '')
        mode = 'disconnected'
        if active_uuid not in ('', '--'):
            wireless_mode = self.client.run('-g', '802-11-wireless.mode',
                                            'connection', 'show', 'uuid', active_uuid)
            mode = 'ap' if wireless_mode == 'ap' else 'wifi'
        return dict(config=config, mode=mode, uuid=active_uuid,
                    connection=values.get('GENERAL.CONNECTION', '--'),
                    status=values.get('GENERAL.STATE', '--'),
                    addresses=[value for key, value in values.items() if key.startswith('IP4.ADDRESS')])

    def wifi_profiles(self):
        config = self.configuration()
        output = self.client.run('-t', '-f', 'UUID,TYPE,NAME', 'connection', 'show')
        profiles = []
        for line in output.splitlines():
            fields = split_fields(line)
            if len(fields) != 3 or fields[1] not in ('802-11-wireless', 'wifi'):
                continue
            identity, _, name = fields
            if name == config['profile']:
                continue
            details = self.client.run('-t', '-f', '802-11-wireless.mode,connection.interface-name',
                                      'connection', 'show', 'uuid', identity)
            values = dict(split_fields(line) for line in details.splitlines() if line)
            mode, interface = values.get('802-11-wireless.mode', ''), values.get('connection.interface-name', '')
            if mode == 'ap' or (interface not in ('', '--', config['interface'])):
                continue
            profiles.append(dict(uuid=identity, name=name))
        return profiles

    def _remember_wifi(self, identity):
        if identity not in ('', '--'):
            uuid.UUID(identity)
            self.state_path.parent.mkdir(exist_ok=True, mode=0o700)
            self.state_path.write_text(json.dumps({'last_wifi_uuid': identity}) + '\n')
            self.state_path.chmod(0o600)

    def preferred_wifi(self):
        if self.state_path.exists():
            return json.loads(self.state_path.read_text()).get('last_wifi_uuid', '')
        return self.configuration().get('previous_connection_uuid', '')

    def switch(self, mode, wifi_uuid=None, persist=True):
        config = self.configuration()
        current = self.state()
        if mode == 'ap':
            if current['mode'] == 'wifi':
                self._remember_wifi(current['uuid'])
            self.client.run('connection', 'up', 'id', config['profile'],
                            'ifname', config['interface'], change=True)
            target = ('id', config['profile'])
        elif mode == 'wifi':
            identity = str(uuid.UUID(wifi_uuid or self.preferred_wifi()))
            # Only an existing infrastructure profile may be activated here.
            if identity not in {profile['uuid'] for profile in self.wifi_profiles()}:
                raise ValueError('선택한 Wi-Fi 설정이 없습니다. 목록을 새로고침하세요.')
            self.client.run('connection', 'up', 'uuid', identity,
                            'ifname', config['interface'], change=True)
            self._remember_wifi(identity)
            target = ('uuid', identity)
        elif mode == 'off':
            if current['mode'] != 'ap' or current['connection'] != config['profile']:
                raise ValueError('이 장비의 AP가 켜져 있을 때만 AP를 끌 수 있습니다.')
            self.client.run('device', 'disconnect', config['interface'], change=True)
            target = None
        else:
            raise ValueError('지원하지 않는 전환 모드')
        if persist:
            try:
                self.client.run('connection', 'modify', 'id', config['profile'],
                                'connection.autoconnect', 'yes' if mode == 'ap' else 'no',
                                'connection.autoconnect-priority', '100', change=True)
                if mode == 'wifi':
                    self.client.run('connection', 'modify', *target,
                                    'connection.autoconnect', 'yes', change=True)
            except (RuntimeError, OSError, subprocess.SubprocessError) as error:
                raise RuntimeError('연결은 전환됐지만 부팅 자동 연결 설정을 저장하지 못했습니다. '
                                   '상태를 확인하세요. ' + str(error)) from error
        return self.state()
