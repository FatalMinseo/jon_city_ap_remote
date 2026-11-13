#!/usr/bin/env python3
# Managed by jon_city_ap_remote/adaptive_headless.py
"""Select the existing desktop's output before GDM; never start a new desktop."""
import argparse
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import time

MARKER = '# Managed by jon_city_ap_remote/'
UNIT = 'jon-city-display-select.service'
STATE = 'var/lib/jon-city-ap-remote/adaptive-headless.json'
SCREEN = 'etc/X11/xorg.conf.d/99-jon-city-headless.conf'
HELPER = 'usr/local/lib/jon-city-ap-remote/adaptive_headless.py'


def atomic_write(path, text, mode=0o644, owner=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.city-display-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as output:
            output.write(text)
            os.fchmod(output.fileno(), mode)
            if owner is not None:
                os.fchown(output.fileno(), *owner)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def snapshot(paths):
    result = {}
    for name, path in paths.items():
        if path.is_symlink():
            raise ValueError(f'심볼릭 링크 설정은 변경하지 않습니다: {path}')
        if path.exists():
            info = path.stat()
            result[name] = dict(text=path.read_text(), mode=stat.S_IMODE(info.st_mode),
                                uid=info.st_uid, gid=info.st_gid)
        else:
            result[name] = None
    return result


def restore_snapshot(paths, values):
    for name, path in paths.items():
        value = values[name]
        if value is None:
            path.unlink(missing_ok=True)
        else:
            atomic_write(path, value['text'], value['mode'], (value['uid'], value['gid']))


def detect(drm):
    """Check connector status and advertised modes/EDID, not the live X geometry."""
    states, physical, details = [], False, []
    for path in sorted(drm.glob('card*-*/status')):
        try:
            value = path.read_text().strip()
            usable = value == 'connected'
            evidence = False
            modes = path.with_name('modes')
            edid = path.with_name('edid')
            if usable and modes.exists():
                evidence = True
                usable = any(re.fullmatch(r'[1-9]\d*x[1-9]\d*', line.strip())
                             for line in modes.read_text().splitlines())
            if value == 'connected' and edid.exists():
                evidence = True
                data = edid.read_bytes()
                usable = usable and len(data) >= 128 and data[:8] == b'\0\xff\xff\xff\xff\xff\xff\0'
            physical = physical or (usable and evidence)
            states.append(value)
            details.append(f'{path.parent.name}={value}' + ('/usable' if usable and evidence else ''))
        except OSError as error:
            states.append('unknown')
            details.append(f'{path.parent.name}={type(error).__name__}')
    if physical:
        return 'physical', details
    if states and all(value == 'disconnected' for value in states):
        return 'headless', details
    return 'unknown', details


def select_mode(root=Path('/'), wait=5, clock=time.monotonic, sleep=time.sleep):
    root = Path(root)
    state, screen = root / STATE, root / SCREEN
    config = json.loads(state.read_text())
    template = config['template']
    if not template.startswith(MARKER):
        raise ValueError('검증된 CITY 화면 템플릿이 없습니다.')
    deadline = clock() + wait
    while True:
        mode, details = detect(root / 'sys/class/drm')
        if mode == 'physical' or clock() >= deadline:
            break
        sleep(min(0.5, max(0, deadline - clock())))
    # A selection failure must leave a previously working profile intact.
    if screen.is_symlink() or (screen.exists() and not screen.read_text().startswith(MARKER)):
        action = 'custom-preserved'
    elif mode == 'physical':
        screen.unlink(missing_ok=True)
        action = 'physical'
    elif mode == 'headless':
        atomic_write(screen, template)
        action = 'headless'
    elif screen.exists():
        action = 'unknown-preserved'
    else:
        # First boot without DRM evidence: use the hardware-validated fallback.
        atomic_write(screen, template)
        action = 'unknown-fallback'
    print(f'CITY display selection: {action}; DRM={details!r}', flush=True)
    return action


class AdaptiveHeadless:
    def __init__(self, root='/'):
        self.root = Path(root)
        self.backup = self.root / 'var/lib/jon-city-ap-remote/adaptive-backup.json'

    def paths(self, manager):
        if manager not in ('gdm.service', 'gdm3.service'):
            raise ValueError('지원하는 GDM display-manager를 확인할 수 없습니다.')
        return dict(state=self.root / STATE, helper=self.root / HELPER,
                    unit=self.root / 'etc/systemd/system' / UNIT,
                    dropin=self.root / 'etc/systemd/system' / (manager + '.d/40-city-display-select.conf'),
                    display_helper=self.root / 'usr/local/lib/jon-city-ap-remote/display_mode.py',
                    autostart=self.root / 'etc/xdg/autostart/jon-city-display.desktop')

    def install(self, setup, user, connector='DFP-1', wait=5):
        setup.validate(connector)
        alias = self.root / 'etc/systemd/system/display-manager.service'
        manager = alias.resolve(strict=True).name
        paths = self.paths(manager)
        current = snapshot(paths)
        desktop = setup.snapshot()
        # Protect unrelated user files at our service/config paths.
        for name in ('helper', 'unit', 'dropin', 'display_helper', 'autostart'):
            value = current[name]
            if value and not any(line.startswith(MARKER) for line in value['text'].splitlines()[:2]):
                raise ValueError(f'사용자 작성 파일은 덮어쓰지 않습니다: {paths[name]}')
        if self.backup.exists():
            original = json.loads(self.backup.read_text())
            if original['manager'] != manager:
                raise ValueError('GDM 서비스가 변경됐습니다. 기존 화면 선택 설정을 먼저 복원하세요.')
            # Extend old backups before installing newly introduced files.
            missing = {key: value for key, value in current.items() if key not in original['files']}
            if missing:
                original['files'].update(missing)
                atomic_write(self.backup, json.dumps(original) + '\n', 0o600)
        else:
            atomic_write(self.backup, json.dumps(dict(manager=manager, files=current)) + '\n', 0o600)
        from headless_setup import HEADLESS
        template = HEADLESS.format(connector=connector)
        try:
            # Back up and set auto-login, keeping the current screen profile until selection.
            setup.apply(user, connector, manage_screen=False)
            # Adopt only the exact legacy forced profile that this project generated.
            screen = setup.paths['screen']
            if screen.exists() and not screen.read_text().startswith(MARKER):
                normalize = lambda text: re.sub(r'\s+', '', '\n'.join(
                    line for line in text.splitlines() if not line.lstrip().startswith('#')))
                if normalize(screen.read_text()) == normalize(template):
                    atomic_write(screen, template)
            config = dict(template=template, manager=manager, connector=connector)
            atomic_write(paths['state'], json.dumps(config) + '\n', 0o600)
            atomic_write(paths['helper'], Path(__file__).read_text())
            atomic_write(paths['display_helper'], Path(__file__).with_name('display_mode.py').read_text())
            quoted_user = user.replace('$', '\\$')
            atomic_write(paths['autostart'], f'''{MARKER}display_mode.py
[Desktop Entry]
Type=Application
Name=JON-CITY 1080p Display
Exec=/usr/bin/python3 /usr/local/lib/jon-city-ap-remote/display_mode.py --user "{quoted_user}"
Terminal=false
OnlyShowIn=GNOME;
X-GNOME-Autostart-enabled=true
''')
            atomic_write(paths['unit'], f'''{MARKER}adaptive_headless.py
[Unit]
Description=Select physical or headless CITY desktop output
After=systemd-udev-trigger.service
Before={manager}
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /{HELPER} select
TimeoutStartSec=15
''')
            # A helper error is logged but must not prevent GDM starting.
            atomic_write(paths['dropin'], f'''{MARKER}adaptive_headless.py
[Unit]
Wants={UNIT}
After={UNIT}
''')
            return select_mode(self.root, wait=wait)
        except Exception:
            setup.restore_snapshot(desktop)
            restore_snapshot(paths, current)
            raise

    def remove(self):
        if not self.backup.exists():
            return
        original = json.loads(self.backup.read_text())
        paths = {key: path for key, path in self.paths(original['manager']).items()
                 if key in original['files']}
        current = snapshot(paths)
        try:
            restore_snapshot(paths, original['files'])
        except Exception:
            restore_snapshot(paths, current)
            raise

    def restore(self, setup):
        desktop = setup.snapshot()
        original = json.loads(self.backup.read_text()) if self.backup.exists() else None
        paths = {key: path for key, path in self.paths(original['manager']).items()
                 if key in original['files']} if original else {}
        current = snapshot(paths)
        try:
            self.remove()
            setup.restore()
        except Exception:
            setup.restore_snapshot(desktop)
            restore_snapshot(paths, current)
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('select',))
    parser.parse_args()
    if os.geteuid() != 0:
        parser.error('sudo 권한이 필요합니다.')
    select_mode()


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError) as error:
        print(f'CITY display selection failed: {error}', file=sys.stderr)
        sys.exit(1)
