"""Resolve an accessible X11 session without relying on an interactive shell."""
import os
from pathlib import Path
import pwd
import re
import subprocess
import time


def session_environment(display, authority):
    env = os.environ.copy()
    for name in ('DISPLAY', 'XAUTHORITY', 'WAYLAND_DISPLAY', 'DBUS_SESSION_BUS_ADDRESS'):
        env.pop(name, None)
    env.update(DISPLAY=display, XAUTHORITY=str(authority), LC_ALL='C.UTF-8')
    return env


def authorization_candidates(config):
    user = config.get('user')
    account = pwd.getpwnam(user) if user else pwd.getpwuid(os.getuid())
    if account.pw_uid != os.getuid():
        raise RuntimeError(f'X11 실행 계정 불일치: 설정={account.pw_name}, '
                           f'실행 UID={os.getuid()}. 원격 서비스를 바탕화면 사용자로 재설치하세요.')
    explicit = config.get('xauthority', '')
    if explicit and explicit != 'guess':
        return [Path(explicit)]
    return [Path(f'/run/user/{account.pw_uid}/gdm/Xauthority'),
            Path(account.pw_dir) / '.Xauthority']


def resolve_session(config, wait=0, socket_dir='/tmp/.X11-unix', run=None,
                    clock=time.monotonic, sleep=time.sleep):
    run = run or subprocess.run
    candidates = authorization_candidates(config)
    deadline = clock() + wait
    preferred = config.get('display', 'auto')
    automatic = preferred == 'auto'
    if not automatic and not re.fullmatch(r':\d+(\.\d+)?', preferred):
        raise ValueError('잘못된 X11 화면 번호')
    while True:
        failures = []
        displays = [] if automatic else [preferred]
        displays += [':' + path.name[1:] for path in sorted(Path(socket_dir).glob('X[0-9]*'))
                     if path.is_socket() and path.name[1:].isdigit()
                     and ':' + path.name[1:] != preferred]
        accessible = []
        for display in displays:
            for path in candidates:
                try:
                    if not path.is_file():
                        failures.append(f'{display}: 인증 파일 없음 ({path})')
                        continue
                    if not os.access(path, os.R_OK):
                        failures.append(f'{display}: 인증 파일 읽기 권한 없음 ({path})')
                        continue
                    if path.stat().st_size == 0:
                        failures.append(f'{display}: 인증 파일이 비어 있음 ({path})')
                        continue
                    result = run(['/usr/bin/xdpyinfo'], env=session_environment(display, path),
                                 text=True, encoding='utf-8', errors='replace',
                                 capture_output=True, timeout=3)
                except (OSError, subprocess.TimeoutExpired) as error:
                    failures.append(f'{display}: {type(error).__name__}: {error}')
                    continue
                geometry = re.search(r'dimensions:\s+(\d+)x(\d+)\s+pixels', result.stdout or '')
                if result.returncode == 0 and geometry:
                    width, height = map(int, geometry.groups())
                    if width > 0 and height > 0:
                        accessible.append(dict(config, display=display, xauthority=str(path),
                                               screen_width=width, screen_height=height))
                        break
                reason = ' '.join((result.stderr or '').split())[:320]
                failures.append(f'{display}: xdpyinfo 종료={result.returncode}, '
                                + (reason or '유효한 화면 크기를 확인하지 못함'))
            if accessible and display == preferred:
                return accessible[0]
        # Do not select another display while the configured desktop is booting.
        if len(accessible) == 1 and (automatic or clock() >= deadline):
            return accessible[0]
        if len(accessible) > 1:
            raise RuntimeError('접근 가능한 화면이 여러 개입니다. 사용할 DISPLAY를 지정하세요: '
                               + ', '.join(item['display'] for item in accessible))
        if clock() >= deadline:
            raise RuntimeError('서비스 환경에서 X11 화면/인증을 확인하지 못했습니다. '
                               f'DISPLAY={preferred}, UID={os.getuid()}, 확인한 인증 경로: '
                               + ', '.join(map(str, candidates))
                               + '. 자동 로그인과 무모니터 Xorg 설정을 확인하세요. '
                               '터미널의 DISPLAY/XAUTHORITY는 상속하지 않습니다. '
                               '실패 상세: ' + ' / '.join(failures[:6]))
        sleep(min(0.5, max(0, deadline - clock())))
