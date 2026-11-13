# JON-CITY AP 원격 바탕화면

## 포트폴리오 공개 범위

Jetson의 Wi-Fi AP 구성과 브라우저 원격 바탕화면, 무모니터 X11 운영 구현을
소개하기 위한 개인 포트폴리오 공개본입니다. AP 코드는 JON-CITY 버전입니다.

이 저장소에는 회사 운영 모델·학습 가중치와 실제 장비의 접속 비밀번호·생성 설정·
로그를 포함하지 않습니다. AI 모델은 AP 기능과 별도로 준비해야 하며,
참고용 공개 YOLO11n 모델은 회사에서 실제 사용하는 운영 모델이 아닙니다.
기존 회사 Git 이력을 가져오지 않고 개인 공개본부터 이력을 새로 관리합니다.

아이콘 설치를 포함한 최초 설치 전체 순서는 [INSTALL.md](INSTALL.md)를 참고하세요.

Jetson의 실제 X11 바탕화면을 맥북/Windows 브라우저에서 보고 조작합니다.
기존 `/home/jetson/jetson_ai_modbus_rtu` 프로젝트는 수정하지 않습니다.

접속 시도 제한·연결 만료·Origin 검사와 AP 방화벽 계획은 [SECURITY.md](SECURITY.md)를
참고하세요. 기본 HTTP와 VNC 인증만으로 보안 구성이 완료된 것은 아닙니다.

외부 Wi-Fi/AP 전환은 `python3 network_ui.py`로 여는 로컬 창에서도 가능합니다.
바탕화면 아이콘과 사용법은 [NETWORK_UI.md](NETWORK_UI.md)를 참고하세요.

## 구성과 메모리

```text
노트북 → JON-CITY-001 AP → http://192.168.50.1:8080
                               ↓ noVNC
                      websockify (접속 시 실행)
                               ↓ localhost
                       x11vnc (연결 시 실행)
                               ↓
                       기존 X11 바탕화면
```

평소에는 NetworkManager AP와 systemd의 대기 소켓만 유지합니다.
페이지를 열면 websockify가 실행되고, 바탕화면에 연결하면 x11vnc가 실행됩니다.
연결 종료 후 x11vnc는 종료됩니다. websockify도 활성 연결이 없으면 약 30초 후
종료됩니다. 다음 접속은 systemd가 다시 처리합니다. VNC 동시 접속은 기본 3명입니다.
`prepare --max-clients 1`처럼 1~3으로 제한할 수 있습니다. 기기를 등록/기억하지
않으며 활성 연결 수만 제한합니다. 연결마다 별도 화면 전송 프로세스를 사용하므로
3명 접속 시 메모리와 CPU 사용량이 늘어납니다. 같은 바탕화면을 함께 조작합니다.
정상/실패한 세션은 종료 후 systemd에서 수거합니다. 연결 기록 파일은 생성하지
않습니다. 장애 확인용 journal 로그는 운영체제의 보존 정책을 따릅니다.
갑작스러운 Wi-Fi 단절에는 TCP keepalive로 죽은 연결을 정리합니다. 정상 종료보다
늦게 정리될 수 있으며 실제 네트워크 단절 시 동작도 현장에서 확인해야 합니다.

별도 가상 바탕화면이나 Jetson 측 Chromium, Flask/Gunicorn은 추가하지 않습니다.
기존 바탕화면·AI 추론·카메라 GUI의 메모리는 별개입니다. 원격에서 실행한 GUI는
직접 닫아야 합니다. 연결 종료가 기존 프로그램을 종료하지는 않습니다.
1920×1080 로컬 검증에서 x11vnc RSS 한 시점은 약 17.5MiB였습니다.
이 값은 최대 사용량이나 websockify를 포함한 전체 사용량이 아닙니다.

## 전제 조건

X11 사용자 로그인 세션이 필요합니다. 현재 장비의 jetson 자동 로그인, X11 설정과
`:0` 화면 접근을 확인했습니다. Wayland는 지원하지 않습니다.
**모니터 없이 전원을 켰을 때 X11이 정상 해상도로 실행되는지는 별도 검증이 필요합니다.**
원격 접속 시 모니터/EDID/Xorg 설정 파일을 변경하지 않습니다.
Jetson에서는 서비스 설치 또는 UI 원격 접속 실행 시 무모니터 출력을 함께 준비합니다.
부팅 전에 모니터를 감지해 물리 출력 또는 무모니터 1080p 출력을 선택합니다.
최초 그래픽 설정 적용 후 모니터 없이 재부팅해 검증해야 합니다.
적용·복원·검증 절차는 [HEADLESS.md](HEADLESS.md)를 참고하세요.

## 설치

일반 jetson 계정에서 실행합니다. 필요한 작업만 sudo로 실행됩니다.

```bash
cd /home/jetson/jon_city_ap_remote
bash install.sh dependencies
python3 remote.py prepare --device-id JON-CITY-001 --location '설치 위치'
python3 remote.py passwd
python3 remote.py check
```

의존성은 Ubuntu의 `x11vnc`, `novnc`, `websockify`입니다. 최초 설치에 인터넷이 필요하며,
실제 접속에는 필요 없습니다. noVNC 파일은 장비 내부에서 제공합니다.
`install.sh dependencies`는 패키지 설치 확인(`[Y/n]`)을 자동 승인합니다.
관리자 권한이 필요하면 sudo 비밀번호는 입력해야 합니다.
최초 설치는 AP를 켜기 전에 외부 Wi-Fi/유선 인터넷에서 진행하고,
소프트웨어 센터·업데이트 창은 닫아두세요. `install.sh dependencies`는 각 단계에서
다른 패키지 작업의 잠금이 풀리기를 최대 5분 기다립니다. 패키지 목록 잠금도 재시도하며,
시간을 초과하면 진단 명령을 안내합니다. 네트워크·저장소 오류는 잠금 재시도로 숨기지 않습니다.
설치 중인 업데이트를 강제 종료하거나 잠금 파일을 삭제하지 않습니다.
더 오래 기다리려면 `JON_CITY_APT_LOCK_TIMEOUT=600 bash install.sh dependencies`처럼
초 단위로 지정하세요(0~3600초). 잠금 대기 시간이며 다운로드·설치 전체 시간 제한은 아닙니다.
PackageKit이 멈춘 경우에는 실제 설치 진행 여부를 확인한 뒤 정상 중지하는 조치가 필요할 수 있습니다.

출고 후 Ubuntu/APT 자동 업데이트를 사용하지 않을 장비에는 의존성 설치와 검증을 마친 뒤
다음 명령을 별도로 실행합니다. 일반 `dependencies` 설치는 자동 업데이트 정책을 변경하지 않습니다.

```bash
bash install.sh disable-auto-updates
bash install.sh auto-updates-status
```

전용 설정 `/etc/apt/apt.conf.d/99jon-city-no-auto-updates`에 APT 주기 작업을 끄는 값을 저장하고,
`apt-daily.timer`와 `apt-daily-upgrade.timer`를 중지·자동 실행 해제합니다.
`apt-daily.service`, `apt-daily-upgrade.service`, `packagekit.service`는 mask하여
재부팅 후나 D-Bus 요청으로 다시 실행되지 않게 합니다. 이 설정은 재부팅 후에도 유지됩니다.
이미 실행 중인 설치·다운로드 작업은 중단하지 않습니다. PackageKit이 이미 실행 중이면
현재 작업이 끝나고 서비스가 종료되거나 재부팅된 이후부터 차단됩니다.
상태 출력에서 APT 설정은 `0`, 타이머는 `disabled`/`inactive`, 서비스는 `masked`인지 확인하세요.
`masked`이면서 `active`이면 기존 서비스가 아직 실행 중인 상태입니다.

터미널의 수동 `apt-get` 설치 및 `install.sh dependencies`는 계속 사용할 수 있습니다.
보안 업데이트도 자동 설치되지 않습니다. Snap·컨테이너·별도 앱 자체 업데이트는 이 설정의 대상이 아닙니다.
원래 APT 정책을 다시 적용하려면 전용 설정을 삭제하고 아래처럼 차단을 해제합니다.
기존 `20auto-upgrades` 등 다른 APT 설정은 그대로 유지됩니다.

```bash
sudo rm /etc/apt/apt.conf.d/99jon-city-no-auto-updates
sudo systemctl unmask apt-daily.service apt-daily-upgrade.service packagekit.service
sudo systemctl enable --now apt-daily.timer apt-daily-upgrade.timer
```

설정 참고: [Ubuntu 자동 업데이트](https://ubuntu.com/server/docs/how-to/software/automatic-updates/),
[systemd mask](https://www.freedesktop.org/software/systemd/man/latest/systemctl.html#mask%20UNIT%E2%80%A6).

`prepare`는 설정/unit 파일만 생성합니다. 기본값은 AP IP 192.168.50.1, 웹 포트 8080,
내부 VNC 포트 5901, 바탕화면 선택은 `auto`입니다. root 대신 바탕화면 사용자로 실행하세요.
AP를 설정한 뒤에는 장비 ID·설치 위치를 `device.json`에서 공통으로 가져오므로
`python3 remote.py prepare`만 실행해도 됩니다. AP 설정이 있으면 다른 ID를 지정해도
AP의 ID를 사용합니다. UI에서도 장비 이름을 별도로 수정하지 않습니다.
화면 번호가 :0·:1이어도 자동으로 찾습니다. 특정 번호를 지정할 때만 `--display :1`을 사용하세요.

`passwd`에서 원격 접속 비밀번호를 숨김 입력으로 설정합니다. 기본 VNC 인증은
비밀번호 앞 8자만 사용하므로 앞 8자를 충분히 무작위로 지정하세요.
UI에서는 영문·숫자·기호 8~63자를 입력할 수 있지만 뒤에 붙인 문자는 인증에 반영되지 않습니다.
Wi-Fi 비밀번호와 원격 접속 비밀번호는 따로 관리합니다.
Xauthority는 접속 때 `/run/user/<uid>/gdm/Xauthority`, `~/.Xauthority` 순서로 확인합니다.
필요하면 `prepare --xauthority /경로 --display :0`으로 직접 지정합니다.

### AP 설정

```bash
sudo python3 apctl.py configure --device-id JON-CITY-001 --location '설치 위치'
sudo python3 apctl.py up
sudo python3 apctl.py autostart on
```

`configure`는 비밀번호를 숨김 입력으로 받고 프로필만 생성합니다.
`up`은 기존 외부 Wi-Fi 연결을 끊을 수 있으므로 로컬/유선 접근이 확보된 상태에서 실행하세요.
무선 장치는 자동 확인하며 여러 개라면 `--interface`로 지정합니다.
프로필은 WPA2-Personal, 2.4GHz를 사용합니다. `--channel 1`로 채널을 지정할 수 있습니다.
장비 이름은 `JON-DEV-02`처럼 자유롭게 지정할 수 있으며 Wi-Fi 이름(SSID)으로도 사용합니다.
UTF-8 기준 1~32바이트를 허용하며, 공백만 있는 이름과 줄바꿈·제어문자는 사용할 수 없습니다.
여러 장비를 배치할 때는 이름을 구분하고 실제 배치에 맞춰 채널 간섭을 점검합니다.

기존 AP의 이름과 설치 위치를 수정하려면 `--update`를 사용합니다.

```bash
sudo python3 apctl.py configure --update --device-id JON-EXPO-01 --location '사내'
```

기존 프로필·Wi-Fi 비밀번호·IP·채널·부팅 자동 연결·이전 Wi-Fi 복구 정보는 유지합니다.
`--location`을 생략하면 기존 위치를 유지하며 `--location ''`으로 비울 수 있습니다.
`--update`에는 주소·채널·인터페이스·비밀번호 옵션을 함께 지정하지 않습니다.
실행 중인 AP를 자동으로 재연결하지 않으므로 새 Wi-Fi 이름은 다음 AP 연결 시 적용됩니다.
로컬/유선에서 UI의 **AP 끄기 → AP 켜기**를 사용하세요. AP를 끄면 원격 연결은 끊깁니다.
원격 접속의 표시 이름·위치도 바꾸려면 원격 접속 설정 창에서 해당 항목을 수정하고 다시 실행하세요.

각 독립 AP에는 같은 IP를 사용할 수 있습니다. 유선망/VPN과 충돌하면
`--address 192.168.60.1/24`로 바꾸고 원격 설정의 `--listen-address`도 맞추세요.
NetworkManager의 `ipv4.method=shared`가 DHCP와 DNS 전달을 제공합니다.
외부 기본 경로가 있으면 NAT로 인터넷을 공유할 수 있습니다. 로컬 접속만 허용하려면
현장 네트워크에 맞춰 전달 차단 정책을 추가해야 합니다.

이전 Wi-Fi로 복구하려면 `sudo python3 apctl.py restore`를 실행합니다.
구성 당시 연결 UUID로 복구하며 AP 자동 연결도 끕니다.
이전 연결 프로필이 삭제되었다면 수동으로 복구해야 합니다.

### 서비스 설치·시작

```bash
bash install.sh services
bash install.sh start
```

`services`는 unit 파일을 검증·설치하지만 시작하지 않습니다.
`start`는 웹/VNC 대기 소켓을 활성화하고 부팅 자동 시작을 등록합니다.
웹 소켓은 AP의 고정 IP에만, 내부 VNC 소켓은 localhost에만 열립니다.
AP가 아직 꺼져 있어도 웹 소켓은 대기할 수 있습니다.
방화벽이 있으면 AP 인터페이스의 웹 포트와 DHCP/DNS 통신을 허용해야 합니다.
방화벽 규칙은 자동 변경하지 않습니다.
설정을 바꾸면 unit 파일을 재설치하고 원격 서비스를 중지/시작하세요.

## 노트북 접속

1. Wi-Fi 목록에서 해당 장비의 `JON-CITY-001`을 선택하고 Wi-Fi 비밀번호를 입력합니다.
2. Chrome에서 `http://192.168.50.1:8080`을 엽니다.
3. 상단 장비 번호/설치 위치를 확인하고 **바탕화면 연결**을 누릅니다.
4. 원격 접속 비밀번호를 입력합니다.
5. 기존 카메라 프로그램을 열어 ROI를 설정합니다.
6. 필요 없는 GUI 창을 닫고 **연결 종료**를 누릅니다.

다른 SSID로 바꾼 뒤에는 페이지를 새로고침하고 장비 이름을 확인하세요.
클릭, 드래그, 키 입력을 실제 바탕화면으로 전달합니다. 브라우저/macOS가 가로채는
단축키는 noVNC 메뉴로 전송해야 합니다. Jetson의 키보드/입력기 설정도 영향을
주므로 macOS/Windows 및 한글 입력은 현장에서 검증하세요.
클립보드 동기화와 파일 전송은 기본으로 사용하지 않습니다.

원격 화면에 연결할 때 연결된 출력의 지원 모드를 확인해 1920×1080·60Hz를 적용합니다.
무모니터 템플릿의 `1920x1080_60` 이름도 처리합니다. DISPLAY와 Xauthority는 원격 설정 및
현재 사용자에서 가져옵니다. 사용할 출력·모드가 없거나 화면 접근에 실패하면
서비스 로그에 안내를 남기고 기존 해상도로 접속합니다. 물리 모니터를 연결한 경우에도
같은 바탕화면의 해상도가 변경됩니다. 재부팅 후에는 다음 원격 연결 시 다시 적용되며,
원격 연결 전 로그인 화면의 해상도까지 보장하는 설정은 아닙니다.

이미 연결 중인 세션에는 해상도 변경을 소급 적용하지 않습니다.
부팅 전 물리/무모니터 자동 선택 기능을 업데이트할 때는 서비스 설치도 갱신하고
모니터 연결/분리 부팅을 각각 검증해야 합니다.
`xrandr`가 없으면 `bash install.sh dependencies`로 의존성을 설치하세요.

## 운영

```bash
bash install.sh status
bash install.sh logs
bash install.sh stop
python3 apctl.py status
```

`stop`은 원격 서비스와 대기 소켓을 중지하고 자동 시작을 해제합니다.
AP와 기존 카메라 프로그램은 계속 동작합니다. AP 중지는 `sudo python3 apctl.py down`입니다.
`.state/vnc.passwd`와 `.state/remote.json`은 일반 계정 소유의 비공개 파일입니다.
Wi-Fi 비밀번호는 device.json 대신 NetworkManager 프로필에 저장합니다.
설정·비밀번호·복사된 제3자 파일은 Git에서 제외합니다.

기본 HTTP는 암호화된 현장 Wi-Fi 내부의 접속용입니다. HTTPS가 필요하면
`prepare --cert /인증서.pem --key /키.pem`을 사용합니다. WSS도 함께 사용하며,
브라우저가 인증서를 신뢰하도록 설치해야 합니다.
인터넷 중계/Google 계정 연결은 포함하지 않습니다.

## 검증

```bash
bash -n install.sh
```

운영에 필요 없는 `test_*.py` 단위 테스트 파일은 저장소에서 제거했습니다.
`verify_runtime.py`는 선택적인 읽기 전용 통합 검증이며 Python `cryptography`가 필요합니다.
현재 바탕화면에서 입력 없이 VNC 인증/화면 수신, 연결 종료 후 프로세스 종료,
상속한 소켓의 noVNC 파일 제공과 웹 서버 유휴 종료를 검증합니다.

```bash
python3 verify_runtime.py
```

구현 시 Ubuntu 패키지를 작업 폴더에만 풀어 이 통합 검증을 통과했습니다.
실제 AP 활성화, systemd 서비스 설치, 노트북 브라우저 입력 조작,
모니터 없는 재부팅과 7m 현장 무선 연결은 아직 검증하지 않았습니다.

공식 문서: [noVNC](https://github.com/novnc/noVNC),
[websockify](https://github.com/novnc/websockify),
[x11vnc](https://github.com/LibVNC/x11vnc),
[NetworkManager](https://networkmanager.pages.freedesktop.org/NetworkManager/NetworkManager/nmcli.html).
