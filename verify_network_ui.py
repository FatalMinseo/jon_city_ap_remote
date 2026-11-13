#!/usr/bin/env python3
"""Preview/test the local window with a fake radio; never changes networking."""
import argparse
import json
from pathlib import Path
import tempfile
import threading
import time
import tkinter as tk
from unittest.mock import patch

from network_ui import NetworkWindow
from remote_control import RemoteController

WIFI = '11111111-1111-4111-8111-111111111111'
AP = '22222222-2222-4222-8222-222222222222'


class PreviewController:
    def __init__(self, path):
        self.config_path = path
        self.mode = 'wifi'

    def state(self):
        return dict(config=dict(device_id='JON-CITY-001', interface='wlan0',
                                profile='jon-city-ap-JON-CITY-001',
                                address='192.168.50.1/24', web_port=8080),
                    mode=self.mode, uuid=WIFI if self.mode == 'wifi' else AP,
                    connection='미리보기 Wi-Fi' if self.mode == 'wifi' else 'JON-CITY-001',
                    addresses=['192.168.0.25/24'] if self.mode == 'wifi' else ['192.168.50.1/24'])

    def wifi_profiles(self):
        return [dict(uuid=WIFI, name='미리보기 Wi-Fi')]

    def preferred_wifi(self):
        return WIFI

    def switch(self, mode, identity=None, persist=True):
        self.mode = 'disconnected' if mode == 'off' else mode
        return self.state()


class PreviewRemoteController(RemoteController):
    def __init__(self, root):
        super().__init__(root)
        self.enabled = False

    def status(self):
        return '허용 중 · 미리보기' if self.enabled else '중지됨'

    def enable(self, settings, password='', confirmation='', autostart=False):
        self.validate_password(password, confirmation, False)
        self.enabled = True
        self.received = settings, autostart
        return self.status()

    def stop(self):
        self.enabled = False
        return self.status()


def pump(root, condition):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        root.update()
        if condition():
            return
        time.sleep(0.02)
    raise AssertionError('UI operation did not complete')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--screenshot')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as directory:
        root = tk.Tk()
        app = NetworkWindow(root, PreviewController(Path(directory) / 'device.json'),
                            PreviewRemoteController(Path(directory)))
        try:
            pump(root, lambda: not app.busy)
            assert '외부 Wi-Fi' in app.status.get()
            # The device was configured after remote settings were first prepared.
            (Path(directory) / 'device.json').write_text(json.dumps(
                dict(device_id='JON-DEV-001', location='Field')))
            app.remote_button.invoke()
            dialog = next(child for child in root.winfo_children() if isinstance(child, tk.Toplevel))
            panel = dialog.winfo_children()[0]
            entries = [child for child in panel.winfo_children() if child.winfo_class() == 'TEntry']
            assert len(entries) == 8
            assert entries[0].get() == 'JON-DEV-001'
            assert str(entries[0].cget('state')) == str(entries[1].cget('state')) == 'readonly'
            assert entries[4].get() == 'auto' and str(entries[4].cget('state')) == 'readonly'
            entries[6].insert(0, 'Demo1234!long')
            entries[7].insert(0, 'different')
            submit = panel.winfo_children()[-1].winfo_children()[-1]
            with patch('network_ui.messagebox.showerror') as error:
                submit.invoke()
                error.assert_called_once()
                assert not app.remote_controller.enabled
            entries[7].delete(0, 'end')
            entries[7].insert(0, 'Demo1234!long')
            submit.invoke()
            pump(root, lambda: not app.busy)
            assert '허용 중' in app.remote_status.get()
            assert app.remote_controller.received[0]['web_port'] == 8080
            assert app.remote_controller.received[0]['display'] == 'auto'
            with patch('network_ui.messagebox.askokcancel', return_value=True):
                app.stop_remote()
                pump(root, lambda: not app.busy)
            assert app.remote_status.get() == '중지됨'
            with patch('network_ui.messagebox.askokcancel', return_value=True):
                app.to_ap()
                pump(root, lambda: not app.busy)
                assert 'AP / 핫스팟' in app.status.get()
                app.to_wifi()
                pump(root, lambda: not app.busy)
                assert '외부 Wi-Fi' in app.status.get()
                app.to_ap()
                pump(root, lambda: not app.busy)
                app.ap_off()
                pump(root, lambda: not app.busy)
                assert '무선 연결 없음' in app.status.get()
                app.to_ap()
                pump(root, lambda: not app.busy)
            # Unchanged periodic reads must not flash text or disable controls.
            writes = []
            variables = [app.title, app.status, app.ap_description, app.progress, app.remote_status]
            traces = [variable.trace_add('write', lambda *args: writes.append(True))
                      for variable in variables]
            states = [button.state() for button in app.action_buttons]
            app.periodic_refresh()
            assert [button.state() for button in app.action_buttons] == states
            assert str(app.selection.cget('state')) == 'readonly'
            pump(root, lambda: not app.polling)
            assert not writes, 'Unchanged periodic read rewrote visible text'
            for variable, trace in zip(variables, traces):
                variable.trace_remove('write', trace)
            # A slow background read cannot block a click or overwrite its result.
            old_snapshot = app.controller.state()
            release = threading.Event()
            def delayed_poll():
                release.wait(timeout=5)
                return old_snapshot, None
            app.run(delayed_poll, '', silent=True)
            with patch('network_ui.messagebox.askokcancel', return_value=True):
                app.to_wifi()
                pump(root, lambda: not app.busy)
            assert '외부 Wi-Fi' in app.status.get()
            release.set()
            pump(root, lambda: not app.polling)
            assert '외부 Wi-Fi' in app.status.get(), 'Stale poll overwrote user action'
            if args.screenshot:
                from PIL import ImageGrab
                root.lift()
                root.attributes('-topmost', True)
                for _ in range(25):
                    root.update()
                    time.sleep(0.04)
                x, y = root.winfo_rootx(), root.winfo_rooty()
                ImageGrab.grab(bbox=(x, y, x + root.winfo_width(), y + root.winfo_height())).save(args.screenshot)
            print('Local UI transitions and AP off: PASS')
            print('Remote enable/stop with fake services: PASS')
            print('Silent polling preserves controls/text and discards stale reads: PASS')
        finally:
            root.destroy()


if __name__ == '__main__':
    main()
