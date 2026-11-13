"""Validation shared by AP and remote desktop configuration."""
import json
import os
from pathlib import Path
import stat
import tempfile


def validate_device_id(value):
    # The device name is also used as the Wi-Fi SSID.
    if (not value.strip() or not value.isprintable()
            or len(value.encode('utf-8')) > 32):
        raise ValueError('장비 이름은 UTF-8 기준 1~32바이트로 지정하세요. '
                         '공백만 입력하거나 줄바꿈·제어문자를 사용할 수 없습니다.')


def device_identity(config, device_path):
    """The AP's device.json owns the identity; remote settings are projections."""
    path = Path(device_path)
    if not path.exists():
        return dict(config)
    device = json.loads(path.read_text())
    validate_device_id(device['device_id'])
    return {**config, 'device_id': device['device_id'], 'location': device.get('location', '')}


def _write(path, text, mode, owner):
    fd, temporary = tempfile.mkstemp(prefix='.device-sync-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as output:
            output.write(text)
            os.fchmod(output.fileno(), mode)
            if os.geteuid() == 0:
                os.fchown(output.fileno(), *owner)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def sync_remote_identity(root):
    """Refresh generated identity fields without preparing units or touching passwords."""
    root = Path(root)
    remote = root / '.state/remote.json'
    if not remote.exists() or not (root / 'device.json').exists():
        return
    config = json.loads(remote.read_text())
    updated = device_identity(config, root / 'device.json')
    public = root / 'public/device.json'
    public.parent.mkdir(exist_ok=True)
    values = {remote: updated, public: {key: updated[key] for key in ('device_id', 'location')}}
    before = {}
    for path in values:
        if path.is_symlink():
            raise ValueError(f'심볼릭 링크 설정은 자동 갱신하지 않습니다: {path}')
        info = path.stat() if path.exists() else path.parent.stat()
        before[path] = (path.read_text() if path.exists() else None,
                        stat.S_IMODE(info.st_mode) if path.exists() else 0o644,
                        (info.st_uid, info.st_gid))
    backup = remote.with_name('remote.json.before-device-id-sync')
    if not backup.exists():
        old, _, owner = before[remote]
        _write(backup, old, 0o600, owner)
    changed = []
    try:
        for path, value in values.items():
            text = json.dumps(value, ensure_ascii=False, indent=2) + '\n'
            old, mode, owner = before[path]
            if text != old:
                _write(path, text, mode, owner)
                changed.append(path)
    except Exception:
        for path in reversed(changed):
            old, mode, owner = before[path]
            if old is None:
                path.unlink(missing_ok=True)
            else:
                _write(path, old, mode, owner)
        raise
