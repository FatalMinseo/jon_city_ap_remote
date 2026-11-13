#!/usr/bin/env python3
"""Configure a NetworkManager AP without activating it implicitly."""
import argparse
import getpass
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from device_config import validate_device_id, sync_remote_identity

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "device.json"


def nmcli(*args):
    result = subprocess.run(
        ["nmcli", *args], text=True, capture_output=True, check=False,
        env={**os.environ, "LC_ALL": "C"},
    )
    if result.returncode:
        # NetworkManager errors may include command arguments; don't print secrets.
        raise RuntimeError("NetworkManager 작업 실패. 권한과 무선 장치 상태를 확인하세요.")
    return result.stdout.strip()


def validate(device_id, address, channel, password):
    validate_device_id(device_id)
    network = ipaddress.IPv4Interface(address)
    private_ranges = [ipaddress.IPv4Network(block) for block in
                      ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")]
    if not any(network.ip in block for block in private_ranges) or network.network.prefixlen != 24:
        raise ValueError("AP 주소는 사설 IPv4 /24 주소를 사용하세요.")
    if network.ip in (network.network.network_address, network.network.broadcast_address):
        raise ValueError("AP에는 네트워크/브로드캐스트 주소를 사용할 수 없습니다.")
    if channel not in range(1, 12):
        raise ValueError("2.4GHz 채널은 1~11을 사용하세요.")
    if not 8 <= len(password) <= 63 or not password.isascii() or not password.isprintable():
        raise ValueError("Wi-Fi 비밀번호는 출력 가능한 ASCII 8~63자로 지정하세요.")


def detect_interface():
    devices = nmcli("-t", "-f", "DEVICE,TYPE", "device", "status")
    wifi = [line.rsplit(":", 1)[0] for line in devices.splitlines()
            if line.endswith(":wifi")]
    if len(wifi) != 1:
        raise ValueError("무선 장치를 자동 선택할 수 없습니다. --interface로 지정하세요.")
    return wifi[0]


def configure(args):
    if getattr(args, 'update', False):
        update_configuration(args)
        return
    if CONFIG.exists():
        raise ValueError("device.json이 이미 있습니다. 이름·위치를 수정하려면 --update를 사용하세요.")
    validate_device_id(args.device_id)
    args.address = args.address or '192.168.50.1/24'
    args.channel = 1 if args.channel is None else args.channel
    args.location = args.location or ''
    password = (Path(args.password_file).read_text().rstrip("\r\n")
                if args.password_file else getpass.getpass("Wi-Fi 비밀번호: "))
    validate(args.device_id, args.address, args.channel, password)
    interface = args.interface or detect_interface()
    support = nmcli("-g", "WIFI-PROPERTIES.AP", "device", "show", interface)
    if support.lower() != "yes":
        raise ValueError("선택한 장치의 AP 지원을 확인할 수 없습니다.")
    profile = "jon-city-ap-" + args.device_id
    existing = nmcli("-g", "NAME", "connection", "show").splitlines()
    if profile in existing:
        raise ValueError("동일한 AP 프로필이 이미 있습니다. 중복 생성하지 않습니다.")
    previous = nmcli("-g", "GENERAL.CON-UUID", "device", "show", interface)
    nmcli("connection", "add", "type", "wifi", "ifname", interface,
          "con-name", profile, "autoconnect", "no", "ssid", args.device_id)
    try:
        nmcli("connection", "modify", profile,
              "802-11-wireless.mode", "ap", "802-11-wireless.band", "bg",
              "802-11-wireless.channel", str(args.channel),
              "802-11-wireless-security.key-mgmt", "wpa-psk",
              "802-11-wireless-security.proto", "rsn",
              "802-11-wireless-security.psk", password,
              "ipv4.method", "shared", "ipv4.addresses", args.address,
              "ipv6.method", "disabled")
        config = dict(device_id=args.device_id, location=args.location,
                      interface=interface, profile=profile, address=args.address,
                      channel=args.channel, previous_connection_uuid=previous,
                      web_port=8080)
        # Password stays in NetworkManager's system connection profile.
        fd = os.open(CONFIG, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        with os.fdopen(fd, "w") as output:
            json.dump(config, output, ensure_ascii=False, indent=2)
            output.write("\n")
    except Exception:
        nmcli("connection", "delete", profile)
        raise
    sync_identity()
    print(f"{args.device_id} AP 프로필 생성 완료. 아직 활성화하지 않았습니다.")


def update_configuration(args):
    if not CONFIG.exists():
        raise ValueError('기존 device.json이 없습니다. --update 없이 최초 설정하세요.')
    validate_device_id(args.device_id)
    if any(getattr(args, key, None) is not None
           for key in ('interface', 'address', 'channel', 'password_file')):
        raise ValueError('--update는 장비 이름과 위치를 수정합니다. '
                         '인터페이스·주소·채널·비밀번호 옵션은 함께 사용할 수 없습니다.')
    original = json.loads(CONFIG.read_text())
    profile = original['profile']
    mode = nmcli('-g', '802-11-wireless.mode', 'connection', 'show', 'id', profile)
    if mode != 'ap':
        raise ValueError('기존 AP 프로필을 확인할 수 없습니다. NetworkManager 설정을 확인하세요.')
    updated = {**original, 'device_id': args.device_id}
    if args.location is not None:
        updated['location'] = args.location
    changed = args.device_id != original['device_id']
    # Keep the same profile, credentials, autoconnect settings and restore UUID.
    # Do not reactivate it: that would disconnect clients of a running AP.
    if changed:
        nmcli('connection', 'modify', 'id', profile, '802-11-wireless.ssid', args.device_id)
    temporary = None
    try:
        fd, name = tempfile.mkstemp(prefix='.device-', suffix='.json', dir=CONFIG.parent)
        temporary = Path(name)
        with os.fdopen(fd, 'w') as output:
            json.dump(updated, output, ensure_ascii=False, indent=2)
            output.write('\n')
            os.fchmod(output.fileno(), CONFIG.stat().st_mode & 0o777)
        os.replace(temporary, CONFIG)
    except Exception:
        if changed:
            try:
                nmcli('connection', 'modify', 'id', profile,
                      '802-11-wireless.ssid', original['device_id'])
            except Exception as rollback_error:
                raise RuntimeError('설정 파일 저장과 AP 이름 복구에 실패했습니다. '
                                   'device.json과 NetworkManager의 SSID를 확인하세요.') from rollback_error
        raise
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    sync_identity()
    print(f"{args.device_id} AP 설정 수정 완료. Wi-Fi 비밀번호와 기존 프로필은 유지했습니다.")
    print('새 Wi-Fi 이름은 다음 AP 연결 시 적용됩니다. 로컬/유선에서 AP 끄기 → 켜기를 하세요.')
    print('원격 화면과 UI의 장비 이름·위치도 같은 설정을 사용합니다.')


def sync_identity():
    try:
        sync_remote_identity(CONFIG.parent)
    except (OSError, ValueError) as error:
        raise RuntimeError('AP 설정은 저장됐지만 원격 표시 동기화에 실패했습니다: ' + str(error)) from error


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    setup = commands.add_parser("configure", help="AP 프로필만 생성; 현재 연결 유지")
    setup.add_argument("--device-id", required=True)
    setup.add_argument("--location")
    setup.add_argument("--interface")
    setup.add_argument("--address")
    setup.add_argument("--channel", type=int)
    setup.add_argument("--password-file")
    setup.add_argument('--update', action='store_true', help='기존 AP의 이름·위치 수정; 비밀번호 유지')
    commands.add_parser("status")
    commands.add_parser("up", help="AP 활성화; 기존 Wi-Fi 연결이 끊길 수 있음")
    commands.add_parser("down")
    commands.add_parser("restore", help="AP를 끄고 구성 당시 Wi-Fi 연결 복구")
    boot = commands.add_parser("autostart")
    boot.add_argument("state", choices=["on", "off"])
    args = parser.parse_args()
    if args.command == "configure":
        configure(args)
        return
    if args.command == "status":
        print(nmcli("-f", "DEVICE,TYPE,STATE,CONNECTION", "device", "status"))
        if CONFIG.exists():
            print(CONFIG.read_text())
        return
    config = json.loads(CONFIG.read_text())
    profile = config["profile"]
    if args.command == "up":
        nmcli("connection", "up", profile)
    elif args.command == "down":
        nmcli("connection", "down", profile)
    elif args.command == "restore":
        uuid = config.get("previous_connection_uuid", "")
        if not uuid or uuid == "--":
            raise ValueError("복구할 이전 연결 UUID가 없습니다.")
        nmcli("connection", "modify", profile, "connection.autoconnect", "no")
        nmcli("connection", "up", "uuid", uuid, "ifname", config["interface"])
    elif args.command == "autostart":
        nmcli("connection", "modify", profile,
              "connection.autoconnect", "yes" if args.state == "on" else "no",
              "connection.autoconnect-priority", "100")
    print("완료")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, OSError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
