import json
import math
import os
import random
import select
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

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

# TICN 라우터(GCS) 위치 — 경기북부 작전지역 기준
ROUTER_LAT = float(os.getenv("ROUTER_LAT", "37.85"))
ROUTER_LON = float(os.getenv("ROUTER_LON", "126.85"))

FAN_OUT = [
    ("Mission Control", MISSION_HOST,   MISSION_PORT),
    ("Collector",       COLLECTOR_HOST, COLLECTOR_PORT),
    ("GCS",             GCS_HOST,       GCS_PORT),
    ("Dashboard",       DASHBOARD_HOST, DASHBOARD_PORT),
]


# ══════════════════════════════════════════════════════════════════════════════
# TICN / TMMR 시뮬레이션 레이어
# ══════════════════════════════════════════════════════════════════════════════

class TICNChannel:
    """TMMR 지원 채널 특성"""
    SPECS = {
        'VHF': {'band': '30-88 MHz',    'max_range_km': 50,  'base_loss': 0.01, 'jam_resist': 'LOW'},
        'UHF': {'band': '225-512 MHz',  'max_range_km': 25,  'base_loss': 0.02, 'jam_resist': 'MEDIUM'},
        'HF':  {'band': '2-30 MHz',     'max_range_km': 300, 'base_loss': 0.08, 'jam_resist': 'HIGH'},
    }
    # 재밍 발생 시 채널 홉 순서
    HOP_ORDER  = ['VHF', 'UHF', 'HF']


class NodeState:
    def __init__(self, platform_id):
        self.platform_id    = platform_id
        self.channel        = 'VHF'          # TMMR 기본 채널
        self.waveform       = 'K-WNW/VHF'
        self.link_quality   = 100            # 0–100
        self.loss_rate_pct  = 0.0
        self.dist_km        = 0.0
        self.lat = self.lon = self.alt = None
        self.rx_count       = 0
        self.drop_count     = 0
        self.hop_count      = 0
        self.last_seen      = time.time()


class TICNLayer:
    """
    TICN 전술통신망 시뮬레이션 레이어.
    - 노드별 채널(VHF/UHF/HF) 관리
    - 거리·고도 기반 링크 품질 계산
    - 패킷 손실 시뮬레이션
    - 재밍 감지 → TMMR 자동 채널 홉
    """

    def __init__(self):
        self.nodes: dict[str, NodeState] = {}
        self.jammed: dict[str, float]    = {}   # channel → jam_until (epoch)
        self.events: list[dict]          = []   # 최근 이벤트 50건
        self.lock = threading.Lock()

    # ── 내부 유틸 ─────────────────────────────────────────────────────────

    def _node(self, pid: str) -> NodeState:
        if pid not in self.nodes:
            self.nodes[pid] = NodeState(pid)
        return self.nodes[pid]

    @staticmethod
    def _haversine(lat1, lon1, lat2, lon2) -> float:
        R = 6371.0
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = (math.sin(dlat / 2) ** 2
             + math.cos(math.radians(lat1))
             * math.cos(math.radians(lat2))
             * math.sin(dlon / 2) ** 2)
        return R * 2 * math.asin(math.sqrt(a))

    def _link_quality(self, node: NodeState) -> int:
        spec = TICNChannel.SPECS[node.channel]
        # 거리 감쇄 (비선형)
        dist_factor = max(0.0, 1.0 - (node.dist_km / spec['max_range_km']) ** 1.5)
        # UAV 고도 가시선 보너스
        alt_bonus = min(0.15, (node.alt or 0) / 10000.0)
        lq = dist_factor * 100 + alt_bonus * 100
        # 가우시안 노이즈
        lq += random.gauss(0, 2.5)
        return max(5, min(100, int(lq)))

    def _loss_rate(self, node: NodeState, lq: int) -> float:
        spec  = TICNChannel.SPECS[node.channel]
        base  = spec['base_loss']
        # LQ 저하에 따른 손실 상승
        lq_loss = max(0.0, (65 - lq) / 65.0) * 0.45
        # 재밍 추가 손실
        now = time.time()
        jam_loss = 0.65 if (node.channel in self.jammed and self.jammed[node.channel] > now) else 0.0
        return min(0.95, base + lq_loss + jam_loss)

    def _log_event(self, ev: dict):
        self.events = [ev] + self.events[:49]

    # ── 채널 홉 ───────────────────────────────────────────────────────────

    def _try_hop(self, node: NodeState):
        """현재 채널이 재밍 중이면 다음 사용 가능한 채널로 전환"""
        now = time.time()
        if node.channel not in self.jammed or self.jammed[node.channel] <= now:
            # 재밍 없음 — VHF가 아니면 복귀 시도
            if node.channel != 'VHF' and ('VHF' not in self.jammed or self.jammed['VHF'] <= now):
                old = node.channel
                node.channel  = 'VHF'
                node.waveform = 'K-WNW/VHF'
                node.hop_count += 1
                ev = {"time": now, "platform": node.platform_id,
                      "event": "CHANNEL_RESTORE", "from": old, "to": "VHF", "reason": "JAM_CLEARED"}
                self._log_event(ev)
                print(f"[TICN] ✅ RESTORE  {node.platform_id}: {old} → VHF  (재밍 해제)")
            return

        # 재밍 탐지 → 다음 채널 탐색
        candidates = [c for c in TICNChannel.HOP_ORDER
                      if c != node.channel and (c not in self.jammed or self.jammed[c] <= now)]
        if not candidates:
            print(f"[TICN] ⚠️  {node.platform_id}: 사용 가능한 채널 없음!")
            return

        old = node.channel
        node.channel  = candidates[0]
        node.waveform = f'K-WNW/{node.channel}'
        node.hop_count += 1
        ev = {"time": now, "platform": node.platform_id,
              "event": "CHANNEL_HOP", "from": old, "to": node.channel, "reason": "JAM_DETECTED"}
        self._log_event(ev)
        print(f"[TICN] ⚡ HOP  {node.platform_id}: {old} → {node.channel}  (재밍 회피)")

    # ── 공개 API ──────────────────────────────────────────────────────────

    def process(self, payload: dict) -> dict | None:
        """
        패킷을 TICN 레이어 통과 처리.
        None 반환 → 패킷 드롭 (손실 시뮬레이션).
        """
        pid = payload.get('platform_id', 'UNKNOWN')
        with self.lock:
            node = self._node(pid)
            node.last_seen = time.time()
            node.rx_count += 1

            # 위치 갱신
            if payload.get('lat') is not None: node.lat = payload['lat']
            if payload.get('lon') is not None: node.lon = payload['lon']
            if payload.get('alt') is not None: node.alt = payload['alt']

            # 라우터까지 거리 계산
            if node.lat and node.lon:
                node.dist_km = self._haversine(ROUTER_LAT, ROUTER_LON, node.lat, node.lon)

            # 채널 홉 체크
            self._try_hop(node)

            # 링크 품질 계산
            lq = self._link_quality(node)
            node.link_quality = lq

            # 패킷 손실 시뮬레이션
            loss = self._loss_rate(node, lq)
            node.loss_rate_pct = round(loss * 100, 1)
            if random.random() < loss:
                node.drop_count += 1
                return None   # 드롭

            # TICN 메타데이터 주입
            payload['ticn'] = {
                'channel':      node.channel,
                'waveform':     node.waveform,
                'band':         TICNChannel.SPECS[node.channel]['band'],
                'link_quality': lq,
                'loss_rate':    node.loss_rate_pct,
                'dist_km':      round(node.dist_km, 2),
                'network':      'TICN',
            }
            return payload

    def jam(self, channel: str, duration_s: float = 30.0):
        """채널 재밍 주입 (공격 에이전트 또는 API 호출)"""
        with self.lock:
            self.jammed[channel] = time.time() + duration_s
            ev = {"time": time.time(), "event": "JAM_START",
                  "channel": channel, "duration_s": duration_s}
            self._log_event(ev)
            print(f"[TICN] 🔴 JAM  채널={channel}  {duration_s}s")

    def clear_jam(self, channel: str):
        with self.lock:
            self.jammed.pop(channel, None)
            print(f"[TICN] ✅ JAM CLEARED  채널={channel}")

    def status(self) -> dict:
        with self.lock:
            now = time.time()
            return {
                "nodes": {
                    pid: {
                        "channel":        n.channel,
                        "waveform":       n.waveform,
                        "band":           TICNChannel.SPECS[n.channel]['band'],
                        "link_quality":   n.link_quality,
                        "loss_rate":      n.loss_rate_pct,
                        "dist_km":        round(n.dist_km, 2),
                        "rx":             n.rx_count,
                        "drop":           n.drop_count,
                        "hops":           n.hop_count,
                        "online":         (now - n.last_seen) < 5,
                    } for pid, n in self.nodes.items()
                },
                "jammed_channels": {
                    ch: round(until - now, 1)
                    for ch, until in self.jammed.items() if until > now
                },
                "recent_events": self.events[:10],
            }


# ══════════════════════════════════════════════════════════════════════════════
# HTTP 상태 서버 (대시보드 조회용)
# ══════════════════════════════════════════════════════════════════════════════

def make_http_handler(ticn: TICNLayer):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass   # 액세스 로그 억제

        def _cors(self):
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Type", "application/json")

        def do_GET(self):
            if self.path in ("/api/ticn", "/api/ticn/status"):
                body = json.dumps(ticn.status()).encode()
                self.send_response(200); self._cors(); self.send_header("Content-Length", len(body))
                self.end_headers(); self.wfile.write(body)
            else:
                self.send_response(404); self.end_headers()

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body   = json.loads(self.rfile.read(length) or b'{}')

            if self.path == "/api/ticn/jam":
                # {"channel": "VHF", "duration": 30}
                ch  = body.get("channel", "VHF")
                dur = float(body.get("duration", 30))
                ticn.jam(ch, dur)
                resp = json.dumps({"ok": True, "channel": ch, "duration": dur}).encode()
                self.send_response(200); self._cors(); self.send_header("Content-Length", len(resp))
                self.end_headers(); self.wfile.write(resp)

            elif self.path == "/api/ticn/clear":
                ch = body.get("channel", "VHF")
                ticn.clear_jam(ch)
                resp = json.dumps({"ok": True, "channel": ch}).encode()
                self.send_response(200); self._cors(); self.send_header("Content-Length", len(resp))
                self.end_headers(); self.wfile.write(resp)

            else:
                self.send_response(404); self.end_headers()

        def do_OPTIONS(self):
            self.send_response(200); self._cors(); self.end_headers()

    return Handler


# ══════════════════════════════════════════════════════════════════════════════
# 메인
# ══════════════════════════════════════════════════════════════════════════════

def bind_udp(port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", port))
    return sock


def jam_listener(ticn: TICNLayer):
    """공격 에이전트 → UDP JAM 신호 수신"""
    sock = bind_udp(JAM_LISTEN_PORT)
    print(f"[TICN] JAM 수신 대기 :{JAM_LISTEN_PORT}")
    while True:
        try:
            data, addr = sock.recvfrom(1024)
            msg = json.loads(data.decode())
            ch  = msg.get("channel", "VHF")
            dur = float(msg.get("duration", 30))
            ticn.jam(ch, dur)
        except Exception as e:
            print(f"[TICN] JAM 파싱 오류: {e}")


def main():
    ticn = TICNLayer()

    # HTTP 상태 서버 (백그라운드)
    http_server = HTTPServer(("0.0.0.0", STATUS_PORT), make_http_handler(ticn))
    threading.Thread(target=http_server.serve_forever, daemon=True).start()
    print(f"[TICN] HTTP 상태 API  :{STATUS_PORT}  →  /api/ticn/status")

    # JAM UDP 리스너 (백그라운드)
    threading.Thread(target=jam_listener, args=(ticn,), daemon=True).start()

    # 메인 소켓
    cc_sock  = bind_udp(CC_LISTEN_PORT)
    ugv_sock = bind_udp(UGV_LISTEN_PORT)
    cmd_sock = bind_udp(CMD_LISTEN_PORT)
    out_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    inputs   = [cc_sock, ugv_sock, cmd_sock]

    print("[TICN] 전술 라우터 시작 ──────────────────────────────────")
    print(f"       CC(UAV)  UDP :{CC_LISTEN_PORT}")
    print(f"       UGV      UDP :{UGV_LISTEN_PORT}")
    print(f"       CMD      UDP :{CMD_LISTEN_PORT}")
    print(f"       Fan-out ×{len(FAN_OUT)}: " + ", ".join(f"{n}:{p}" for n,_,p in FAN_OUT))

    while True:
        readable, _, _ = select.select(inputs, [], [], 1)
        for sock in readable:
            data, addr = sock.recvfrom(8192)
            try:
                payload = json.loads(data.decode())
            except json.JSONDecodeError:
                print("[ROUTER] 잘못된 패킷 드롭")
                continue

            # ── Command 경로 (TICN 레이어 우회 — 명령은 손실 없이 전달)
            if sock is cmd_sock:
                payload["router_forwarded_at"] = time.time()
                payload["via"] = "dah-tactical-router/TICN"
                out_sock.sendto(json.dumps(payload).encode(), (CC_CMD_HOST, CC_CMD_PORT))
                print(f"[CMD] [{payload.get('command')}] {addr[0]} → CC:{CC_CMD_PORT}")
                continue

            # ── Telemetry 경로: TICN 레이어 통과
            payload["router_received_at"] = time.time()
            result = ticn.process(payload)

            if result is None:
                # 패킷 손실 시뮬레이션
                pid = payload.get('platform_id', '?')
                print(f"[TICN] DROP  {pid}  (링크 손실 시뮬레이션)")
                continue

            # fan-out
            failed = []
            for name, host, port in FAN_OUT:
                try:
                    out_sock.sendto(json.dumps(result).encode(), (host, port))
                except Exception:
                    failed.append(name)

            ticn_info = result.get('ticn', {})
            status_str = f"fan-out×{len(FAN_OUT) - len(failed)}"
            if failed:
                status_str += f"  (fail:{','.join(failed)})"
            print(
                f"[TICN] {result.get('platform_id')}  "
                f"ch={ticn_info.get('channel')}  "
                f"LQ={ticn_info.get('link_quality')}  "
                f"loss={ticn_info.get('loss_rate')}%  "
                f"{status_str}"
            )


if __name__ == "__main__":
    main()
