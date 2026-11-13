#!/usr/bin/env bash
set -euo pipefail
TASK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# DPkg::Lock::Timeout covers dpkg locks, but not apt's package-list lock.
# Retry the latter too, without stopping another package manager or deleting locks.
dependency_step() (
    apt_log=$(mktemp)
    trap 'rm -f "$apt_log"' EXIT
    apt_deadline=$((SECONDS + apt_lock_timeout))
    while true; do
      apt_remaining=$((apt_deadline - SECONDS))
      if (( apt_remaining < 0 )); then apt_remaining=0; fi
      if sudo env LC_ALL=C apt-get -o "DPkg::Lock::Timeout=$apt_remaining" "$@" 2>&1 | tee "$apt_log"; then
        return 0
      else
        apt_result=$?
      fi
      apt_output=$(<"$apt_log")
      if [[ "$apt_output" != *"Could not get lock "* &&
            "$apt_output" != *"Unable to acquire the dpkg frontend lock"* &&
            "$apt_output" != *"Unable to lock directory "* ]]; then
        echo '패키지 작업에 실패했습니다. 위의 네트워크·저장소·패키지 오류를 확인하세요.' >&2
        return "$apt_result"
      fi
      apt_remaining=$((apt_deadline - SECONDS))
      if (( apt_remaining <= 0 )); then
        echo "패키지 잠금 대기 시간(${apt_lock_timeout}초)을 초과했습니다." >&2
        echo '소프트웨어 센터·업데이트 창을 닫고, 진행 중인 설치·업데이트가 끝난 뒤 재실행하세요.' >&2
        echo '계속 막히면 다음 명령으로 잠금 보유 프로세스와 설치 진행 상태를 확인하세요:' >&2
        echo "  ps -eo pid,ppid,etime,stat,args | grep -E 'packagekit|apt|dpkg|unattended'" >&2
        echo '  sudo journalctl -u packagekit -n 30 --no-pager' >&2
        echo '  sudo tail -n 20 /var/log/dpkg.log' >&2
        echo '잠금 파일 삭제나 패키지 관리자 강제 종료는 하지 마세요.' >&2
        return "$apt_result"
      fi
      echo "다른 패키지 작업이 끝나기를 기다립니다… (남은 대기: ${apt_remaining}초)"
      if (( apt_remaining > 5 )); then apt_remaining=5; fi
      sleep "$apt_remaining"
    done
)

install_dependencies() {
    apt_lock_timeout=${JON_CITY_APT_LOCK_TIMEOUT:-300}
    if [[ ! "$apt_lock_timeout" =~ ^(0|[1-9][0-9]{0,3})$ ]] || (( apt_lock_timeout > 3600 )); then
      echo 'JON_CITY_APT_LOCK_TIMEOUT은 0~3600초로 지정하세요.' >&2
      return 2
    fi
    sudo -v
    echo "최초 설치: 외부 Wi-Fi/유선 인터넷에 연결하고 소프트웨어 센터·업데이트 창을 닫아주세요."
    echo "패키지 잠금은 단계마다 최대 ${apt_lock_timeout}초 기다립니다. 기존 업데이트를 강제 종료하지 않습니다."
    dependency_step update
    dependency_step install --assume-yes --no-install-recommends x11vnc novnc websockify python3-tk x11-xserver-utils x11-utils
    echo '의존성 설치 완료. 이제 UI에서 원격 접속을 설정할 수 있습니다.'
}

disable_auto_updates() (
    sudo -v
    updates_config=$(mktemp)
    trap 'rm -f "$updates_config"' EXIT
    cat > "$updates_config" <<'APT_CONFIG'
// JON-CITY deployment: use manual apt commands for package maintenance.
APT::Periodic::Enable "0";
APT::Periodic::Update-Package-Lists "0";
APT::Periodic::Download-Upgradeable-Packages "0";
APT::Periodic::Unattended-Upgrade "0";
APT_CONFIG
    # Stop scheduling new jobs. Existing apt/dpkg jobs are allowed to finish.
    sudo systemctl disable --now apt-daily.timer apt-daily-upgrade.timer
    sudo install -m 0644 "$updates_config" /etc/apt/apt.conf.d/99jon-city-no-auto-updates
    # PackageKit can also be activated by D-Bus, so disabling alone is not enough.
    # Do not use --now: an already-running package operation must not be killed.
    sudo systemctl mask apt-daily.service apt-daily-upgrade.service packagekit.service
    echo 'Ubuntu/APT 자동 업데이트와 PackageKit의 다음 실행을 차단했습니다.'
    echo '현재 진행 중인 패키지 작업은 종료하지 않았습니다. 완료를 기다려주세요.'
    echo '이미 실행 중인 PackageKit은 종료되거나 재부팅된 뒤부터 차단됩니다.'
    echo '수동 apt 설치와 install.sh dependencies는 계속 사용할 수 있습니다.'
    echo '보안 업데이트도 자동 설치되지 않습니다. Snap 등 별도 업데이트 경로는 이 설정의 대상이 아닙니다.'
)

auto_updates_status() {
    apt-config shell PeriodicEnabled APT::Periodic::Enable \
      RefreshLists APT::Periodic::Update-Package-Lists \
      DownloadPackages APT::Periodic::Download-Upgradeable-Packages \
      UnattendedUpgrade APT::Periodic::Unattended-Upgrade
    systemctl show apt-daily.timer apt-daily-upgrade.timer \
      apt-daily.service apt-daily-upgrade.service packagekit.service \
      -p Id -p ActiveState -p UnitFileState
}

configure_desktop() {
    [[ -f "$TASK_DIR/.state/remote.json" ]] || { echo '먼저 원격 접속 설정을 준비하세요.' >&2; return 1; }
    local desktop_user
    desktop_user=$(/usr/bin/python3 - "$TASK_DIR/.state/remote.json" <<'PY'
import json
import pwd
import sys
with open(sys.argv[1]) as source:
    user = json.load(source)['user']
if pwd.getpwnam(user).pw_uid == 0:
    raise SystemExit('root 대신 바탕화면 사용자로 원격 설정을 준비하세요.')
print(user)
PY
    )
    if [[ "$(id -u)" != 0 && "$(id -un)" != "$desktop_user" ]]; then
      echo '원격 설정의 바탕화면 사용자 계정에서 설치하세요.' >&2
      return 1
    fi
    if [[ -f /etc/nv_tegra_release ]]; then
      # Register output selection before GDM without restarting the desktop.
      # Validation and backup happen in the helper; never restart a running desktop here.
      sudo /usr/bin/python3 "$TASK_DIR/headless_setup.py" apply --user "$desktop_user" --connector DFP-1
      echo 'Jetson 물리/무모니터 자동 출력을 준비했습니다. 다음 재부팅부터 자동 선택합니다.'
    fi
    if [[ "$(id -u)" == 0 ]]; then
      sudo -u "$desktop_user" /usr/bin/python3 "$TASK_DIR/remote.py" auto-display
    else
      /usr/bin/python3 "$TASK_DIR/remote.py" auto-display
    fi
}

install_services() {
    for unit in jon-city-vnc.socket jon-city-vnc@.service jon-city-web.socket jon-city-web.service; do
      [[ -f "$TASK_DIR/build/$unit" ]] || { echo '먼저 remote.py prepare를 실행하세요.' >&2; exit 1; }
    done
    [[ -s "$TASK_DIR/.state/vnc.passwd" ]] || { echo '먼저 remote.py passwd를 실행하세요.' >&2; exit 1; }
    sudo systemd-analyze verify "$TASK_DIR/build/jon-city-vnc.socket" \
      "$TASK_DIR/build/jon-city-vnc@.service" "$TASK_DIR/build/jon-city-web.socket" "$TASK_DIR/build/jon-city-web.service"
    configure_desktop
    sudo install -m 644 "$TASK_DIR/build/jon-city-vnc.socket" \
      "$TASK_DIR/build/jon-city-vnc@.service" "$TASK_DIR/build/jon-city-web.socket" \
      "$TASK_DIR/build/jon-city-web.service" /etc/systemd/system/
    sudo systemctl daemon-reload
}
case "${1:-help}" in
  dependencies)
    install_dependencies
    ;;
  disable-auto-updates)
    disable_auto_updates
    ;;
  auto-updates-status)
    auto_updates_status
    ;;
  headless)
    if [[ "$(id -u)" == 0 ]]; then
      echo 'sudo 없이 바탕화면 사용자 계정에서 bash install.sh headless 를 실행하세요.' >&2
      exit 1
    fi
    sudo /usr/bin/python3 "$TASK_DIR/headless_setup.py" apply --user "$(id -un)" --connector "${2:-DFP-1}"
    if [[ -f "$TASK_DIR/.state/remote.json" ]]; then
      /usr/bin/python3 "$TASK_DIR/remote.py" auto-display
    else
      echo '원격 설정을 처음 준비할 때 바탕화면 자동 선택이 기본 적용됩니다.'
    fi
    ;;
  headless-restore)
    sudo /usr/bin/python3 "$TASK_DIR/headless_setup.py" restore
    ;;
  services)
    install_services
    echo '서비스 설치 완료. 아직 시작하지 않았습니다.'
    ;;
  remote-enable)
    [[ "${2:-}" == on || "${2:-}" == off ]] || exit 2
    install_services
    sudo systemctl stop jon-city-web.socket jon-city-vnc.socket jon-city-web.service 'jon-city-vnc@*.service'
    sudo systemctl disable jon-city-web.socket jon-city-vnc.socket
    sudo systemctl start jon-city-vnc.socket jon-city-web.socket
    if [[ "$2" == on ]]; then
      sudo systemctl enable jon-city-vnc.socket jon-city-web.socket
    fi
    ;;
  start)
    sudo systemctl enable --now jon-city-vnc.socket jon-city-web.socket
    ;;
  stop)
    sudo systemctl disable --now jon-city-web.socket jon-city-vnc.socket
    sudo systemctl stop jon-city-web.service
    sudo systemctl stop 'jon-city-vnc@*.service'
    ;;
  status)
    systemctl --no-pager status jon-city-web.socket jon-city-vnc.socket jon-city-web.service
    ;;
  logs)
    journalctl -u jon-city-web.service -u 'jon-city-vnc@*' -u jon-city-display-select.service --no-pager -n 80
    ;;
  ui)
    python3 "$TASK_DIR/install_network_ui.py"
    ;;
  *) echo 'Usage: bash install.sh dependencies|headless|headless-restore|disable-auto-updates|auto-updates-status|services|start|stop|status|logs|ui' ;;
esac
