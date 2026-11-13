#!/usr/bin/env python3
# Managed by jon_city_ap_remote/display_mode.py
"""Apply supported 1080p after the configured desktop user's X11 login."""
import argparse
import os
from pathlib import Path
import pwd
import re
import subprocess
import sys
import time


def select_mode(text):
    outputs, output = {}, None
    for line in text.splitlines():
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
    if output and outputs[output]:
        return output, outputs[output][0]
    return None


def apply(wait=30, run=None, clock=time.monotonic, sleep=time.sleep):
    run = run or subprocess.run
    env = {**os.environ, 'LC_ALL': 'C.UTF-8'}
    if not re.fullmatch(r':\d+(?:\.\d+)?', env.get('DISPLAY', '')):
        raise RuntimeError('1080p 적용에는 로그인한 X11 세션의 DISPLAY가 필요합니다.')
    if not env.get('XAUTHORITY'):
        for path in (Path(f'/run/user/{os.getuid()}/gdm/Xauthority'), Path.home() / '.Xauthority'):
            if path.is_file() and os.access(path, os.R_OK):
                env['XAUTHORITY'] = str(path)
                break
    deadline = clock() + wait
    reason = '지원하는 1920×1080·60Hz 출력이 없습니다.'
    while True:
        try:
            query = run(['/usr/bin/xrandr', '--query'], env=env, capture_output=True,
                        text=True, timeout=3)
            target = select_mode(query.stdout) if query.returncode == 0 else None
            if target:
                output, mode = target
                result = run(['/usr/bin/xrandr', '--output', output, '--mode', mode, '--rate', '60'],
                             env=env, capture_output=True, text=True, timeout=3)
                if result.returncode == 0:
                    print(f'CITY login display: {output} {mode} 60Hz', flush=True)
                    return
                reason = (result.stderr or reason).strip()[:320]
            elif query.returncode:
                reason = (query.stderr or reason).strip()[:320]
        except (OSError, subprocess.TimeoutExpired) as error:
            reason = str(error)
        if clock() >= deadline:
            raise RuntimeError(reason)
        sleep(min(2, max(0, deadline - clock())))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--user', required=True)
    args = parser.parse_args()
    if pwd.getpwnam(args.user).pw_uid != os.getuid():
        return
    apply()


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, RuntimeError, KeyError) as error:
        print(f'CITY login display failed: {error}', file=sys.stderr)
        sys.exit(1)
