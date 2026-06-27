import json
import os
import socket
import threading
import time
from pymavlink import mavutil
from detector import detect
from responder import respond

LISTEN_HOST    = '0.0.0.0'
LISTEN_PORT    = 14551
ALLOWED_GCS_ID = 255
CHECK_INTERVAL = 0.5

DASHBOARD_HOST = os.getenv("DASHBOARD_HOST", "dah-dashboard")
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "14571"))

alerts   = []
last_seq = {}
_evt_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)


def _send(source, message, level="info", detail="", status=""):
    evt = {
        "platform_type": "AGENT",
        "agent_type":    "DEF",
        "platform_id":   "DEF-001",
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


def monitor():
    mav = mavutil.mavlink_connection(f'udpin:{LISTEN_HOST}:{LISTEN_PORT}')
    print(f"[DEFENSE] 감시 시작 → 포트 {LISTEN_PORT}")
    _send("MONITOR", "패킷 감시 시작", detail=f"UDP {LISTEN_PORT} 포트")

    while True:
        msg = mav.recv_match(blocking=True)
        if msg is None:
            continue

        msg_type = msg.get_type()
        src_id   = msg.get_srcSystem()
        seq      = msg._header.seq

        if msg_type == 'COMMAND_LONG':
            cmd = msg.command
            print(f"[DEFENSE] COMMAND_LONG 감지 | SYS_ID={src_id} | 명령={cmd} | SEQ={seq}")

            if src_id != ALLOWED_GCS_ID:
                alerts.append({
                    'type':   'UNKNOWN_SRC',
                    'src_id': src_id,
                    'cmd':    cmd,
                    'seq':    seq,
                })
                print(f"[DEFENSE] ⚠️  비정상 출처 → SYS_ID={src_id}")
                _send("MONITOR",
                      f"비정상 COMMAND_LONG 탐지",
                      level="warn",
                      detail=f"SYS_ID={src_id} (허용={ALLOWED_GCS_ID}) cmd={cmd} SEQ={seq}",
                      status="ALERT")
            else:
                _send("MONITOR",
                      f"정상 명령 수신",
                      detail=f"SYS_ID={src_id} cmd={cmd} SEQ={seq}")

        if src_id in last_seq:
            if seq <= last_seq[src_id]:
                alerts.append({
                    'type':   'REPLAY',
                    'src_id': src_id,
                    'seq':    seq,
                })
                print(f"[DEFENSE] ⚠️  Replay Attack 의심 → SEQ={seq} (이전={last_seq[src_id]})")
                _send("MONITOR",
                      f"Replay Attack 의심",
                      level="warn",
                      detail=f"SYS_ID={src_id} SEQ={seq} ≤ 이전={last_seq[src_id]}",
                      status="ALERT")

        last_seq[src_id] = seq


def defense_loop():
    idle_ticks = 0
    while True:
        time.sleep(CHECK_INTERVAL)

        if not alerts:
            idle_ticks += 1
            if idle_ticks >= 20:  # 0.5s × 20 = 10초마다 상태 보고
                idle_ticks = 0
                _send("MONITOR",
                      "감시 중 — 이상 없음",
                      level="info",
                      detail=f"UDP {LISTEN_PORT} 포트 정상 감시 중",
                      status="OK")
            continue

        idle_ticks = 0
        current_alerts = alerts.copy()
        alerts.clear()

        threats = detect(current_alerts)
        if threats:
            print(f"[DEFENSE] 위협 {len(threats)}건 탐지 → 대응 시작")
            _send("DETECTOR",
                  f"위협 {len(threats)}건 탐지",
                  level="warn",
                  detail="; ".join(t.get('reason', '') for t in threats),
                  status="THREAT")
            respond(threats, _send)


def main():
    print(f"[DEFENSE] 방어 에이전트 시작")
    _send("DEFENSE", "방어 에이전트 시작",
          level="info",
          detail=f"monitor + detector + responder 통합 실행")

    t = threading.Thread(target=monitor, daemon=True)
    t.start()
    defense_loop()


if __name__ == '__main__':
    main()
