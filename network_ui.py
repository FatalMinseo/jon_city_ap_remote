#!/usr/bin/env python3
"""Local Tk window to switch the configured Jetson radio between Wi-Fi and AP."""
import queue
import shutil
import subprocess
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from network_control import NetworkController
from remote_control import RemoteController


class NetworkWindow:
    def __init__(self, root, controller=None, remote_controller=None):
        self.root = root
        self.controller = controller or NetworkController()
        self.remote_controller = remote_controller or RemoteController()
        self.events = queue.Queue()
        self.busy = False
        self.polling = False
        self.generation = 0
        self.switch_in_progress = False
        self.profiles = []
        self.snapshot = None
        root.title('JON-CITY · Wi-Fi / AP 전환')
        root.geometry('700x740')
        root.minsize(650, 700)
        root.protocol('WM_DELETE_WINDOW', self.close)
        frame = ttk.Frame(root, padding=24)
        frame.pack(fill='both', expand=True)
        self.title = tk.StringVar(value='JON-CITY 네트워크')
        ttk.Label(frame, textvariable=self.title, font=('', 17, 'bold')).pack(anchor='w')
        self.status = tk.StringVar(value='상태 확인 중…')
        ttk.Label(frame, textvariable=self.status, wraplength=590,
                  font=('', 11)).pack(anchor='w', pady=(12, 18))
        ttk.Label(frame, text='외부 Wi-Fi', font=('', 12, 'bold')).pack(anchor='w')
        ttk.Label(frame, text='저장된 Wi-Fi를 선택해 연결합니다.').pack(anchor='w', pady=(4, 6))
        self.selection = ttk.Combobox(frame, state='readonly')
        self.selection.pack(fill='x')
        wifi_row = ttk.Frame(frame)
        wifi_row.pack(fill='x', pady=(8, 16))
        self.wifi_button = ttk.Button(wifi_row, text='선택한 Wi-Fi로 전환', command=self.to_wifi)
        self.wifi_button.pack(side='left')
        self.settings_button = ttk.Button(wifi_row, text='새 Wi-Fi 설정…', command=self.open_settings)
        self.settings_button.pack(side='left', padx=8)
        ttk.Separator(frame).pack(fill='x', pady=4)
        ttk.Label(frame, text='현장 AP / 핫스팟', font=('', 12, 'bold')).pack(anchor='w', pady=(12, 4))
        self.ap_description = tk.StringVar(value='설정된 JON-CITY Wi-Fi를 제공합니다.')
        ttk.Label(frame, textvariable=self.ap_description, wraplength=590).pack(anchor='w')
        ap_row = ttk.Frame(frame)
        ap_row.pack(fill='x', pady=(8, 12))
        self.ap_button = ttk.Button(ap_row, text='AP 켜기', command=self.to_ap)
        self.ap_button.pack(side='left')
        self.ap_off_button = ttk.Button(ap_row, text='AP 끄기', command=self.ap_off)
        self.ap_off_button.pack(side='left', padx=8)
        self.persist = tk.BooleanVar(value=True)
        ttk.Checkbutton(frame, text='선택한 모드를 부팅 후에도 유지',
                        variable=self.persist).pack(anchor='w')
        ttk.Label(frame, text='무선 모듈 하나로 전환합니다. AP를 끄면 원격 접속이 끊기며,\n'
                  'AP를 켜면 기존 외부 Wi-Fi와 인터넷 연결이 끊길 수 있습니다.',
                  foreground='#9a4d00', wraplength=590).pack(anchor='w', pady=(10, 8))
        ttk.Separator(frame).pack(fill='x', pady=8)
        ttk.Label(frame, text='원격 바탕화면 접속', font=('', 12, 'bold')).pack(anchor='w')
        self.remote_status = tk.StringVar(value='상태 확인 중…')
        ttk.Label(frame, textvariable=self.remote_status, wraplength=610).pack(anchor='w', pady=6)
        remote_row = ttk.Frame(frame)
        remote_row.pack(fill='x', pady=(0, 8))
        self.remote_button = ttk.Button(remote_row, text='원격 접속 허용(실행)…', command=self.remote_dialog)
        self.remote_button.pack(side='left')
        self.remote_stop_button = ttk.Button(remote_row, text='원격 접속 중지', command=self.stop_remote)
        self.remote_stop_button.pack(side='left', padx=8)
        bottom = ttk.Frame(frame)
        bottom.pack(fill='x', side='bottom')
        self.refresh_button = ttk.Button(bottom, text='상태·목록 새로고침', command=self.refresh)
        self.refresh_button.pack(side='left')
        self.progress = tk.StringVar(value='')
        ttk.Label(bottom, textvariable=self.progress).pack(side='left', padx=12)
        self.action_buttons = [self.wifi_button, self.ap_button, self.ap_off_button,
                               self.refresh_button, self.settings_button,
                               self.remote_button, self.remote_stop_button]
        self.root.after(100, self.poll_events)
        self.root.after(5000, self.periodic_refresh)
        self.refresh()

    def run(self, task, text, silent=False):
        if self.busy or (silent and self.polling):
            return
        if silent:
            self.polling = True
        else:
            self.busy = True
            self.generation += 1
            self.progress.set(text)
            for button in self.action_buttons:
                button.state(['disabled'])
            self.selection.configure(state='disabled')
        generation = self.generation

        def worker():
            try:
                self.events.put((silent, generation, 'ok', task()))
            except Exception as error:
                self.events.put((silent, generation, 'error', str(error)))

        threading.Thread(target=worker, daemon=True).start()

    def refresh(self):
        self.run(lambda: self.read_state(profiles=True), '연결 상태 확인 중…')

    def read_state(self, profiles=False):
        result = dict(remote_status=self.remote_controller.status())
        try:
            result['snapshot'] = self.controller.state()
            result['profiles'] = self.controller.wifi_profiles() if profiles else None
        except Exception as error:
            result['network_error'] = str(error)
        return result

    def periodic_refresh(self):
        if not self.busy:
            self.run(self.read_state, '상태 확인 중…', silent=True)
        self.root.after(5000, self.periodic_refresh)

    def display(self, snapshot, profiles=None):
        self.snapshot = snapshot
        config = snapshot['config']
        self.set_changed(self.title, config['device_id'] + ' · Wi-Fi / AP 전환')
        mode = {'ap': 'AP / 핫스팟 연결 중', 'wifi': '외부 Wi-Fi 연결 중',
                'disconnected': '무선 연결 없음'}[snapshot['mode']]
        address = ', '.join(snapshot['addresses']) or '없음'
        self.set_changed(self.status, f"{mode}\n연결: {snapshot['connection']} · IP: {address}")
        remote = self.controller.config_path.parent / '.state/remote.json'
        scheme, port = 'http', config.get('web_port', 8080)
        if remote.exists():
            import json
            settings = json.loads(remote.read_text())
            scheme = 'https' if settings.get('cert') else 'http'
            port = settings['web_port']
        ip = config['address'].split('/')[0]
        hostname = settings.get('hostname', ip) if remote.exists() else ip
        authority = hostname if (scheme, port) in (('http', 80), ('https', 443)) else f'{hostname}:{port}'
        self.set_changed(self.ap_description, f"Wi-Fi 이름: {config['device_id']}\n원격 접속: {scheme}://{authority}")
        if profiles is not None:
            old_uuid = (self.profiles[self.selection.current()]['uuid']
                        if 0 <= self.selection.current() < len(self.profiles) else '')
            self.profiles = profiles
            self.selection['values'] = [f"{item['name']}  ({item['uuid'][:8]})" for item in profiles]
            wanted = old_uuid or (snapshot['uuid'] if snapshot['mode'] == 'wifi'
                                  else self.controller.preferred_wifi())
            if wanted in ('', '--'):
                wanted = self.controller.preferred_wifi()
            index = next((i for i, item in enumerate(profiles) if item['uuid'] == wanted), 0)
            if profiles:
                self.selection.current(index)
            else:
                self.selection.set('저장된 Wi-Fi 없음 — 새 Wi-Fi 설정을 여세요')

    @staticmethod
    def set_changed(variable, value):
        if variable.get() != value:
            variable.set(value)

    def poll_events(self):
        try:
            silent, generation, outcome, result = self.events.get_nowait()
        except queue.Empty:
            pass
        else:
            if silent:
                self.polling = False
            else:
                self.busy = False
                self.switch_in_progress = False
                for button in self.action_buttons:
                    button.state(['!disabled'])
                self.selection.configure(state='readonly')
            if generation != self.generation or (silent and self.busy):
                # A read started before a user action must not replace newer state.
                self.root.after(100, self.poll_events)
                return
            if outcome == 'ok':
                self.set_changed(self.progress, '')
                try:
                    if isinstance(result, dict):
                        self.set_changed(self.remote_status, result['remote_status'])
                        if 'network_error' in result:
                            self.set_changed(self.status, result['network_error'])
                        else:
                            self.display(result['snapshot'], result['profiles'])
                    else:
                        self.display(*result)
                except Exception as error:
                    self.progress.set('표시 실패')
                    if not silent:
                        messagebox.showerror('상태 확인', str(error), parent=self.root)
            else:
                self.progress.set('오류 — 새로고침으로 상태 확인')
                # Don't repeat modal errors every five seconds.
                if not silent:
                    messagebox.showerror('작업 실패', result, parent=self.root)
        self.root.after(100, self.poll_events)

    def switch(self, mode, identity=None):
        if self.busy:
            return
        self.switch_in_progress = True
        persist = self.persist.get()
        self.run(lambda: (self.controller.switch(mode, identity, persist),
                          self.controller.wifi_profiles()), '전환 중… 인증 창이 나오면 확인하세요')

    def to_ap(self):
        if messagebox.askokcancel('AP로 전환', '외부 Wi-Fi 연결이 끊길 수 있습니다.\nAP로 전환할까요?', parent=self.root):
            self.switch('ap')

    def to_wifi(self):
        index = self.selection.current()
        if not 0 <= index < len(self.profiles):
            messagebox.showinfo('Wi-Fi 선택', '먼저 저장된 Wi-Fi를 선택하세요.', parent=self.root)
            return
        if messagebox.askokcancel('Wi-Fi로 전환', 'AP가 꺼지면 현재 원격 접속이 끊깁니다.\n'
                                  '로컬 또는 유선으로 복구할 수 있는 상태에서 전환하세요.', parent=self.root):
            self.switch('wifi', self.profiles[index]['uuid'])

    def ap_off(self):
        if messagebox.askokcancel('AP 끄기', '원격 접속이 끊깁니다. 외부 Wi-Fi에 자동 연결하지는 않습니다.\n'
                                  'AP를 끌까요?', parent=self.root):
            self.switch('off')

    def open_settings(self):
        for command in (['gnome-control-center', 'wifi'], ['nm-connection-editor']):
            if shutil.which(command[0]):
                subprocess.Popen(command)
                return
        messagebox.showinfo('Wi-Fi 설정', '시스템 네트워크 설정에서 Wi-Fi를 추가한 뒤 새로고침하세요.', parent=self.root)

    def remote_dialog(self):
        if self.busy:
            return
        try:
            settings = self.remote_controller.settings()
        except Exception as error:
            messagebox.showerror('원격 접속 설정', str(error), parent=self.root)
            return
        dialog = tk.Toplevel(self.root)
        dialog.title('원격 접속 설정 및 실행')
        dialog.transient(self.root)
        dialog.resizable(False, False)
        panel = ttk.Frame(dialog, padding=20)
        panel.pack(fill='both', expand=True)
        fields = {}
        identity_locked = (self.remote_controller.root / 'device.json').is_file()
        for row, (key, label) in enumerate((
                ('device_id', '장비 번호'), ('location', '설치 위치'),
                ('listen_address', 'AP 접속 IP'), ('web_port', '웹 포트'),
                ('display', '바탕화면 선택'), ('max_clients', '동시 접속 수 (1~3)'),
                ('password', '원격 접속 비밀번호'), ('confirmation', '비밀번호 확인'))):
            ttk.Label(panel, text=label).grid(row=row, column=0, sticky='w', pady=5, padx=(0, 16))
            variable = tk.StringVar(value='auto' if key == 'display' else settings.get(key, ''))
            entry = ttk.Entry(panel, textvariable=variable, width=32,
                              state='readonly' if key == 'display' or
                                    (identity_locked and key in ('device_id', 'location')) else 'normal',
                              show='•' if key in ('password', 'confirmation') else '')
            entry.grid(row=row, column=1, sticky='ew', pady=5)
            fields[key] = variable
        ttk.Label(panel, text='비밀번호: 영문·숫자·기호 8~63자, 공백 제외. Wi-Fi 비밀번호와 별개입니다.\n'
                  '장비 번호·설치 위치는 AP 설정과 동일하게 사용합니다.\n'
                  'VNC 인증은 앞 8자만 사용합니다. 뒤에 붙인 문자는 인증에 반영되지 않습니다.\n'
                  '저장된 비밀번호를 유지하려면 두 비밀번호 칸을 비워두세요.\n'
                  '바탕화면은 자동 선택합니다. :0·:1 번호를 지정할 필요가 없습니다.',
                  wraplength=490).grid(row=8, column=0, columnspan=2, sticky='w', pady=10)
        autostart = tk.BooleanVar(value=False)
        ttk.Checkbutton(panel, text='재부팅 후에도 원격 접속 허용', variable=autostart).grid(
            row=9, column=0, columnspan=2, sticky='w')
        ttk.Label(panel, text='최초 무모니터 출력 설정은 재부팅 후 적용됩니다.\n'
                  '실행 시 서비스 설치와 관리자 인증이 필요할 수 있습니다.\n'
                  '접속할 기기는 위 AP에 연결해야 합니다.', wraplength=490).grid(
            row=10, column=0, columnspan=2, sticky='w', pady=10)

        def enable():
            values = {key: value.get() for key, value in fields.items()}
            password, confirmation = values.pop('password'), values.pop('confirmation')
            try:
                from pathlib import Path
                saved = Path(settings.get('password_file', self.remote_controller.config_path.parent / 'vnc.passwd'))
                self.remote_controller.validate_password(password, confirmation, saved.is_file())
                values['web_port'] = int(values['web_port'])
                values['max_clients'] = int(values['max_clients'])
            except ValueError as error:
                messagebox.showerror('입력 확인', str(error), parent=dialog)
                return
            boot = autostart.get()
            fields['password'].set('')
            fields['confirmation'].set('')
            dialog.destroy()
            self.start_remote(values, password, confirmation, boot)

        buttons = ttk.Frame(panel)
        buttons.grid(row=11, column=0, columnspan=2, sticky='e')
        ttk.Button(buttons, text='취소', command=dialog.destroy).pack(side='left', padx=8)
        ttk.Button(buttons, text='설정 후 원격 접속 실행', command=enable).pack(side='left')
        dialog.grab_set()

    def start_remote(self, settings, password, confirmation, autostart):
        if self.busy:
            return
        self.switch_in_progress = True
        def task():
            self.remote_controller.enable(settings, password, confirmation, autostart)
            return self.read_state()
        self.run(task, '원격 접속 준비 중… 인증 창이 나오면 확인하세요')

    def stop_remote(self):
        if self.busy:
            return
        if not messagebox.askokcancel('원격 접속 중지',
                                     '현재 원격 연결이 종료되고 부팅 자동 실행도 해제됩니다.\n중지할까요?',
                                     parent=self.root):
            return
        self.switch_in_progress = True
        def task():
            self.remote_controller.stop()
            return self.read_state()
        self.run(task, '원격 접속 중지 중…')

    def close(self):
        if self.switch_in_progress:
            messagebox.showinfo('작업 진행 중', '진행 중인 작업이 끝난 뒤 창을 닫아주세요.', parent=self.root)
            return
        self.root.destroy()


def main():
    root = tk.Tk()
    NetworkWindow(root)
    root.mainloop()


if __name__ == '__main__':
    main()
