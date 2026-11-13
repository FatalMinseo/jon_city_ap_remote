#!/usr/bin/env python3
"""Prepare reversible headless X11 settings for the R36.4 Jetson Tegra profile."""
import argparse
import json
import os
from pathlib import Path
import pwd
import re
import stat
import subprocess
import sys
import tempfile

HEADLESS = '''# Managed by jon_city_ap_remote/headless_setup.py
Section "Monitor"
    Identifier "JonCityHeadlessMonitor"
    HorizSync 30-85
    VertRefresh 50-75
    Modeline "1920x1080_60" 148.50 1920 2008 2052 2200 1080 1084 1089 1125 +HSync +VSync
EndSection

Section "Screen"
    Identifier "JonCityHeadlessScreen"
    Device "Tegra0"
    Monitor "JonCityHeadlessMonitor"
    DefaultDepth 24
    Option "ConnectedMonitor" "{connector}"
    Option "UseDisplayDevice" "{connector}"
    Option "UseEDID" "False"
    Option "ModeValidation" "AllowNonEdidModes"
    Option "ModeDebug" "True"
    Option "MetaModes" "{connector}: 1920x1080_60 +0+0"
    SubSection "Display"
        Depth 24
        Modes "1920x1080_60"
        Virtual 1920 1080
    EndSubSection
EndSection
'''


def gdm_settings(text, user):
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.-]*\$?', user):
        raise ValueError('잘못된 바탕화면 사용자 이름입니다.')
    lines = text.splitlines()
    sections = [(i, match.group(1)) for i, line in enumerate(lines)
                if (match := re.match(r'^\s*\[([^]]+)\]', line))]
    daemon = [i for i, name in sections if name == 'daemon']
    if len(daemon) > 1:
        raise ValueError('GDM 설정에 [daemon] 구역이 중복되어 있습니다.')
    if not daemon:
        lines += ['', '[daemon]']
        start, end = len(lines) - 1, len(lines)
    else:
        start = daemon[0]
        end = next((i for i, _ in sections if i > start), len(lines))
    values = dict(WaylandEnable='false', AutomaticLoginEnable='true',
                  AutomaticLogin=user, TimedLoginEnable='false')
    result, seen = [], set()
    for line in lines[start + 1:end]:
        match = re.match(r'^\s*(?:#\s*)?([A-Za-z]+)\s*=', line)
        key = match.group(1) if match else None
        if key in values:
            if key not in seen:
                result.append(f'{key}={values[key]}')
                seen.add(key)
        else:
            result.append(line)
    result += [f'{key}={value}' for key, value in values.items() if key not in seen]
    return '\n'.join(lines[:start + 1] + result + lines[end:]) + '\n'


def atomic_write(path, text, mode=0o644, owner=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as output:
            output.write(text)
            os.fchmod(output.fileno(), mode)
            if owner is not None:
                os.fchown(output.fileno(), *owner)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


class HeadlessSetup:
    def __init__(self, root='/'):
        self.root = Path(root)
        self.paths = dict(gdm=self.root / 'etc/gdm3/custom.conf',
                          screen=self.root / 'etc/X11/xorg.conf.d/99-jon-city-headless.conf')
        self.backup = self.root / 'var/lib/jon-city-ap-remote/headless-backup.json'

    def snapshot(self):
        result = {}
        for key, path in self.paths.items():
            if path.is_symlink():
                raise ValueError(f'심볼릭 링크 설정은 자동 변경하지 않습니다: {path}')
            if path.exists():
                info = path.stat()
                result[key] = dict(text=path.read_text(), mode=stat.S_IMODE(info.st_mode),
                                   uid=info.st_uid, gid=info.st_gid)
            else:
                result[key] = None
        return result

    def restore_snapshot(self, snapshot):
        for key, path in self.paths.items():
            value = snapshot[key]
            if value is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write(path, value['text'], value['mode'], (value['uid'], value['gid']))

    def validate(self, connector):
        if not re.fullmatch(r'DFP-\d+', connector):
            raise ValueError('출력 식별자는 DFP-1 형식으로 지정하세요.')
        release = (self.root / 'etc/nv_tegra_release').read_text()
        if not re.search(r'# R36\b.*REVISION:\s*4\.', release):
            raise ValueError('이 자동 설정은 Jetson R36.4 계열만 지원합니다.')
        xorg = (self.root / 'etc/X11/xorg.conf').read_text()
        devices = re.findall(r'(?ims)^\s*Section\s+"Device"\s*$.*?^\s*EndSection\s*$', xorg)
        if not any(re.search(r'Identifier\s+"Tegra0"', block)
                   and re.search(r'Driver\s+"nvidia"', block) for block in devices):
            raise ValueError('기존 NVIDIA Tegra0 장치 설정을 확인할 수 없습니다.')
        logs = list((self.root / 'var/log').glob('Xorg.*.log'))
        output = re.escape(connector)
        pattern = (rf'NVIDIA\(GPU-\d+\):[ \t]*(?:{output}(?=$|[\s:(])'
                   rf'|[^\r\n]*\({output}\))')
        if not any(re.search(pattern,
                             path.read_text(errors='replace')) for path in logs):
            raise ValueError(f'Xorg 로그에서 {connector} 지원을 확인하지 못했습니다. '
                             '출력 식별자를 먼저 확인하세요.')
        if not self.paths['gdm'].is_file():
            raise ValueError('기존 GDM 설정 파일이 없습니다.')

    def apply(self, user, connector='DFP-1', manage_screen=True):
        self.validate(connector)
        current = self.snapshot()
        gdm = gdm_settings(current['gdm']['text'], user)
        screen = HEADLESS.format(connector=connector)
        if not self.backup.exists():
            self.backup.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            atomic_write(self.backup, json.dumps(current, ensure_ascii=False, indent=2) + '\n', 0o600)
        try:
            value = current['gdm']
            atomic_write(self.paths['gdm'], gdm, value['mode'], (value['uid'], value['gid']))
            if manage_screen:
                value = current['screen']
                atomic_write(self.paths['screen'], screen, value['mode'] if value else 0o644,
                             (value['uid'], value['gid']) if value else None)
        except Exception:
            self.restore_snapshot(current)
            raise

    def restore(self):
        if not self.backup.is_file():
            raise ValueError('복구할 최초 백업이 없습니다.')
        current = self.snapshot()
        original = json.loads(self.backup.read_text())
        try:
            self.restore_snapshot(original)
        except Exception:
            self.restore_snapshot(current)
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    apply = commands.add_parser('apply')
    apply.add_argument('--user', required=True)
    apply.add_argument('--connector', default='DFP-1')
    commands.add_parser('restore')
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise ValueError('이 설정 명령은 sudo 권한이 필요합니다.')
    setup = HeadlessSetup()
    from adaptive_headless import AdaptiveHeadless
    adaptive = AdaptiveHeadless()
    if args.command == 'apply':
        account = pwd.getpwnam(args.user)
        if account.pw_uid == 0:
            raise ValueError('root 대신 실제 바탕화면 사용자를 지정하세요.')
        adaptive.install(setup, args.user, args.connector)
        print(f'{args.user} 자동 로그인·X11·부팅 시 물리/무모니터 출력 선택을 준비했습니다.')
    else:
        adaptive.restore(setup)
        print('무모니터 출력과 GDM 설정을 최초 백업으로 복원했습니다.')
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    print('아직 그래픽 서비스나 장비를 재시작하지 않았습니다.')
    print('그래픽 작업을 저장한 뒤 모니터를 분리한 상태로 재부팅해 검증하세요.')


if __name__ == '__main__':
    try:
        main()
    except (KeyError, ValueError, OSError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
