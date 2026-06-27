import json
import math
import os
import select
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from ticn import TMMRNode, TICNNetwork, SharedState

# ── 포트 설정 ──────────────────────────────────────────────────────────────
CC_LISTEN_PORT  = int(os.getenv("CC_LISTEN_PORT",  "14555"))
UGV_LISTEN_PORT = int(os.getenv("UGV_LISTEN_PORT", "14660"))
CMD_LISTEN_PORT = int(os.getenv("CMD_LISTEN_PORT", "14580"))
JAM_LISTEN_PORT = int(os.getenv("JAM_LISTEN_PORT", "14590"))
STATUS_PORT     = int(os.getenv("STATUS_PORT",     "8080"))

MISSION_HOST   = os.getenv("MISSION_HOST",   "mission-control")
MISSION_PORT   = int(os.getenv("MISSION_PORT",   "14540"))
COLLECTOR_HOST = os.getenv("COLLECTOR_HOST", "telemetry-collector")
COLLECTOR_PORT = int(os.getenv("COLLECTOR_PORT", "14541"))
GCS_HOST       = os.getenv("GCS_HOST",       "dah-gcs")
GCS_PORT       = int(os.getenv("GCS_PORT",       "14570"))
DASHBOARD_HOST = os.getenv("DASHBOARD_HOST", "dah-dashboard")
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "14571"))
CC_CMD_HOST    = os.getenv("CC_CMD_HOST",    "dah-companion")
CC_CMD_PORT    = int(os.getenv("CC_CMD_PORT",    "14552"))

ROUTER_LAT = float(os.getenv("ROUTER_LAT", "37.85"))
ROUTER_LON = float(os.getenv("ROUTER_LON", "126.85"))

FAN_OUT = [
    ("Mission Control", MISSION_HOST,   MISSION_PORT),
    ("Collector",       COLLECTOR_HOST, COLLECTOR_PORT),
    ("GCS",             GCS_HOST,       GCS_PORT),
    ("Dashboard",       DASHBOARD_HOST, DASHBOARD_PORT),
]


# ── HTTP 상태 API ──────────────────────────────────────────────────────────

def make_http_handler(shared: SharedState, tmmr_nodes: dict, ticn: TICNNetwork):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass

        def _send(self, code: int, body: bytes):
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", len(body))
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self):
            self.send_response(200)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

        def do_GET(self):
            if self.path in ("/api/ticn", "/api/ticn/status"):
                body = json.dumps({
                    "tmmr": {pid: n.to_dict() for pid, n in tmmr_nodes.items()},
                    "ticn": ticn.status(),
                    "jammed_channels": shared.jammed_remaining(),
                    "recent_events":   shared.recent_events(15),
                }).encode()
                self._send(200, body)
            else:
                self._send(404, b'{"error":"not found"}')

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body   = json.loads(self.rfile.read(length) or b'{}')

            if self.path == "/api/ticn/jam":
                ch, dur = body.get("channel", "VHF"), float(body.get("duration", 30))
                shared.jam(ch, dur)
                self._send(200, json.dumps({"ok": True, "channel": ch, "duration": dur}).encode())
            elif self.path == "/api/ticn/clear":
                ch = body.get("channel", "VHF")
                shared.clear_jam(ch)
                self._send(200, json.dumps({"ok": True, "channel": ch}).encode())
            else:
                self._send(404, b'{"error":"not found"}')

    return Handler


# ── 유틸 ──────────────────────────────────────────────────────────────────

def bind_udp(port: int) -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("0.0.0.0", port))
    return s


def haversine(lat1, lon1, lat2, lon2) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1))*math.cos(math.radians(lat2))*math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(a))


def jam_udp_listener(shared: SharedState):
    sock = bind_udp(JAM_LISTEN_PORT)
    print(f"[TICN]  JAM 수신 대기  :{JAM_LISTEN_PORT}")
    while True:
        try:
            data, _ = sock.recvfrom(1024)
            msg = json.loads(data.decode())
            shared.jam(msg.get("channel", "VHF"), float(msg.get("duration", 30)))
        except Exception as e:
            print(f"[TICN]  JAM 파싱 오류: {e}")


# ── 메인 ──────────────────────────────────────────────────────────────────

def main():
    shared      = SharedState()
    tmmr_nodes: dict[str, TMMRNode] = {}
    ticn        = TICNNetwork()

    # HTTP 상태 API
    http_srv = HTTPServer(("0.0.0.0", STATUS_PORT), make_http_handler(shared, tmmr_nodes, ticn))
    threading.Thread(target=http_srv.serve_forever, daemon=True).start()
    print(f"[TICN]  HTTP API  :{STATUS_PORT}  →  /api/ticn/status")

    # JAM UDP 리스너
    threading.Thread(target=jam_udp_listener, args=(shared,), daemon=True).start()

    cc_sock  = bind_udp(CC_LISTEN_PORT)
    ugv_sock = bind_udp(UGV_LISTEN_PORT)
    cmd_sock = bind_udp(CMD_LISTEN_PORT)
    out_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    print("[ROUTER] ── TICN 전술 라우터 시작 ──────────────────────────")
    print(f"         CC(UAV) :{CC_LISTEN_PORT}  UGV :{UGV_LISTEN_PORT}  CMD :{CMD_LISTEN_PORT}")
    print(f"         Fan-out ×{len(FAN_OUT)}: " + ", ".join(f"{n}:{p}" for n, _, p in FAN_OUT))

    while True:
        readable, _, _ = select.select([cc_sock, ugv_sock, cmd_sock], [], [], 1)
        for sock in readable:
            data, addr = sock.recvfrom(8192)
            try:
                payload = json.loads(data.decode())
            except json.JSONDecodeError:
                continue

            # ── Command (QoS 우선 — TICN 손실 최소화)
            if sock is cmd_sock:
                payload.update({"router_forwarded_at": time.time(), "via": "TICN/TMMR"})
                out_sock.sendto(json.dumps(payload).encode(), (CC_CMD_HOST, CC_CMD_PORT))
                print(f"[CMD]  [{payload.get('command')}]  {addr[0]} → CC:{CC_CMD_PORT}")
                continue

            # ── Telemetry: TMMR → TICN 처리
            payload["router_received_at"] = time.time()
            pid = payload.get('platform_id', 'UNKNOWN')

            if pid not in tmmr_nodes:
                tmmr_nodes[pid] = TMMRNode(pid)
            tmmr = tmmr_nodes[pid]

            lat     = payload.get('lat') or 0
            lon     = payload.get('lon') or 0
            alt     = payload.get('alt') or 0
            dist_km = haversine(ROUTER_LAT, ROUTER_LON, lat, lon) if (lat and lon) else 0.0

            # TMMR 레이어
            jammed = shared.active_jammed()
            tmmr.update_rssi(dist_km, alt, jammed)
            tmmr.auto_hop(jammed, lambda ev: (shared.log(ev), ticn.log(ev)))
            tmmr.adjust_tx_power(dist_km)

            # TICN 레이어
            ticn.update_link(pid, dist_km, tmmr)
            result = ticn.route(payload, tmmr)

            if result is None:
                lq = ticn.links.get(pid)
                print(f"[TICN]  DROP  {pid}  LQ={lq.quality if lq else '?'}  jam={tmmr.jam_detected}")
                continue

            failed = []
            for name, host, port in FAN_OUT:
                try:
                    out_sock.sendto(json.dumps(result).encode(), (host, port))
                except Exception:
                    failed.append(name)

            t = result.get('tmmr', {})
            n = result.get('ticn', {})
            fo = f"fan-out×{len(FAN_OUT)-len(failed)}" + (f" fail:{','.join(failed)}" if failed else "")
            print(
                f"[TICN]  {pid}  wf={t.get('waveform')}  "
                f"RSSI={t.get('rssi_dbm')}dBm  TX={t.get('tx_power_pct')}%  "
                f"LQ={n.get('link_quality')}  loss={n.get('loss_pct')}%  "
                f"dist={n.get('dist_km')}km  {fo}"
            )


if __name__ == "__main__":
    main()
