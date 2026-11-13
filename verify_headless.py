#!/usr/bin/env python3
"""Isolated Xvfb / production VNC regression; never changes host Xorg or GDM."""
import argparse
import json
import os
from pathlib import Path
import pwd
import secrets
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
from unittest.mock import patch

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from x11_session import resolve_session, session_environment
import remote
from headless_setup import HeadlessSetup
import adaptive_headless
import display_mode


def receive(client, size):
    data = b''
    while len(data) < size:
        part = client.recv(size - len(data))
        if not part:
            raise RuntimeError('VNC disconnected before framebuffer arrived')
        data += part
    return data


def connector_log_regressions(directory):
    root = directory / 'connector-logs'
    files = {'etc/nv_tegra_release': '# R36 (release), REVISION: 4.7',
             'etc/X11/xorg.conf': 'Section "Device"\n Identifier "Tegra0"\n Driver "nvidia"\nEndSection\n',
             'etc/gdm3/custom.conf': '[daemon]\n',
             'var/log/Xorg.0.log': ''}
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    setup = HeadlessSetup(root)
    original = setup.snapshot()
    log = root / 'var/log/Xorg.0.log'
    supported = (
        'NVIDIA(GPU-0): DFP-1',
        'NVIDIA(GPU-0): DFP-1: connected\n',
        'NVIDIA(GPU-2): DFP-1 (boot)',
        '[ 123.4] (--) NVIDIA(GPU-0): LG Electronics LG FULL HD (DFP-1): connected\n',
        'NVIDIA(GPU-0): Vendor (Monitor model) (DFP-1): disconnected',
    )
    rejected = (
        'NVIDIA(GPU-0): DFP-0',
        'NVIDIA(GPU-0): DFP-10',
        'NVIDIA(GPU-0): DFP-1x',
        'NVIDIA(GPU-0): LG FULL HD (DFP-10): connected',
        'NVIDIA(GPU-0): LG FULL HD (DFP-0): connected',
        'NVIDIA(GPU-0): LG FULL HD\nOther driver (DFP-1): connected',
        'Other driver: LG FULL HD (DFP-1): connected',
    )
    for text in supported:
        log.write_text(text)
        setup.validate('DFP-1')
    for text in rejected:
        log.write_text(text)
        try:
            setup.validate('DFP-1')
        except ValueError as error:
            assert 'Xorg 로그에서 DFP-1' in str(error)
        else:
            raise AssertionError(f'Wrong connector accepted: {text!r}')
    assert setup.snapshot() == original
    print(f'PASS: Xorg direct/named connector formats ({len(supported)} accepted, {len(rejected)} rejected); no settings changed')


def regressions(directory):
    auth = directory / 'delayed-auth'
    ticks = [0]
    probes = []
    def probe(command, **kwargs):
        env = kwargs['env']
        assert env['DISPLAY'] == ':91' and env['XAUTHORITY'] == str(auth)
        assert 'WAYLAND_DISPLAY' not in env and 'DBUS_SESSION_BUS_ADDRESS' not in env
        probes.append(env)
        return subprocess.CompletedProcess(command, 0, 'dimensions: 1280x720 pixels', '')
    def advance(delay):
        ticks[0] += delay
        auth.write_text('test-cookie-file')
    config = dict(display=':91', xauthority=str(auth), user=pwd.getpwuid(os.getuid()).pw_name)
    with patch.dict(os.environ, DISPLAY=':999', XAUTHORITY='/bad/path',
                    WAYLAND_DISPLAY='wayland-99', DBUS_SESSION_BUS_ADDRESS='stale'):
        result = resolve_session(config, wait=1, socket_dir=directory, run=probe,
                                 clock=lambda: ticks[0], sleep=advance)
    assert probes and ticks[0] > 0 and result['screen_width'] == 1280
    auth.unlink()
    try:
        resolve_session(config, socket_dir=directory, run=probe)
    except RuntimeError:
        pass
    else:
        raise AssertionError('Missing authorization accepted')
    auth.write_text('test-cookie-file')
    with socket.socket(socket.AF_UNIX) as fixture:
        fixture.bind(str(directory / 'X91'))
        resolved = resolve_session({**config, 'display': ':29999'}, socket_dir=directory,
            run=lambda command, **kw: subprocess.CompletedProcess(command,
                0 if kw['env']['DISPLAY'] == ':91' else 1,
                'dimensions: 1280x720 pixels', ''))
        assert resolved['display'] == ':91'
    automatic_dir = directory / 'auto-sockets'
    automatic_dir.mkdir()
    def ready(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, 'dimensions: 1920x1080 pixels', '')
    def unexpected_wait(delay):
        raise AssertionError('Auto mode waited for a nonexistent fixed display')
    for number in (0, 1):
        path = automatic_dir / f'X{number}'
        with socket.socket(socket.AF_UNIX) as fixture:
            fixture.bind(str(path))
            result = resolve_session({**config, 'display': 'auto'}, wait=12,
                socket_dir=automatic_dir, run=ready, sleep=unexpected_wait)
            assert result['display'] == f':{number}'
        path.unlink()
    with socket.socket(socket.AF_UNIX) as first, socket.socket(socket.AF_UNIX) as second:
        first.bind(str(automatic_dir / 'X0'))
        second.bind(str(automatic_dir / 'X1'))
        try:
            resolve_session({**config, 'display': 'auto'}, socket_dir=automatic_dir, run=ready)
        except RuntimeError as error:
            assert '여러 개' in str(error)
        else:
            raise AssertionError('Auto mode picked an arbitrary desktop')
    other = 'root' if os.getuid() else 'nobody'
    try:
        resolve_session({**config, 'user': other}, socket_dir=directory, run=probe)
    except RuntimeError:
        pass
    else:
        raise AssertionError('Mismatched desktop user accepted')
    for mode in ('1920x1080', '1920x1080_60'):
        calls = []
        def randr(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0,
                f'DP-1 connected 1920x1080+0+0\n   {mode} 60.00*+\n', '')
        with patch.object(remote.subprocess, 'run', side_effect=randr):
            assert remote.apply_wide_resolution(config)
        assert calls[-1] == ['xrandr', '--output', 'DP-1', '--mode', mode, '--rate', '60']
    setup = HeadlessSetup(directory / 'fake-system')
    files = {'etc/nv_tegra_release': '# R36 (release), REVISION: 4.7',
             'etc/X11/xorg.conf': 'Section "Device"\n Identifier "Tegra0"\n Driver "nvidia"\nEndSection\n',
             'etc/gdm3/custom.conf': '[daemon]\n#WaylandEnable=false\n',
             'var/log/Xorg.0.log': 'NVIDIA(GPU-0): DFP-1'}
    for name, text in files.items():
        path = setup.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    original = setup.snapshot()
    setup.apply(config['user'])
    assert 'ConnectedMonitor" "DFP-1"' in setup.paths['screen'].read_text()
    assert 'AutomaticLogin=' + config['user'] in setup.paths['gdm'].read_text()
    setup.restore()
    assert setup.snapshot() == original
    print('PASS: delayed authorization, clean environment, DISPLAY recovery, missing-auth / user rejection')
    print('PASS: auto selects :0 / :1 immediately; ambiguous desktops rejected')
    print('PASS: both 1080p names; headless settings apply/restore in temporary filesystem')


def installation_regression(directory):
    """Run the installer with fake privileged operations and a disposable config."""
    source = Path(remote.__file__).parent
    deploy = directory / 'installation'
    deploy.mkdir()
    for name in ('install.sh', 'remote.py', 'device_config.py', 'x11_session.py'):
        shutil.copy2(source / name, deploy / name)
    (deploy / 'headless_setup.py').write_text(
        'from pathlib import Path\nimport sys\n'
        'Path(__file__).with_name("headless-called").write_text(" ".join(sys.argv[1:]))\n')
    state = deploy / '.state'
    state.mkdir()
    configpath = state / 'remote.json'
    original = dict(user=pwd.getpwuid(os.getuid()).pw_name, display=':1',
                    xauthority='/old/auth', device_id='JON-CITY-001',
                    password_file=str(state / 'vnc.passwd'),
                    connection_security={'session_seconds': 1800})
    configpath.write_text(json.dumps(original))
    password = state / 'vnc.passwd'
    password.write_bytes(b'01234567')
    build = deploy / 'build'
    build.mkdir()
    for name in ('jon-city-vnc.socket', 'jon-city-vnc@.service',
                 'jon-city-web.socket', 'jon-city-web.service'):
        (build / name).touch()
    fakebin = deploy / 'fakebin'
    fakebin.mkdir()
    sudo = fakebin / 'sudo'
    sudo.write_text('#!/usr/bin/python3\nimport os,sys\nargs=sys.argv[1:]\n'
                    'if args[0] == "-u": args=args[2:]\n'
                    'if args[0] == "/usr/bin/python3": os.execv(args[0], args)\n'
                    'assert args[0] in ("systemd-analyze", "install", "systemctl"), args\n')
    sudo.chmod(0o700)
    env = {**os.environ, 'PATH': str(fakebin) + ':' + os.environ.get('PATH', '/usr/bin:/bin')}
    for attempt in range(2):
        if attempt:
            # The UI calls this installer through pkexec (root). Simulate the
            # branch without granting privilege or changing real system files.
            identity = fakebin / 'id'
            identity.write_text('#!/bin/sh\nprintf "0\\n"\n')
            identity.chmod(0o700)
        result = subprocess.run(['/bin/bash', str(deploy / 'install.sh'), 'services'],
                                env=env, text=True, capture_output=True)
        assert result.returncode == 0, result.stdout + result.stderr
        assert json.loads(configpath.read_text()) == {**original, 'display': 'auto', 'xauthority': ''}
        assert json.loads((state / 'remote.json.before-display-fix').read_text()) == original
        assert password.read_bytes() == b'01234567'
    if Path('/etc/nv_tegra_release').exists():
        assert (deploy / 'headless-called').read_text() == f'apply --user {original["user"]} --connector DFP-1'
    print('PASS: installer prepares Jetson output and migrates old display automatically; backup/password preserved')


def adaptive_regressions(directory):
    root = directory / 'adaptive-system'
    setup = HeadlessSetup(root)
    files = {'etc/nv_tegra_release': '# R36 (release), REVISION: 4.7',
             'etc/X11/xorg.conf': 'Section "Device"\n Identifier "Tegra0"\n Driver "nvidia"\nEndSection\n',
             'etc/gdm3/custom.conf': '[daemon]\n#WaylandEnable=false\n',
             'var/log/Xorg.0.log': 'NVIDIA(GPU-0): DFP-1',
             'etc/systemd/system/gdm.service': '[Service]\nExecStart=/usr/sbin/gdm3\n',
             'sys/class/drm/card0-DP-1/status': 'connected',
             'sys/class/drm/card0-DP-1/modes': '1920x1080\n'}
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    alias = root / 'etc/systemd/system/display-manager.service'
    alias.symlink_to('gdm.service')
    original = setup.snapshot()
    adaptive = adaptive_headless.AdaptiveHeadless(root)
    user = pwd.getpwuid(os.getuid()).pw_name
    assert adaptive.install(setup, user, wait=0) == 'physical'
    paths = adaptive.paths('gdm.service')
    screen = setup.paths['screen']
    assert not screen.exists()
    assert 'Wants=jon-city-display-select.service' in paths['dropin'].read_text()
    assert 'Requires=' not in paths['dropin'].read_text()
    assert 'Before=gdm.service' in paths['unit'].read_text()
    assert f'--user "{user}"' in paths['autostart'].read_text()
    assert 'select_mode' in paths['display_helper'].read_text()
    subprocess.run(['systemd-analyze', 'verify', str(paths['unit'])],
                   capture_output=True, text=True, check=True)
    status = root / 'sys/class/drm/card0-DP-1/status'
    modes = status.with_name('modes')
    status.write_text('disconnected')
    assert adaptive_headless.select_mode(root, wait=0) == 'headless'
    forced = screen.read_text()
    assert '1920x1080_60' in forced and 'ConnectedMonitor" "DFP-1"' in forced
    status.write_text('unknown')
    assert adaptive_headless.select_mode(root, wait=0) == 'unknown-preserved'
    assert screen.read_text() == forced
    screen.unlink()
    assert adaptive_headless.select_mode(root, wait=0) == 'unknown-fallback'
    assert screen.read_text() == forced
    status.write_text('connected')
    modes.write_text('')
    assert adaptive_headless.select_mode(root, wait=0) == 'unknown-preserved'
    modes.write_text('1920x1080\n')
    edid = status.with_name('edid')
    edid.write_bytes(b'')
    assert adaptive_headless.select_mode(root, wait=0) == 'unknown-preserved'
    edid.write_bytes(b'\0\xff\xff\xff\xff\xff\xff\0' + bytes(120))
    assert adaptive_headless.select_mode(root, wait=0) == 'physical'
    assert not screen.exists()
    screen.write_text('user custom output\n')
    assert adaptive_headless.select_mode(root, wait=0) == 'custom-preserved'
    assert screen.read_text() == 'user custom output\n'
    screen.unlink()
    ticks = [0]
    status.write_text('unknown')
    def advance(delay):
        ticks[0] += delay
        status.write_text('connected')
    assert adaptive_headless.select_mode(root, wait=5, clock=lambda: ticks[0], sleep=advance) == 'physical'
    assert ticks[0] > 0
    # A missing DRM tree and unreadable status must preserve a working fallback.
    status.write_text('disconnected')
    adaptive_headless.select_mode(root, wait=0)
    status.unlink()
    assert adaptive_headless.select_mode(root, wait=0) == 'unknown-preserved'
    status.write_text('connected')
    read_text = Path.read_text
    def unreadable(path, *args, **kwargs):
        if path == status:
            raise PermissionError('fixture')
        return read_text(path, *args, **kwargs)
    with patch.object(Path, 'read_text', unreadable):
        assert adaptive_headless.select_mode(root, wait=0) == 'unknown-preserved'
    desktop_before = setup.snapshot()
    before = adaptive_headless.snapshot(paths)
    write = adaptive_headless.atomic_write
    failed = []
    def fail_once(path, *args, **kwargs):
        if path == paths['helper'] and not failed:
            failed.append(True)
            raise OSError('simulated install write failure')
        return write(path, *args, **kwargs)
    with patch.object(adaptive_headless, 'atomic_write', fail_once):
        try:
            adaptive.install(setup, user, wait=0)
        except OSError:
            pass
        else:
            raise AssertionError('Simulated install failure ignored')
    assert setup.snapshot() == desktop_before
    assert adaptive_headless.snapshot(paths) == before
    # Removing boot hooks and restoring GDM/Xorg must be one recoverable operation.
    with patch.object(setup, 'restore', side_effect=OSError('simulated restore failure')):
        try:
            adaptive.restore(setup)
        except OSError:
            pass
        else:
            raise AssertionError('Simulated restore failure ignored')
    assert setup.snapshot() == desktop_before
    assert adaptive_headless.snapshot(paths) == before
    adaptive.restore(setup)
    assert setup.snapshot() == original
    assert all(not path.exists() for path in paths.values())
    # The field workaround had exactly this template but no ownership comment.
    status.write_text('disconnected')
    screen.write_text(forced.split('\n', 1)[1])
    assert adaptive.install(setup, user, wait=0) == 'headless'
    assert screen.read_text().startswith(adaptive_headless.MARKER)
    status.write_text('connected')
    assert adaptive_headless.select_mode(root, wait=0) == 'physical'
    protected = root / 'user-screen.conf'
    protected.write_text('custom file target')
    screen.symlink_to(protected)
    assert adaptive_headless.select_mode(root, wait=0) == 'custom-preserved'
    assert screen.is_symlink() and protected.read_text() == 'custom file target'
    screen.unlink()
    adaptive.restore(setup)
    assert setup.snapshot() == original
    # Upgrade backups made before login-time 1080p was introduced.
    saved = json.loads(adaptive.backup.read_text())
    for key in ('display_helper', 'autostart'):
        saved['files'].pop(key)
    adaptive.backup.write_text(json.dumps(saved))
    adaptive.install(setup, user, wait=0)
    upgraded = json.loads(adaptive.backup.read_text())
    assert upgraded['files']['display_helper'] is None and upgraded['files']['autostart'] is None
    adaptive.restore(setup)
    assert all(not path.exists() for path in paths.values())
    print('PASS: adaptive physical/headless/unknown selection, mode/EDID checks, custom files, delayed DRM')
    print('PASS: boot unit syntax, failure rollback, restoration, legacy field config, symlink protection')


def login_display_regressions():
    # The target reported this exact preferred/current 1024x768 layout.
    query = ('DP-0 disconnected\nDP-1 connected primary 1024x768+0+0\n'
             '   1024x768 75.03*+ 70.07 60.00\n   1920x1080 60.00 59.93\n')
    assert display_mode.select_mode(query) == ('DP-1', '1920x1080')
    assert display_mode.select_mode(query.replace('1920x1080 ', '1920x1080_60 ')) == ('DP-1', '1920x1080_60')
    calls, ticks = [], [0]
    def probe(command, **kwargs):
        assert kwargs['env']['DISPLAY'] == ':17'
        assert kwargs['env']['XAUTHORITY'] == '/test/auth'
        calls.append(command)
        if command[-1] == '--query':
            return subprocess.CompletedProcess(command, 0, '' if ticks[0] == 0 else query, '')
        return subprocess.CompletedProcess(command, 0, '', '')
    with patch.dict(os.environ, DISPLAY=':17', XAUTHORITY='/test/auth'):
        display_mode.apply(run=probe, clock=lambda: ticks[0], sleep=lambda delay: ticks.__setitem__(0, ticks[0] + delay))
        assert calls[-1] == ['/usr/bin/xrandr', '--output', 'DP-1', '--mode', '1920x1080', '--rate', '60']
        try:
            display_mode.apply(wait=0, run=lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, '', ''))
        except RuntimeError:
            pass
        else:
            raise AssertionError('Unsupported mode accepted')
    print('PASS: login-time 1024x768 to supported 1080p, inherited session, delayed readiness, unsupported mode rejection')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-root', default='/')
    parser.add_argument('--ui', action='store_true', help='Also verify the UI using this isolated X server')
    args = parser.parse_args()
    runtime = Path(args.runtime_root).resolve()
    env = os.environ.copy()
    for name in ('DISPLAY', 'XAUTHORITY', 'WAYLAND_DISPLAY', 'DBUS_SESSION_BUS_ADDRESS'):
        env.pop(name, None)
    if runtime != Path('/'):
        env['LD_LIBRARY_PATH'] = str(runtime / 'usr/lib/aarch64-linux-gnu')
    with tempfile.TemporaryDirectory(prefix='jon-headless-') as temporary:
        directory = Path(temporary)
        connector_log_regressions(directory)
        regressions(directory)
        installation_regression(directory)
        adaptive_regressions(directory)
        login_display_regressions()
        number = next(n for n in range(90, 190)
                      if not Path(f'/tmp/.X11-unix/X{n}').exists()
                      and not Path(f'/tmp/.X{n}-lock').exists())
        display = f':{number}'
        auth = directory / 'Xauthority'
        auth.touch(mode=0o600)
        subprocess.run(['xauth', '-f', str(auth), '-'],
                       input=f'add {display} . {secrets.token_hex(16)}\n',
                       text=True, env=env, capture_output=True, check=True)
        logpath = directory / 'vnc.log'
        with (directory / 'xvfb.log').open('wb') as xlog, logpath.open('wb') as vlog:
            xvfb = subprocess.Popen([str(runtime / 'usr/bin/Xvfb'), display,
                                     '-screen', '0', '1280x720x24', '-auth', str(auth),
                                     '-nolisten', 'tcp', '-noreset'], env=env, stdout=xlog, stderr=xlog)
            process = None
            client = None
            try:
                config = dict(display=display, xauthority=str(auth),
                              user=pwd.getpwuid(os.getuid()).pw_name)
                resolved = resolve_session(config, wait=10)
                assert resolved['display'] == display, 'Isolated X server did not start'
                if args.ui:
                    ui_env = session_environment(display, auth)
                    ui_env.update({key: value for key, value in env.items()
                                   if key not in ('DISPLAY', 'XAUTHORITY')})
                    subprocess.run([sys.executable, str(Path(remote.__file__).with_name('verify_network_ui.py'))],
                                   env=ui_env, check=True, timeout=45)
                password = secrets.token_hex(4)
                pwfile = directory / 'vnc.passwd'
                x11vnc = str(runtime / 'usr/bin/x11vnc')
                subprocess.run([x11vnc, '-storepasswd', str(pwfile)],
                               input=f'{password}\n{password}\ny\n', text=True,
                               env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               start_new_session=True, check=True)
                config.update(password_file=str(pwfile), x11vnc=x11vnc,
                              device_id='JON-CITY-HEADLESS-VERIFY', set_resolution=False)
                configpath = directory / 'remote.json'
                configpath.write_text(json.dumps(config))
                with socket.socket() as listener:
                    listener.bind(('127.0.0.1', 0))
                    listener.listen(1)
                    client = socket.create_connection(listener.getsockname(), timeout=20)
                    accepted, _ = listener.accept()
                with accepted:
                    process = subprocess.Popen([sys.executable, str(Path(remote.__file__)),
                                                'vnc', '--config', str(configpath)],
                                               stdin=accepted, stdout=accepted, stderr=vlog, env=env)
                assert receive(client, 12) == b'RFB 003.008\n'
                client.sendall(b'RFB 003.008\n')
                types = receive(client, receive(client, 1)[0])
                assert 2 in types and 1 not in types
                client.sendall(b'\x02')
                challenge = receive(client, 16)
                key = bytes(int(f'{byte:08b}'[::-1], 2) for byte in password.encode())
                encryptor = Cipher(algorithms.TripleDES(key), modes.ECB()).encryptor()
                client.sendall(encryptor.update(challenge) + encryptor.finalize())
                assert receive(client, 4) == b'\0' * 4
                client.sendall(b'\x01')
                header = receive(client, 24)
                width, height = struct.unpack('>HH', header[:4])
                assert (width, height) == (1280, 720)
                name = receive(client, struct.unpack('>I', header[20:24])[0])
                assert name == b'JON-CITY-HEADLESS-VERIFY'
                client.sendall(struct.pack('>BBHi', 2, 0, 1, 0))  # Raw encoding
                client.sendall(struct.pack('>BBHHHH', 3, 0, 0, 0, 1, 1))
                update = receive(client, 4)
                assert update[0] == 0 and struct.unpack('>H', update[2:])[0] == 1
                x, y, w, h, encoding = struct.unpack('>HHHHi', receive(client, 12))
                assert (x, y, w, h, encoding) == (0, 0, 1, 1, 0)
                receive(client, header[4] // 8)
                print('PASS: production remote.py vnc, VNC password, actual pixel (1280x720)')
                client.close()
                client = None
                assert process.wait(timeout=10) == 0
                print('PASS: VNC exits after disconnect')
            except Exception:
                vlog.flush()
                xlog.flush()
                print((directory / 'xvfb.log').read_text(errors='replace'), file=sys.stderr)
                print(logpath.read_text(errors='replace'), file=sys.stderr)
                raise
            finally:
                if client:
                    client.close()
                for child in (process, xvfb):
                    if child and child.poll() is None:
                        child.terminate()
                        try:
                            child.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            child.kill()
                            child.wait()
    print('NVIDIA physical-monitor-free cold boot still requires verification on the target Jetson.')


if __name__ == '__main__':
    main()
