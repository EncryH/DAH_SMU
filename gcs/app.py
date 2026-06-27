"""
GCS / Ground Gateway / Mission Control Server
역할:
  - CC(UAV) 텔레메트리 직접 수신 (포트 14555)
  - Dashboard / Telemetry Collector fan-out
  - Tactical Router로 전술망 연동 데이터 전달 (포트 14560)
  - Upper C2/BMS 명령 수신 (Router 경유, 포트 14562) → CC 하달
  - 운용자 직접 Command → CC (포트 14552)
"""
import json
import os
import socket
import threading
import time
from flask import Flask, jsonify, request

# ── 포트 설정 ─────────────────────────────────────────────────────────────
CC_LISTEN_PORT    = int(os.getenv("CC_LISTEN_PORT",    "14555"))  # CC → GCS 텔레메트리
C2_CMD_LISTEN_PORT= int(os.getenv("C2_CMD_LISTEN_PORT","14562"))  # Router → GCS (Upper C2 명령)

DASHBOARD_HOST = os.getenv("DASHBOARD_HOST", "dah-dashboard")
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "14571"))
COLLECTOR_HOST = os.getenv("COLLECTOR_HOST", "telemetry-collector")
COLLECTOR_PORT = int(os.getenv("COLLECTOR_PORT", "14541"))
ROUTER_HOST    = os.getenv("ROUTER_HOST",    "tactical-router")
ROUTER_PORT    = int(os.getenv("ROUTER_PORT",    "14560"))  # GCS → Router 전술 릴레이

CC_HOST        = os.getenv("CC_HOST",        "dah-companion")
CC_CMD_PORT    = int(os.getenv("CC_CMD_PORT",    "14552"))  # GCS → CC 직접 명령

app = Flask(__name__)
platforms: dict = {}
event_log: list = []
_out_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)


def add_event(source: str, message: str, level: str = "info",
              event_type: str = "telemetry", target: str = "", status: str = ""):
    entry = {
        "time":    time.strftime("%H:%M:%S"),
        "level":   level,
        "type":    event_type,
        "source":  source,
        "message": message,
    }
    if target: entry["target"] = target
    if status: entry["status"] = status
    event_log.insert(0, entry)
    if len(event_log) > 200:
        event_log.pop()


def fanout(payload: dict):
    """Dashboard · Collector · Tactical Router 순서로 분배"""
    data = json.dumps(payload).encode()
    for name, host, port in [
        ("Dashboard",        DASHBOARD_HOST, DASHBOARD_PORT),
        ("Collector",        COLLECTOR_HOST, COLLECTOR_PORT),
        ("Tactical Router",  ROUTER_HOST,    ROUTER_PORT),
    ]:
        try:
            _out_sock.sendto(data, (host, port))
        except Exception as e:
            print(f"[GCS] fan-out 실패 → {name}: {e}")


def cc_listener():
    """CC(UAV) 텔레메트리 직수신 → 상태 저장 → fan-out"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", CC_LISTEN_PORT))
    print(f"[GCS] CC 텔레메트리 수신 대기 → UDP {CC_LISTEN_PORT}")

    while True:
        data, addr = sock.recvfrom(8192)
        try:
            payload = json.loads(data.decode())
        except Exception:
            continue

        pid = payload.get("platform_id", "UNKNOWN")

        if payload.get("event") == "MISSION_ITEM_REACHED":
            wp_num = payload.get("wp_seq", 0) + 1
            add_event(pid,
                f"MISSION_ITEM_REACHED WP{wp_num} "
                f"lat={round(payload.get('lat', 0), 5)} alt={payload.get('alt', '--')}m",
                event_type="telemetry")
            print(f"[GCS] ← CC  {pid}  MISSION_ITEM_REACHED WP{wp_num}")
        else:
            platforms[pid] = {**payload, "gcs_received_at": time.time()}
            add_event(pid,
                f"telemetry seq={payload.get('seq')} "
                f"alt={payload.get('alt')}m fuel={payload.get('fuel')}%")
            print(f"[GCS] ← CC  {pid}  seq={payload.get('seq')}  "
                  f"alt={payload.get('alt')}m  fuel={payload.get('fuel')}%")
            fanout(platforms[pid])


def c2_cmd_listener():
    """Router 경유 Upper C2/BMS 명령 수신 → CC 하달"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", C2_CMD_LISTEN_PORT))
    print(f"[GCS] Upper C2 명령 수신 대기 → UDP {C2_CMD_LISTEN_PORT}")

    while True:
        data, addr = sock.recvfrom(4096)
        try:
            cmd = json.loads(data.decode())
        except Exception:
            continue

        command = cmd.get("command", "")
        source  = cmd.get("source", "Upper C2/BMS")
        print(f"[GCS] ← Upper C2  [{command}]  출처={source}")
        add_event("Upper C2/BMS", command, "warn",
                  event_type="command", target=cmd.get("target", "UAV-001"), status="SENT")

        # Upper C2 명령을 CC용 Command로 변환 후 하달
        cc_cmd = {
            "command":   command,
            "source":    source,
            "via":       "Upper C2/BMS → TICN → TMMR → Router → GCS",
            "timestamp": time.time(),
        }
        try:
            _out_sock.sendto(json.dumps(cc_cmd).encode(), (CC_HOST, CC_CMD_PORT))
            print(f"[GCS] → CC  [{command}]  {CC_HOST}:{CC_CMD_PORT}")
        except Exception as e:
            print(f"[GCS] CC 명령 전송 실패: {e}")


threading.Thread(target=cc_listener,   daemon=True).start()
threading.Thread(target=c2_cmd_listener, daemon=True).start()


# ── HTTP API ───────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return jsonify({"status": "ok"})


@app.get("/api/status")
def status():
    return jsonify({
        "platforms": list(platforms.values()),
        "events":    event_log[:30],
        "count":     len(platforms),
    })


@app.get("/api/dashboard")
def dashboard():
    return jsonify({
        "platforms": list(platforms.values()),
        "events":    event_log[:20],
        "links": {
            "ticn":   "NORMAL",
            "satcom": "DEGRADED",
            "tdl":    "ACTIVE",
        },
    })


@app.post("/api/command")
def command():
    """
    운용자 직접 명령 → GCS → CC (Router 경유 없음)
    body: {"target": "UAV-001", "command": "LAND" | "RTB"}
    """
    cmd = request.get_json(silent=True)
    if not cmd or "command" not in cmd:
        return jsonify({"status": "error", "message": "command 필드 필요"}), 400

    cc_cmd = {
        "command":   cmd["command"],
        "target":    cmd.get("target", ""),
        "source":    "GCS/Operator",
        "via":       "GCS → CC (direct)",
        "timestamp": time.time(),
    }
    try:
        _out_sock.sendto(json.dumps(cc_cmd).encode(), (CC_HOST, CC_CMD_PORT))
        add_event("GCS/Operator", cmd["command"], "warn",
                  event_type="command", target=cmd.get("target", "UAV-001"), status="SENT")
        print(f"[GCS] → CC  [{cmd['command']}] (운용자 직접)")
        return jsonify({"status": "sent", "command": cmd["command"], "via": "GCS → CC (direct)"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
