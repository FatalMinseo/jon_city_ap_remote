#!/usr/bin/env python3
"""Install the network window launcher for the current desktop user."""
import argparse
import os
from pathlib import Path
import shlex
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def launcher(project):
    path = str(project)
    if any(character in path for character in '\r\n\x00'):
        raise ValueError('잘못된 프로젝트 경로')
    # Desktop Entry Exec uses its own escaping, not a shell command.
    executable = (str(project / 'network_ui.py').replace('\\', '\\\\')
                  .replace('"', '\\"').replace('`', '\\`').replace('$', '\\$')
                  .replace('%', '%%'))
    return f'''[Desktop Entry]
Type=Application
Version=1.0
Name=JON-CITY 네트워크
Comment=외부 Wi-Fi와 현장 AP 전환
Exec=/usr/bin/python3 "{executable}"
Path={path}
Icon=network-wireless
Terminal=false
Categories=Network;Settings;
'''


def desktop_directory(home, config_home, run=subprocess.run):
    """Resolve the actual XDG desktop even when run from an SSH shell."""
    try:
        result = run(['xdg-user-dir', 'DESKTOP'], text=True,
                     capture_output=True, timeout=5)
        value = result.stdout.strip()
        if result.returncode == 0 and value and Path(value).is_absolute():
            path = Path(value)
            return None if path == home else path
    except (OSError, subprocess.SubprocessError):
        pass
    settings = config_home / 'user-dirs.dirs'
    if settings.exists():
        for line in settings.read_text().splitlines():
            if not line.strip().startswith('XDG_DESKTOP_DIR='):
                continue
            values = shlex.split(line.split('=', 1)[1], comments=True)
            if not values:
                continue
            value = values[0]
            for prefix in ('$HOME', '${HOME}'):
                if value == prefix or value.startswith(prefix + '/'):
                    value = str(home) + value[len(prefix):]
                    break
            path = Path(value)
            if path.is_absolute():
                return None if path == home else path
    for name in ('Desktop', '바탕화면'):
        if (home / name).is_dir():
            return home / name
    return home / 'Desktop'


def install(project, home, desktop=None, run=subprocess.run):
    applications = Path(os.environ.get('XDG_DATA_HOME', str(home / '.local/share'))) / 'applications'
    applications.mkdir(parents=True, exist_ok=True)
    text = launcher(project)
    path = applications / 'jon-city-network.desktop'
    path.write_text(text)
    path.chmod(0o755)
    print(f'앱 메뉴 아이콘 생성: {path}')
    if desktop is None:
        config_home = Path(os.environ.get('XDG_CONFIG_HOME', str(home / '.config')))
        desktop = desktop_directory(home, config_home, run)
    if desktop is None or desktop == home:
        print('바탕화면 경로가 홈 폴더로 설정되어 있어 바탕화면 복사는 하지 않았습니다.')
        print('앱 메뉴에서 실행하거나 --desktop-dir로 실제 바탕화면 폴더를 지정하세요.')
        return None
    desktop.mkdir(parents=True, exist_ok=True)
    target = desktop / path.name
    target.write_text(text)
    target.chmod(0o755)
    print(f'바탕화면 아이콘 생성: {target}')
    env = os.environ.copy()
    runtime = Path(f'/run/user/{os.getuid()}')
    if runtime.is_dir():
        env.setdefault('XDG_RUNTIME_DIR', str(runtime))
        if (runtime / 'bus').exists():
            env.setdefault('DBUS_SESSION_BUS_ADDRESS', f'unix:path={runtime}/bus')
    try:
        result = run(['gio', 'set', str(target), 'metadata::trusted', 'true'],
                     capture_output=True, timeout=5, env=env)
        if result.returncode != 0:
            print('아이콘은 생성됐지만 실행 허용을 자동 설정하지 못했습니다.')
    except (OSError, subprocess.SubprocessError):
        print('아이콘은 생성됐지만 실행 허용을 자동 설정하지 못했습니다.')
    print('실행이 막히면 바탕화면 아이콘을 우클릭 → 실행 허용을 선택하세요.')
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--desktop-dir', type=Path, help='실제 바탕화면 경로를 직접 지정')
    args = parser.parse_args()
    if os.geteuid() == 0:
        raise SystemExit('바탕화면 사용자의 일반 계정으로 실행하세요. sudo를 사용하지 않습니다.')
    desktop = args.desktop_dir.expanduser().resolve() if args.desktop_dir else None
    install(ROOT, Path.home(), desktop)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError) as error:
        print(f'아이콘 설치 실패: {error}', file=sys.stderr)
        sys.exit(1)
