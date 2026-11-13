#!/usr/bin/env python3
"""Render/apply an isolated AP firewall table without replacing host firewall rules."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
PLAN = ROOT / '.state/firewall.json'


def render(config):
    interface = config['interface']
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,15}', interface):
        raise ValueError('잘못된 네트워크 인터페이스 이름')
    address = str(ipaddress.IPv4Address(config['address']))
    port = int(config['web_port'])
    if not 1024 <= port <= 65535:
        raise ValueError('잘못된 웹 포트')
    ssh = ''
    if config.get('ssh_from'):
        network = ipaddress.IPv4Network(config['ssh_from'], strict=False)
        if network.prefixlen < 24:
            raise ValueError('SSH 허용 범위는 /24 이하의 작은 네트워크로 지정하세요.')
        ssh = f'    iifname "{interface}" ip saddr {network} ip daddr {address} tcp dport 22 accept\n'
    return f'''table inet jon_city_ap {{
  chain input {{
    type filter hook input priority -10; policy accept;
    iifname "{interface}" ct state established,related accept
    iifname "{interface}" udp sport 68 udp dport 67 accept
    iifname "{interface}" ip daddr {address} udp dport 53 accept
    iifname "{interface}" ip daddr {address} tcp dport 53 accept
    iifname "{interface}" ip daddr {address} tcp dport {port} accept
{ssh}    iifname "{interface}" counter drop
  }}
  chain forward {{
    type filter hook forward priority -10; policy accept;
    iifname "{interface}" counter drop
    oifname "{interface}" counter drop
  }}
}}
'''


def nft(*arguments, **kwargs):
    return subprocess.run(['nft', *arguments], text=True, capture_output=True, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    plan = commands.add_parser('plan', help='규칙을 출력/저장; 실제 방화벽은 변경하지 않음')
    plan.add_argument('--interface', required=True)
    plan.add_argument('--address', default='192.168.50.1')
    plan.add_argument('--web-port', type=int, default=8080)
    plan.add_argument('--ssh-from', help='선택: AP에서 SSH를 허용할 관리 IP/범위')
    commands.add_parser('apply', help='준비된 규칙 적용; 로컬/유선 터미널에서 실행')
    commands.add_parser('remove', help='이 프로그램의 AP 제한 테이블만 제거')
    args = parser.parse_args()
    if args.command == 'plan':
        config = dict(interface=args.interface, address=args.address,
                      web_port=args.web_port, ssh_from=args.ssh_from)
        rules = render(config)
        PLAN.parent.mkdir(exist_ok=True, mode=0o700)
        PLAN.write_text(json.dumps(config, indent=2) + '\n')
        PLAN.chmod(0o600)
        (PLAN.parent / 'ap-firewall.nft').write_text(rules)
        print(rules)
        print('계획만 저장했습니다. AP/SSH/방화벽은 아직 변경하지 않았습니다.')
        return
    if os.geteuid() != 0:
        raise ValueError('방화벽 적용/제거는 sudo로 실행하세요.')
    if args.command == 'remove':
        result = nft('delete', 'table', 'inet', 'jon_city_ap')
        if result.returncode:
            raise RuntimeError(result.stderr.strip())
        print('AP 방화벽 테이블 제거 완료')
        return
    config = json.loads(PLAN.read_text())
    if not (Path('/sys/class/net') / config['interface']).exists():
        raise ValueError('계획에 지정한 인터페이스가 없습니다.')
    rules = render(config)
    exists = nft('list', 'table', 'inet', 'jon_city_ap')
    # Delete + replace only our table in one nft transaction, never flush ruleset.
    transaction = ('delete table inet jon_city_ap\n' if exists.returncode == 0 else '') + rules
    checked = nft('-c', '-f', '-', input=transaction)
    if checked.returncode:
        raise RuntimeError(checked.stderr.strip())
    applied = nft('-f', '-', input=transaction)
    if applied.returncode:
        raise RuntimeError(applied.stderr.strip())
    print('AP 방화벽 적용 완료. 기존 연결은 유지하며 새 연결에는 제한이 적용됩니다.')
    print('이 명령은 부팅 자동 적용을 등록하지 않습니다.')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
