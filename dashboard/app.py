import json
import os
import socket
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from flask import Flask, jsonify, render_template

app = Flask(__name__)

MISSION_CONTROL_URL   = os.getenv("MISSION_CONTROL_URL", "http://mission-control:8080")
GCS_URL               = os.getenv("GCS_URL",            "http://dah-gcs:8080")
GCS_TELEMETRY_PORT    = int(os.getenv("ROUTER_TELEMETRY_PORT", "14571"))  # GCS/Router UDP fan-out 수신 포트

# UDP로 직접 수신한 플랫폼 상태 + 로컬 이벤트 (UGV 등 GCS 비경유 데이터)
router_platforms: dict = {}
local_events: deque  = deque(maxlen=100)
agent_events: deque  = deque(maxlen=300)


def _router_udp_listener():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", GCS_TELEMETRY_PORT))
    print(f"[DASH] UDP 직수신 대기 → {GCS_TELEMETRY_PORT}  (GCS fan-out + Router UGV)")
    while True:
        try:
            data, _ = sock.recvfrom(8192)
            payload = json.loads(data.decode())
            pid  = payload.get("platform_id", "UNKNOWN")
            ptype = payload.get("platform_type", "")
            # 에이전트 판단 이벤트는 별도 큐에 저장
            if ptype == "AGENT":
                agent_events.appendleft({
                    "time":       payload.get("time", time.strftime("%H:%M:%S")),
                    "level":      payload.get("level", "info"),
                    "agent_type": payload.get("agent_type", ""),
                    "source":     payload.get("source", ""),
                    "message":    payload.get("message", ""),
                    "detail":     payload.get("detail", ""),
                    "status":     payload.get("status", ""),
                })
                continue

            router_platforms[pid] = {**payload, "gcs_received_at": time.time()}

            # GCS를 거치지 않는 플랫폼(UGV 등)은 여기서 로컬 이벤트 생성
            if ptype != "UAV":
                local_events.appendleft({
                    "time":    time.strftime("%H:%M:%S"),
                    "level":   "info",
                    "source":  pid,
                    "message": (
                        f"telemetry seq={payload.get('seq')} "
                        f"spd={payload.get('speed')}m/s "
                        f"batt={payload.get('battery')}%"
                    ),
                })
        except Exception:
            pass


threading.Thread(target=_router_udp_listener, daemon=True).start()


TOPOLOGY = {
    "title": "UAV/UGV 전술통신 파이프라인",
    "subtitle": "CC(UAV) 텔레메트리가 GCS로 직수신된 후 Dashboard / Collector / Router로 fan-out되고, Router가 TMMR/TICN 시뮬레이션을 통해 Upper C2/BMS로 전달합니다.",
    "network": {
        "name": "uav_net / ops_net / dah-net",
        "subnet": "pipeline separated networks",
        "type": "Docker bridge networks",
    },
    "nodes": [
        {
            "id": "us",
            "name": "US",
            "label": "Unmanned Systems",
            "role": "무인체계 전체 범위",
            "status": "scope",
            "ip": "-",
            "group": "system",
        },
        {
            "id": "uav",
            "name": "UAV",
            "label": "dah-uav",
            "role": "송골매 UAV 시뮬레이터",
            "status": "implemented",
            "ip": "172.20.0.10",
            "group": "vehicle",
        },
        {
            "id": "ugv",
            "name": "UGV",
            "label": "dah-ugv",
            "role": "무인지상차량 시뮬레이터",
            "status": "implemented",
            "ip": "uav_net",
            "group": "vehicle",
        },
        {
            "id": "router",
            "name": "Tactical Router",
            "label": "dah-tactical-router",
            "role": "GCS 전술망 릴레이 → TMMR/TICN 시뮬레이션 → Upper C2/BMS",
            "status": "implemented",
            "ip": "uav_net/ops_net",
            "group": "network",
        },
        {
            "id": "mission",
            "name": "Upper C2/BMS",
            "label": "dah-mission-control",
            "role": "작전 상황 판단 / 명령 하달",
            "status": "implemented",
            "ip": "ops_net:8080",
            "group": "ops",
        },
        {
            "id": "collector",
            "name": "Telemetry Collector",
            "label": "dah-telemetry-collector",
            "role": "전술 텔레메트리 로그 수집",
            "status": "implemented",
            "ip": "ops_net:14541",
            "group": "ops",
        },
        {
            "id": "recon",
            "name": "Recon",
            "label": "dah-recon",
            "role": "텔레메트리 도청/분석",
            "status": "implemented",
            "ip": "172.20.0.40",
            "group": "attack",
        },
        {
            "id": "executor",
            "name": "Executor",
            "label": "dah-executor",
            "role": "COMMAND_LONG LAND 주입",
            "status": "implemented",
            "ip": "172.20.0.50",
            "group": "attack",
        },
        {
            "id": "defense",
            "name": "Defense",
            "label": "dah-defense",
            "role": "비정상 명령 탐지/대응",
            "status": "implemented",
            "ip": "172.20.0.60",
            "group": "defense",
        },
        {
            "id": "dashboard",
            "name": "Dashboard",
            "label": "dah-dashboard",
            "role": "통신 구조 시각화",
            "status": "implemented",
            "ip": "172.20.0.70",
            "group": "ops",
        },
    ],
    "links": [
        {
            "source": "us",
            "target": "uav",
            "protocol": "범위",
            "port": "-",
            "flow": "US 하위 체계",
            "status": "implemented",
        },
        {
            "source": "us",
            "target": "ugv",
            "protocol": "범위",
            "port": "-",
            "flow": "US 하위 체계",
            "status": "planned",
        },
        {
            "source": "uav",
            "target": "dashboard",
            "protocol": "MAVLink/JSON / UDP",
            "port": "14555",
            "flow": "CC → GCS 텔레메트리 직수신",
            "status": "implemented",
        },
        {
            "source": "ugv",
            "target": "router",
            "protocol": "JSON telemetry / UDP",
            "port": "14660",
            "flow": "UGV 위치, 속도, 지상 센서 상태",
            "status": "implemented",
        },
        {
            "source": "dashboard",
            "target": "router",
            "protocol": "JSON telemetry / UDP",
            "port": "14560",
            "flow": "GCS → Router 전술망 릴레이",
            "status": "implemented",
        },
        {
            "source": "dashboard",
            "target": "collector",
            "protocol": "JSON telemetry / UDP",
            "port": "14541",
            "flow": "GCS → Collector fan-out",
            "status": "implemented",
        },
        {
            "source": "router",
            "target": "mission",
            "protocol": "JSON+TMMR/TICN / UDP",
            "port": "14545",
            "flow": "Router → Upper C2/BMS 전술 상황",
            "status": "implemented",
        },
        {
            "source": "mission",
            "target": "router",
            "protocol": "JSON / UDP",
            "port": "14546",
            "flow": "Upper C2/BMS 작전 명령 하달",
            "status": "implemented",
        },
    ],
    "notes": [
        "텔레메트리 파이프라인: CC → GCS → (Dashboard / Collector / Router) → Upper C2/BMS",
        "명령 파이프라인: Upper C2/BMS → Router → GCS → CC → UAV FC (MAVLink)",
        "Recon/Executor/Defense는 별도의 직접 공격/방어 실습 레이어로 유지됩니다.",
        "Upper C2/BMS API: http://localhost:8082  |  GCS API: http://localhost:8083",
    ],
}


def fetch_json(path):
    url = f"{MISSION_CONTROL_URL}{path}"
    try:
        with urllib.request.urlopen(url, timeout=1.5) as response:
            return json_loads(response.read())
    except (urllib.error.URLError, TimeoutError, ValueError):
        return None


def json_loads(raw):
    import json

    return json.loads(raw.decode("utf-8"))


@app.get("/")
def index():
    return render_template("index.html", topology=TOPOLOGY)


@app.get("/api/topology")
def topology():
    return jsonify(TOPOLOGY)




def fetch_gcs(path):
    url = f"{GCS_URL}{path}"
    try:
        with urllib.request.urlopen(url, timeout=1.5) as response:
            return json_loads(response.read())
    except (urllib.error.URLError, TimeoutError, ValueError):
        return None


@app.get("/api/live")
def live():
    # GCS 우선 fetch (최신 데이터 보유)
    dashboard = fetch_gcs("/api/dashboard")
    if dashboard is None:
        # GCS 불가 시 Upper C2/BMS fallback
        dashboard = fetch_json("/api/dashboard")
    if dashboard is None:
        return jsonify({
            "status": "degraded",
            "message": "GCS / Upper C2 unavailable (direct UDP only)",
            "platforms": list(router_platforms.values()),
            "events": [],
        })
    # 플랫폼: GCS + UDP 직수신 병합 (UDP 직수신 우선)
    mc_platforms = {p["platform_id"]: p for p in dashboard.get("platforms", [])}
    merged = {**mc_platforms, **router_platforms}

    # 이벤트: GCS 이벤트 + UGV 로컬 이벤트 병합 후 시간순 정렬
    gcs_events = dashboard.get("events", [])
    merged_events = list(gcs_events) + list(local_events)
    merged_events.sort(key=lambda e: e.get("time", ""), reverse=True)

    return jsonify({
        "status": "ok",
        **dashboard,
        "platforms":    list(merged.values()),
        "events":       merged_events[:50],
        "agent_events": list(agent_events)[:100],
        "gcs_direct":   len(router_platforms),
    })


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
