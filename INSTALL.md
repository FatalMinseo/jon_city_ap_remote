# 처음 설치하는 Jetson의 전체 순서

최신 수정본을 Git 서버에 푸시한 뒤 받는 장비에서 진행합니다.
푸시는 개발 장비에서 사용자가 직접 수행합니다. 이미 설치한 장비에 이 최초 설정을
반복하면 기존 설정을 덮어쓸 수 있으므로 아래 절차는 새 설치 기준입니다.

일반 바탕화면 사용자 계정으로 실행합니다. 예시는 `jetson`, 장비 ID는 `JON-DEV-001`입니다.
장비마다 ID를 다르게 지정하세요. 현재 무모니터 자동 구성은 Jetson R36.4 계열의
NVIDIA Tegra0 / DFP-1을 확인하는 프로필이며, 확인 실패 시 설치가 중단됩니다.
실제 장비의 무모니터 부팅·입력 조작 검증은 설치 후 수행해야 합니다.

## 1. 소스 받기

처음에는 외부 Wi-Fi 또는 유선 인터넷에 연결해 두세요.

```bash
cd ~
git clone https://github.com/FatalMinseo/jon_city_ap_remote.git
cd ~/jon_city_ap_remote
```

Git 서버 로그인 정보를 요청하면 입력합니다.

## 2. 의존성 설치

```bash
bash install.sh dependencies
```

x11vnc·noVNC·websockify·Tk 및 X11 도구를 설치합니다. 오류가 나면 다음 단계로
넘어가지 말고 그 오류부터 해결하세요.

## 3. 장비 ID와 AP 비밀번호 설정

```bash
sudo python3 apctl.py configure \
  --device-id JON-DEV-001 \
  --location '설치 위치'
```

`Wi-Fi 비밀번호:`는 노트북·폰이 핫스팟에 연결할 때 쓰는 비밀번호입니다.
출력 가능한 ASCII 8~63자로 지정하며 한글은 사용할 수 없습니다.
이 단계는 프로필만 생성하고 현재 Wi-Fi는 전환하지 않습니다.
장비 ID·설치 위치는 AP, 원격 UI, 브라우저 화면이 공통으로 사용합니다.

## 4. 원격 설정 준비

```bash
python3 remote.py prepare
```

앞에서 정한 ID를 자동으로 사용합니다. `--device-id`를 다시 입력하지 않습니다.
화면 번호는 `auto`이므로 :0·:1을 지정하지 않습니다.

## 5. 원격 바탕화면 비밀번호 설정

```bash
python3 remote.py passwd
```

`Enter VNC password`와 `Verify password`에 같은 비밀번호를 입력하고,
저장 여부를 물으면 `y`를 입력합니다. 입력 문자가 보이지 않는 것은 정상입니다.
현재 VNC 인증에는 앞 8자만 반영되므로 무작위 ASCII 8자리로 설정하세요.
AP Wi-Fi 비밀번호 및 sudo 관리자 비밀번호와는 별개입니다.

## 6. 서비스와 부팅 전 화면 선택 등록

```bash
bash install.sh services
```

원격 서비스를 설치하고 Jetson에서는 GDM X11 자동 로그인과 물리/무모니터 출력 선택을
함께 준비합니다. 별도의 `headless` 명령은 필요 없습니다. 현재 바탕화면은 재시작하지
않으며, 부팅 전 화면 선택은 이후 장비를 다시 켰을 때 검증합니다.
로그인 후에는 지원되는 1920×1080·60Hz를 원격 접속 여부와 관계없이 적용하도록 등록합니다.

## 7. 바탕화면 아이콘 설치

```bash
bash install.sh ui
```

바탕화면과 앱 메뉴에 **JON-CITY 네트워크** 아이콘을 만듭니다.
바탕화면 아이콘이 실행되지 않으면 우클릭 → **실행 허용**을 선택하세요.
UI는 Jetson의 실제 바탕화면에서 열립니다. SSH 터미널에서 직접 창을 실행하려고
DISPLAY를 수동 설정할 필요 없이 바탕화면 아이콘을 사용하면 됩니다.

## 8. 원격 서비스 시작·자동 시작 등록

```bash
bash install.sh start
```

웹/VNC 대기 소켓을 시작하고 부팅 시 자동 시작을 등록합니다. AP 활성화는 다음 단계입니다.

## 9. AP 활성화

이 단계는 Jetson의 로컬 터미널이나 유선 관리 연결에서 진행하세요.
기존 외부 Wi-Fi를 통한 SSH는 전환 시 끊길 수 있습니다.

```bash
sudo python3 apctl.py autostart on
sudo python3 apctl.py up
```

재부팅 후에도 AP를 자동으로 연결하도록 설정합니다.
설치된 아이콘에서도 **선택한 모드를 부팅 후에도 유지**를 체크하고 **AP 켜기**로 전환할 수 있습니다.

## 10. 브라우저 연결 확인

노트북·폰을 **JON-DEV-001** Wi-Fi에 연결하고 3단계의 Wi-Fi 비밀번호를 입력합니다.
브라우저에서 다음 주소를 엽니다.

```text
http://192.168.50.1:8080
```

장비 ID가 `JON-DEV-001`인지 확인하고 **바탕화면 연결**을 눌러 5단계의 원격 비밀번호를
입력합니다. 기존 바탕화면이 보이고 마우스·키보드 입력이 전달되는지 확인합니다.
원격 연결 종료는 추론 프로그램 종료를 뜻하지 않습니다.

## 11. 무모니터 부팅 검증

실행 중인 GUI·추론 작업을 정리하고 장비를 종료합니다.

```bash
sudo poweroff
```

전원이 꺼지면 HDMI/DP 모니터 케이블을 분리하고 장비 전원을 다시 켭니다.
모니터 전원만 끄는 것과 케이블을 분리하는 것은 다를 수 있습니다.
부팅 후 AP에 다시 연결하고 같은 브라우저 주소에서 바탕화면·입력을 확인합니다.
이 과정에서 모니터를 다시 연결하면 무모니터 부팅 검증과 구분해야 합니다.

문제가 있으면 모니터를 연결하기 전에 AP를 통해 SSH로 접속해 확인합니다.

```bash
cd ~/jon_city_ap_remote
python3 remote.py check
bash install.sh logs
```

화면 선택 기록만 확인하려면 다음을 사용합니다.

```bash
sudo journalctl -b -u jon-city-display-select.service --no-pager
```

## 이후 로컬 UI 사용

바탕화면의 **JON-CITY 네트워크** 아이콘을 열어 Wi-Fi/AP 전환과 원격 접속 허용·중지를
할 수 있습니다. UI에서 원격 접속 비밀번호·부팅 후 허용 여부를 바꿀 수도 있습니다.
최초 설치 직후 원격 서비스는 8단계에서 이미 시작했으므로 UI에서 다시 실행할 필요는 없습니다.

외부 Wi-Fi로 돌아가려면 UI에서 저장된 Wi-Fi를 선택하고 **선택한 Wi-Fi로 전환**을 누릅니다.
전환하면 AP를 통한 원격 연결은 끊깁니다. CLI 복구는 다음과 같습니다.

```bash
sudo python3 apctl.py restore
```
