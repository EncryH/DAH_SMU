import json
import os
import socket
import time
from pymavlink import mavutil

LISTEN_HOST    = '0.0.0.0'
LISTEN_PORT    = 14550
DASHBOARD_HOST = os.getenv("DASHBOARD_HOST", "dah-dashboard")
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "14571"))

intel = {
    'sys_id':   None,
    'last_seq': None,
    'lat':      None,
    'lon':      None,
    'alt':      None,
    'fuel':     None,
    'speed':    None,
    'mission':  'RECON',
}

_evt_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)


def _send(source, message, level="info", detail="", status=""):
    evt = {
        "platform_type": "AGENT",
        "agent_type":    "ATK",
        "platform_id":   "ATK-RECON",
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


def print_intel():
    print("─" * 50)
    print(f"[INTEL] SYS_ID  = {intel['sys_id']}")
    print(f"[INTEL] SEQ     = {intel['last_seq']}  ← 다음 공격 패킷은 {(intel['last_seq'] or 0) + 1}번")
    print(f"[INTEL] 위치    = 위도 {intel['lat']} / 경도 {intel['lon']}")
    print(f"[INTEL] 고도    = {intel['alt']}m")
    print(f"[INTEL] 연료    = {intel['fuel']}%")
    print("─" * 50)
    _send("RECON",
          f"인텔 수집 완료 SYS_ID={intel['sys_id']} SEQ={intel['last_seq']}",
          level="info",
          detail=f"lat={intel['lat']} alt={intel['alt']}m fuel={intel['fuel']}%",
          status="OK")


def assess_attack_timing():
    fuel = intel['fuel']
    alt  = intel['alt']
    if fuel is None or alt is None:
        return

    if fuel >= 30 and alt >= 1000:
        print(f"[RECON] ⚠️  공격 최적 타이밍 — 연료={fuel}% 고도={alt}m")
        print(f"[RECON] ⚠️  LAND 명령 주입 시 임무 중단 가능")
        _send("RECON",
              f"공격 최적 타이밍 감지",
              level="warn",
              detail=f"연료={fuel}% 고도={alt}m — LAND 주입 시 임무 중단 가능",
              status="ALERT")
    else:
        print(f"[RECON] 공격 타이밍 미충족 — 연료={fuel}% 고도={alt}m")
        _send("RECON",
              f"공격 타이밍 미충족",
              level="info",
              detail=f"연료={fuel}% 고도={alt}m")


def main():
    print(f"[RECON] 감시 시작 — UDP {LISTEN_PORT} 포트 도청 중...")
    _send("RECON", "도청 시작", detail=f"UDP {LISTEN_PORT} 포트 감시")

    mav = mavutil.mavlink_connection(f'udpin:{LISTEN_HOST}:{LISTEN_PORT}')

    while True:
        msg = mav.recv_match(blocking=True)
        if msg is None:
            continue

        msg_type = msg.get_type()
        intel['sys_id']   = msg.get_srcSystem()
        intel['last_seq'] = msg._header.seq

        if msg_type == 'HEARTBEAT':
            print(f"[RECON] HEARTBEAT 수신 | SYS_ID={intel['sys_id']} | SEQ={intel['last_seq']}")

        elif msg_type == 'SYS_STATUS':
            intel['fuel'] = msg.battery_remaining
            print(f"[RECON] SYS_STATUS 수신 | 연료={intel['fuel']}% | SEQ={intel['last_seq']}")

        elif msg_type == 'GLOBAL_POSITION_INT':
            intel['lat'] = msg.lat / 1e7
            intel['lon'] = msg.lon / 1e7
            intel['alt'] = msg.alt / 1000
            print(f"[RECON] POSITION 수신 | 위도={intel['lat']} 경도={intel['lon']} 고도={intel['alt']}m")
            assess_attack_timing()

        # 10패킷마다 인텔 요약 + 이벤트 전송 (약 5초 간격)
        if intel['last_seq'] and intel['last_seq'] % 10 == 0:
            print_intel()


if __name__ == '__main__':
    main()
