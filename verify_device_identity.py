#!/usr/bin/env python3
"""Identity regression using disposable configs and a fake NetworkManager."""
import argparse
import json
from pathlib import Path
import stat
import tempfile
from unittest.mock import patch
import apctl
import device_config
import remote
from remote_control import RemoteController


def main():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        (root / '.state').mkdir()
        (root / 'public').mkdir()
        path = root / '.state/remote.json'
        old = dict(device_id='JON-CITY-001', location='old', display='auto',
                   connection_security={'session_seconds': 1800})
        path.write_text(json.dumps(old))
        path.chmod(0o600)
        password = root / '.state/vnc.passwd'
        password.write_bytes(b'01234567')
        public = root / 'public/device.json'
        public.write_text(json.dumps(old))
        calls = []
        def nmcli(*args):
            calls.append(args)
            return {('WIFI-PROPERTIES.AP'): 'yes', ('802-11-wireless.mode'): 'ap'}.get(args[1], '')
        with patch.object(apctl, 'CONFIG', root / 'device.json'), \
             patch.object(apctl, 'nmcli', side_effect=nmcli), \
             patch.object(apctl.getpass, 'getpass', return_value='TestOnly123'):
            apctl.configure(argparse.Namespace(device_id='JON-DEV-001', location='',
                address=None, channel=None, password_file=None, interface='wlan0'))
            assert json.loads(path.read_text()) == {**old, 'device_id': 'JON-DEV-001', 'location': ''}
            apctl.update_configuration(argparse.Namespace(device_id='JON-DEV-002', location='Field',
                address=None, channel=None, password_file=None, interface=None))
        expected = {**old, 'device_id': 'JON-DEV-002', 'location': 'Field'}
        assert json.loads(path.read_text()) == expected
        assert json.loads(public.read_text()) == {'device_id': 'JON-DEV-002', 'location': 'Field'}
        assert password.read_bytes() == b'01234567' and stat.S_IMODE(path.stat().st_mode) == 0o600
        assert not any(args[:2] == ('connection', 'up') for args in calls)
        assert json.loads(path.with_name('remote.json.before-device-id-sync').read_text()) == old
        path.write_text(json.dumps(old))
        assert RemoteController(root).settings() == expected
        assert remote.load_config(path) == expected
        before_remote, before_public = path.read_text(), public.read_text()
        write, failed = device_config._write, []
        def fail_once(target, *args, **kwargs):
            if target == public and not failed:
                failed.append(True)
                raise OSError('simulated write failure')
            return write(target, *args, **kwargs)
        # Ensure the second write needs updating, so rollback of the first is exercised.
        public.write_text('{}')
        with patch.object(device_config, '_write', side_effect=fail_once):
            try:
                device_config.sync_remote_identity(root)
            except OSError:
                pass
            else:
                raise AssertionError('Write failure ignored')
        assert path.read_text() == before_remote and public.read_text() == '{}'
        public.write_text(before_public)
        assets = root / 'assets'
        assets.mkdir()
        (assets / 'vnc.html').write_text('test')
        args = argparse.Namespace(device_id='WRONG-ID', location='wrong', max_clients=3,
            listen_address='192.168.50.1', display='auto', web_port=8080, vnc_port=5901,
            cert=None, key=None, novnc_dir=str(assets), x11vnc='/usr/bin/true',
            websockify='/usr/bin/true', xauthority='')
        with patch.object(remote, 'ROOT', root), patch.object(remote, 'STATE', root / '.state'), \
             patch.object(remote, 'BUILD', root / 'build'):
            remote.prepare(args)
            args.device_id = None
            remote.prepare(args)
        assert json.loads(path.read_text())['device_id'] == 'JON-DEV-002'
        assert json.loads(public.read_text())['device_id'] == 'JON-DEV-002'
        assert password.read_bytes() == b'01234567'
    print('PASS: AP create/update, stale settings, prepare, UI settings, metadata, backup/rollback, password preservation')


if __name__ == '__main__':
    main()
