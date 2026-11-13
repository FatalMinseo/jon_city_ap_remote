#!/usr/bin/env python3
"""Prepare and run the on-demand browser desktop service."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import subprocess
import sys
import tempfile

from device_config import validate_device_id, device_identity
from x11_session import authorization_candidates, resolve_session, session_environment

ROOT = Path(__file__).resolve().parent
STATE = ROOT / ".state"
BUILD = ROOT / "build"


def load_config(path):
    path = Path(path)
    root = path.parent.parent if path.parent.name == '.state' else path.parent
    return device_identity(json.loads(path.read_text()), root / 'device.json')


def unit_quote(value):
    value = str(value)
    if any(char in value for char in "\r\n\x00"):
        raise ValueError("잘못된 경로")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'


def resolve_auth(config):
    candidates = authorization_candidates(config)
    for path in candidates:
        if path.is_file() and os.access(path, os.R_OK) and path.stat().st_size > 0:
            return str(path)
    raise RuntimeError('읽을 수 있는 X11 인증 파일이 없습니다: ' + ', '.join(map(str, candidates)))


def detect_display(config, socket_dir='/tmp/.X11-unix', run=None):
    return resolve_session(config, socket_dir=socket_dir, run=run)['display']


def save_display(path, config, display):
    path = Path(path)
    backup = path.with_name(path.name + '.before-display-fix')
    if not backup.exists():
        shutil.copy2(path, backup)
        backup.chmod(0o600)
    fd, temporary = tempfile.mkstemp(prefix='.remote-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as output:
            json.dump({**config, 'display': display}, output, ensure_ascii=False, indent=2)
            output.write('\n')
            os.fchmod(output.fileno(), 0o600)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def apply_wide_resolution(config):
    """Use an advertised 1080p mode, including the headless modeline alias."""
    try:
        env = session_environment(config['display'], resolve_auth(config))
        query = subprocess.run(['xrandr', '--query'], env=env, capture_output=True,
                               text=True, timeout=5)
        if query.returncode:
            raise RuntimeError('출력 조회 실패')
        outputs = {}
        output = None
        for line in query.stdout.splitlines():
            match = re.match(r'^(\S+) connected\b', line)
            if match:
                output = match.group(1)
                outputs[output] = []
            elif line and not line[0].isspace():
                output = None
            elif output:
                mode = re.match(r'\s+(1920x1080(?:_60)?)\s+(.+)', line)
                if mode and any(abs(float(rate) - 60) < 0.2
                                for rate in re.findall(r'\d+(?:\.\d+)?', mode.group(2))):
                    outputs[output].append(mode.group(1))
        output = 'DP-1' if 'DP-1' in outputs else next(iter(outputs)) if len(outputs) == 1 else None
        if output is None or not outputs[output]:
            raise RuntimeError('1080p 출력·모드를 확인할 수 없음')
        result = subprocess.run(
            ['xrandr', '--output', output, '--mode', outputs[output][0], '--rate', '60'],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=5)
        if result.returncode == 0:
            return True
    except (OSError, RuntimeError, subprocess.SubprocessError):
        pass
    print("1920×1080·60Hz 설정 실패: 연결된 출력·지원 모드·X11 인증을 확인하세요. "
          "현재 해상도로 원격 접속을 계속합니다.", file=sys.stderr)
    return False


def vnc_command(config):
    # systemd already connects fd 2 to the journal. Write to that inherited fd:
    # reopening /dev/stderr fails with ENXIO when fd 2 is a journal Unix socket.
    # Don't use -q either: inetd + quiet redirects diagnostics to /dev/null.
    return [config["x11vnc"], "-inetd", "-once", "-shared",
            "-display", config["display"], "-auth", resolve_auth(config),
            "-rfbauth", config["password_file"], "-desktop", config["device_id"],
            "-noxdamage", "-noscr", "-noclipboard", "-safer", "-xkb",
            "-wait", "100", "-defer", "30", "-timeout", "20"]


def server_command(config, socket_activated=False):
    if 'connection_security' in config:
        command = ['/usr/bin/python3', str(ROOT / 'gateway.py'),
                   '--config', config['connection_security']['config_file']]
        if socket_activated:
            command += ['--inetd']
        return command
    command = [config["websockify"], "--web", config["web_root"], "--heartbeat", "20"]
    if config.get("cert"):
        command += ["--cert", config["cert"], "--key", config["key"], "--ssl-only"]
    if socket_activated:
        command += ["--inetd", "--idle-timeout", "30"]
    else:
        command += [f'{config["listen_address"]}:{config["web_port"]}']
    command += [f'127.0.0.1:{config["vnc_port"]}']
    return command


def prepare(args):
    if os.geteuid() == 0:
        raise ValueError("prepare는 바탕화면 사용자의 일반 계정으로 실행하세요.")
    identity = device_identity(vars(args), ROOT / 'device.json')
    args.device_id, args.location = identity['device_id'], identity['location']
    if not args.device_id:
        raise ValueError('먼저 AP 장비 ID를 설정하거나 --device-id로 최초 장비 ID를 지정하세요.')
    validate_device_id(args.device_id)
    if not 1 <= args.max_clients <= 3:
        raise ValueError("동시 연결 수는 1~3으로 지정하세요.")
    address = ipaddress.IPv4Address(args.listen_address)
    if address.is_unspecified or address.is_multicast:
        raise ValueError("전체 인터페이스 대신 AP 또는 localhost IPv4를 지정하세요.")
    if args.display != 'auto' and not re.fullmatch(r":\d+(\.\d+)?", args.display):
        raise ValueError("X11 DISPLAY는 auto 또는 :0 형식으로 지정하세요.")
    for port in (args.web_port, args.vnc_port):
        if not 1024 <= port <= 65535:
            raise ValueError("포트는 1024~65535 사이여야 합니다.")
    if args.web_port == args.vnc_port:
        raise ValueError("웹 포트와 VNC 포트는 달라야 합니다.")
    if bool(args.cert) != bool(args.key):
        raise ValueError("TLS 인증서와 키를 함께 지정하세요.")
    assets = Path(args.novnc_dir).resolve()
    if not (assets / "vnc.html").is_file():
        raise ValueError("noVNC 파일이 없습니다. 먼저 의존성을 설치하세요.")
    for binary in (args.x11vnc, args.websockify):
        if not os.access(binary, os.X_OK):
            raise ValueError(f"실행 파일이 없습니다: {binary}")
    STATE.mkdir(exist_ok=True, mode=0o700)
    BUILD.mkdir(exist_ok=True)
    web_root = ROOT / "public"
    web_root.mkdir(exist_ok=True)
    shutil.copytree(assets, web_root / "novnc", dirs_exist_ok=True)
    username = pwd.getpwuid(os.getuid()).pw_name
    config = dict(device_id=args.device_id, location=args.location, user=username,
                  listen_address=str(address), web_port=args.web_port,
                  vnc_port=args.vnc_port, display=args.display, max_clients=args.max_clients,
                  xauthority=args.xauthority, x11vnc=str(Path(args.x11vnc).resolve()),
                  websockify=str(Path(args.websockify).resolve()),
                  web_root=str(web_root), password_file=str(STATE / "vnc.passwd"),
                  cert=str(Path(args.cert).resolve()) if args.cert else "",
                  key=str(Path(args.key).resolve()) if args.key else "")
    config_path = STATE / "remote.json"
    scheme = 'https' if args.cert else 'http'
    config['connection_security'] = {
        'config_file': str(config_path),
        'database': str(STATE / 'connection_attempts.sqlite3'),
        'allowed_origins': [f'{scheme}://{address}:{args.web_port}'],
        'session_seconds': 1800,
    }
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n")
    config_path.chmod(0o600)
    (web_root / "device.json").write_text(json.dumps(
        {"device_id": args.device_id, "location": args.location}, ensure_ascii=False))
    command = " ".join(map(unit_quote, ["/usr/bin/python3", ROOT / "remote.py"]))
    config_arg = unit_quote(config_path)
    (BUILD / "jon-city-vnc.socket").write_text(f"""[Unit]
Description=JON-CITY on-demand VNC listener (localhost only)

[Socket]
ListenStream=127.0.0.1:{args.vnc_port}
Accept=yes
MaxConnections={args.max_clients}
KeepAlive=yes
Backlog=3

[Install]
WantedBy=sockets.target
""")
    (BUILD / "jon-city-vnc@.service").write_text(f"""[Unit]
Description=JON-CITY desktop session %I
ConditionPathExists={str(STATE / 'vnc.passwd').replace('%', '%%')}
CollectMode=inactive-or-failed

[Service]
Type=simple
User={username}
ExecStart={command} vnc --config {config_arg}
StandardInput=socket
StandardOutput=socket
StandardError=journal
NoNewPrivileges=yes
UMask=0077
TimeoutStopSec=5
""")
    (BUILD / "jon-city-web.socket").write_text(f"""[Unit]
Description=JON-CITY browser listener

[Socket]
ListenStream={address}:{args.web_port}
FreeBind=yes
Accept=no
KeepAlive=yes
KeepAliveTimeSec=30
KeepAliveIntervalSec=10
KeepAliveProbes=3

[Install]
WantedBy=sockets.target
""")
    (BUILD / "jon-city-web.service").write_text(f"""[Unit]
Description=JON-CITY browser desktop gateway
Requires=jon-city-vnc.socket
ConditionPathExists={str(STATE / 'vnc.passwd').replace('%', '%%')}

[Service]
Type=simple
User={username}
ExecStart={command} web --socket-activated --config {config_arg}
StandardInput=socket
StandardOutput=journal
StandardError=journal
NoNewPrivileges=yes
UMask=0077
TimeoutStopSec=5

""")
    scheme = "https" if args.cert else "http"
    print(f"설정 준비 완료: {scheme}://{address}:{args.web_port}")
    print("아직 서비스 설치/시작이나 AP 활성화는 하지 않았습니다.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    setup = commands.add_parser("prepare")
    setup.add_argument("--device-id", help='AP 설정이 없을 때 최초 장비 ID 지정')
    setup.add_argument("--location", default="")
    setup.add_argument("--listen-address", default="192.168.50.1")
    setup.add_argument("--web-port", type=int, default=8080)
    setup.add_argument("--vnc-port", type=int, default=5901)
    setup.add_argument("--max-clients", type=int, default=3)
    setup.add_argument("--display", default="auto")
    setup.add_argument("--xauthority", default="")
    setup.add_argument("--novnc-dir", default="/usr/share/novnc")
    setup.add_argument("--x11vnc", default="/usr/bin/x11vnc")
    setup.add_argument("--websockify", default="/usr/bin/websockify")
    setup.add_argument("--cert")
    setup.add_argument("--key")
    for name in ("web", "vnc", "passwd", "check", "detect-display", "repair-display", "auto-display"):
        command = commands.add_parser(name)
        command.add_argument("--config", default=str(STATE / "remote.json"))
        if name == "web":
            command.add_argument("--socket-activated", action="store_true")
        if name == 'detect-display':
            command.add_argument('--save', action='store_true')
    args = parser.parse_args()
    if args.command == "prepare":
        prepare(args)
        return
    config = load_config(args.config)
    if args.command == 'auto-display':
        if os.geteuid() == 0:
            raise ValueError('화면 설정은 sudo 없이 바탕화면 사용자 계정으로 실행하세요.')
        authorization_candidates(config)  # Verify this is the configured desktop user.
        save_display(args.config, {**config, 'xauthority': ''}, 'auto')
        print('바탕화면 자동 선택을 저장했습니다. 접속할 때 화면과 인증 파일을 확인합니다.')
    elif args.command in ('detect-display', 'repair-display'):
        if os.geteuid() == 0:
            raise ValueError('화면 확인은 sudo 없이 바탕화면 사용자의 계정으로 실행하세요.')
        resolved = resolve_session(config, wait=5)
        save = args.command == 'repair-display' or args.save
        if save:
            repaired = {**config, 'xauthority': resolved['xauthority']}
            save_display(args.config, repaired, resolved['display'])
        print(f"서비스 환경에서 접근 가능한 화면: {resolved['display']} · "
              f"인증 파일: {resolved['xauthority']}" + (' · 설정 저장 완료' if save else ''))
    elif args.command == "passwd":
        subprocess.run([config["x11vnc"], "-storepasswd", config["password_file"]], check=True)
        Path(config["password_file"]).chmod(0o600)
    elif args.command == "check":
        resolved = resolve_session(config, wait=5)
        print(f"{config['device_id']}: 서비스 환경에서 X11 접근 가능 · "
              f"{resolved['display']} · {resolved['screen_width']}×{resolved['screen_height']} · "
              f"인증={resolved['xauthority']}")
    else:
        if not Path(config["password_file"]).is_file():
            raise ValueError("먼저 remote.py passwd로 원격 접속 비밀번호를 지정하세요.")
        if args.command == "vnc":
            config = resolve_session(config, wait=12)
            print(f"X11 준비 완료: UID={os.getuid()}, DISPLAY={config['display']}, "
                  f"인증={config['xauthority']}", file=sys.stderr)
            if config.get('set_resolution', True):
                apply_wide_resolution(config)
        command = (vnc_command(config) if args.command == "vnc" else
                   server_command(config, args.socket_activated))
        os.execvpe(command[0], command, os.environ.copy())


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
