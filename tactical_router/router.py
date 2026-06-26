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

# TICN 라우터(GCS) 기준 위치 — 경기북부 작전지역
ROUTER_LAT = float(os.getenv("ROUTER_LAT", "37.85"))
ROUTER_LON = float(os.getenv("ROUTER_LON", "126.85"))

FAN_OUT = [
    ("Mission Control", MISSION_HOST,   MISSION_PORT),
    ("Collector",       COLLECTOR_HOST, COLLECTOR_PORT),
    ("GCS",             GCS_HOST,       GCS_PORT),
    ("Dashboard",       DASHBOARD_HOST, DASHBOARD_PORT),
]


# ══════════════════════════════════════════════════════════════════════════════
# TMMR — 전술 다대역 다기능 무전기 (노드별 무전기 레이어)
# ══════════════════════════════════════════════════════════════════════════════

class WaveformSpec:
    """TMMR 지원 파형 스펙 (K-WNW 시리즈 + 레거시)"""
    TABLE = {
        'K-WNW/VHF': {'band': '30-88 MHz',   'data_kbps': 512,  'max_range_km': 50,  'base_loss': 0.010, 'jam_resist': 'LOW'},
        'K-WNW/UHF': {'band': '225-512 MHz', 'data_kbps': 2048, 'max_range_km': 25,  'base_loss': 0.020, 'jam_resist': 'MEDIUM'},
        'K-WNW/HF':  {'band': '2-30 MHz',    'data_kbps': 64,   'max_range_km': 300, 'base_loss': 0.080, 'jam_resist': 'HIGH'},
    }
    PRIORITY = ['K-WNW/VHF', 'K-WNW/UHF', 'K-WNW/HF']   # 정상 운용 우선순위


class TMMRNode:
    """
    TMMR 무전기 — 노드별 SDR 레이어.
    역할: 파형 선택, RSSI 측정, 재밍 감지, 자동 채널홉, TX 전력 제어.
    """
    RSSI_JAM_THRESHOLD = -45   # dBm 이상이면 재밍 잡음으로 판단
    JAM_WINDOW         = 5     # 최근 N 패킷으로 재밍 판단

    def __init__(self, platform_id: str):
        self.platform_id  = platform_id
        self.waveform     = 'K-WNW/VHF'
        self.tx_power     = 80          # % (0–100)
        self.rssi         = -65.0       # dBm
        self._rssi_hist: list[float] = []
        self.jam_detected = False
        self.hop_count    = 0

    @property
    def channel(self) -> str:
        return self.waveform.split('/')[-1]   # 'K-WNW/VHF' → 'VHF'

    @property
    def spec(self) -> dict:
        return WaveformSpec.TABLE.get(self.waveform, WaveformSpec.TABLE['K-WNW/VHF'])

    def update_rssi(self, dist_km: float, alt_m: float, jammed: set[str]) -> float:
        """
        거리·고도 기반 RSSI 계산.
        재밍 채널이면 잡음 신호(높은 RSSI) 추가 — 감지 트리거.
        """
        # 자유공간 경로손실 (Friis 근사, 400 MHz 기준)
        freq_mhz = 400 if self.channel == 'UHF' else (60 if self.channel == 'VHF' else 10)
        path_loss = 20 * math.log10(max(0.1, dist_km) * 1000) + 20 * math.log10(freq_mhz) - 27.55
        rssi = -30 - path_loss + (alt_m / 600)   # 고도 보너스
        if self.channel in jammed:
            rssi += random.uniform(28, 42)        # 재밍 잡음 (강한 신호로 마스킹)
        rssi += random.gauss(0, 1.5)
        self.rssi = round(rssi, 1)

        self._rssi_hist.append(self.rssi)
        if len(self._rssi_hist) > self.JAM_WINDOW:
            self._rssi_hist.pop(0)
        avg = sum(self._rssi_hist) / len(self._rssi_hist)
        self.jam_detected = avg > self.RSSI_JAM_THRESHOLD
        return self.rssi

    def auto_hop(self, jammed: set[str], log_fn) -> bool:
        """
        재밍 감지 시 자동 파형 전환 (TMMR SDR 핵심 기능).
        재밍 해제 시 K-WNW/VHF 복귀.
        """
        if not self.jam_detected:
            if self.waveform != 'K-WNW/VHF' and 'VHF' not in jammed:
                old = self.waveform
                self.waveform = 'K-WNW/VHF'
                self._rssi_hist.clear()
                self.hop_count += 1
                log_fn({"layer": "TMMR", "event": "WAVEFORM_RESTORE",
                        "platform": self.platform_id, "from": old, "to": self.waveform,
                        "reason": "JAM_CLEARED"})
                print(f"[TMMR] ✅ RESTORE  {self.platform_id}: {old} → {self.waveform}")
            return False

        candidates = [w for w in WaveformSpec.PRIORITY
                      if w != self.waveform and w.split('/')[-1] not in jammed]
        if not candidates:
            print(f"[TMMR] ⚠️  {self.platform_id}: 전환 가능한 파형 없음")
            return False

        old = self.waveform
        self.waveform = candidates[0]
        self._rssi_hist.clear()
        self.hop_count += 1
        log_fn({"layer": "TMMR", "event": "WAVEFORM_HOP",
                "platform": self.platform_id, "from": old, "to": self.waveform,
                "reason": "JAM_DETECTED", "rssi_dbm": self.rssi})
        print(f"[TMMR] ⚡ HOP  {self.platform_id}: {old} → {self.waveform}  RSSI={self.rssi}dBm")
        return True

    def adjust_tx_power(self, dist_km: float):
        """거리 비례 TX 출력 자동 조절 — 근거리 전력 절감, 원거리 출력 증가"""
        ratio  = dist_km / max(1, self.spec['max_range_km'])
        target = max(20, min(100, int(ratio * 80 + 25)))
        if abs(target - self.tx_power) > 5:
            self.tx_power = target

    def to_dict(self) -> dict:
        return {
            'waveform':     self.waveform,
            'channel':      self.channel,
            'band':         self.spec['band'],
            'data_kbps':    self.spec['data_kbps'],
            'tx_power_pct': self.tx_power,
            'rssi_dbm':     self.rssi,
            'jam_detected': self.jam_detected,
            'hop_count':    self.hop_count,
        }


# ══════════════════════════════════════════════════════════════════════════════
# TICN — 전술정보통신망 (망 레이어)
# ══════════════════════════════════════════════════════════════════════════════

class LinkState:
    """TICN 링크 상태 테이블 엔트리 (OLSR 링크 상태 모사)"""
    TIMEOUT_S = 10

    def __init__(self):
        self.quality    = 100
        self.loss_pct   = 0.0
        self.dist_km    = 0.0
        self.updated_at = time.time()

    @property
    def active(self) -> bool:
        return (time.time() - self.updated_at) < self.TIMEOUT_S

    @property
    def cost(self) -> float:
        """라우팅 비용 — OLSR ETX(Expected Transmission Count) 모사"""
        return max(1.0, (100 - self.quality) / 10 + self.loss_pct / 5)


class TICNNetwork:
    """
    TICN 전술정보통신망 시뮬레이션.
    - OLSR 링크 상태 테이블 관리
    - QoS: 명령(command) 패킷 손실률 우선 저감
    - 패킷 손실 시뮬레이션 (링크 품질 + TMMR 상태 반영)
    - 망 이벤트 로그 (50건)
    """
    QOS_CMD_LOSS_FACTOR = 0.08   # 명령 패킷: 손실률 × 8% (우선 전달)

    def __init__(self):
        self.links:  dict[str, LinkState] = {}
        self.events: list[dict]           = []
        self.rx_total   = 0
        self.drop_total = 0
        self.lock = threading.Lock()

    def log(self, ev: dict):
        ev.setdefault('time', time.time())
        self.events = [ev] + self.events[:49]

    def update_link(self, platform_id: str, dist_km: float, tmmr: TMMRNode):
        """TMMR 상태 + 거리로 TICN 링크 품질 갱신"""
        with self.lock:
            lnk = self.links.setdefault(platform_id, LinkState())
            spec = tmmr.spec

            # 거리 감쇄 (비선형)
            range_f = max(0.0, 1.0 - (dist_km / spec['max_range_km']) ** 1.5)
            # RSSI 정규화 (-100 dBm → 0, -40 dBm → 1)
            rssi_f  = max(0.0, min(1.0, (tmmr.rssi + 100) / 60))
            # TX 출력 반영
            power_f = tmmr.tx_power / 100

            lq = int((range_f * 0.5 + rssi_f * 0.35 + power_f * 0.15) * 100)
            lq = max(5, min(100, lq + int(random.gauss(0, 2))))

            lnk.quality    = lq
            lnk.dist_km    = round(dist_km, 2)
            lnk.loss_pct   = round(max(0.0, (65 - lq) / 65) * 40 + spec['base_loss'] * 100, 1)
            lnk.updated_at = time.time()

    def route(self, payload: dict, tmmr: TMMRNode) -> dict | None:
        """
        TICN 망 라우팅 처리.
        반환 None → 패킷 드롭.
        """
        platform_id = payload.get('platform_id', 'UNKNOWN')
        pkt_type    = payload.get('type', 'telemetry')

        with self.lock:
            self.rx_total += 1
            lnk = self.links.get(platform_id)

            loss = (lnk.loss_pct / 100) if lnk else 0.01
            if pkt_type == 'command':
                loss *= self.QOS_CMD_LOSS_FACTOR   # QoS 우선
            if tmmr.jam_detected:
                loss = min(0.92, loss + 0.55)      # 재밍 중 대역폭 붕괴

            if random.random() < loss:
                self.drop_total += 1
                return None

            # 패킷에 TMMR + TICN 메타데이터 주입
            payload['tmmr'] = tmmr.to_dict()
            payload['ticn'] = {
                'network':      'TICN',
                'link_quality': lnk.quality   if lnk else 100,
                'loss_pct':     lnk.loss_pct  if lnk else 0.0,
                'dist_km':      lnk.dist_km   if lnk else 0.0,
                'link_cost':    round(lnk.cost, 2) if lnk else 1.0,
                'rx_total':     self.rx_total,
                'drop_total':   self.drop_total,
            }
            return payload

    def status(self) -> dict:
        with self.lock:
            return {
                'links': {
                    pid: {
                        'quality':  l.quality,
                        'loss_pct': l.loss_pct,
                        'dist_km':  l.dist_km,
                        'cost':     round(l.cost, 2),
                        'active':   l.active,
                    } for pid, l in self.links.items()
                },
                'rx_total':   self.rx_total,
                'drop_total': self.drop_total,
            }


# ══════════════════════════════════════════════════════════════════════════════
# 공유 상태 — 재밍 채널 집합 + 이벤트 로그
# ══════════════════════════════════════════════════════════════════════════════

class SharedState:
    def __init__(self):
        self.jammed: dict[str, float] = {}   # channel → jam_until (epoch)
        self.events: list[dict]       = []
        self.lock = threading.Lock()

    def jam(self, channel: str, duration_s: float):
        with self.lock:
            self.jammed[channel] = time.time() + duration_s
            ev = {"layer": "TICN", "event": "JAM_START",
                  "channel": channel, "duration_s": duration_s, "time": time.time()}
            self.events = [ev] + self.events[:49]
            print(f"[TICN]  🔴 JAM  채널={channel}  {duration_s}s")

    def clear_jam(self, channel: str):
        with self.lock:
            self.jammed.pop(channel, None)
            print(f"[TICN]  ✅ CLEAR  채널={channel}")

    def active_jammed(self) -> set[str]:
        now = time.time()
        with self.lock:
            return {ch for ch, until in self.jammed.items() if until > now}

    def jammed_remaining(self) -> dict:
        now = time.time()
        with self.lock:
            return {ch: round(until - now, 1)
                    for ch, until in self.jammed.items() if until > now}

    def log(self, ev: dict):
        ev.setdefault('time', time.time())
        with self.lock:
            self.events = [ev] + self.events[:49]

    def recent_events(self, n: int = 10) -> list:
        with self.lock:
            return self.events[:n]


# ══════════════════════════════════════════════════════════════════════════════
# HTTP 상태 API
# ══════════════════════════════════════════════════════════════════════════════

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
                payload = {
                    "tmmr": {
                        pid: node.to_dict()
                        for pid, node in tmmr_nodes.items()
                    },
                    "ticn": ticn.status(),
                    "jammed_channels": shared.jammed_remaining(),
                    "recent_events":   shared.recent_events(15),
                }
                self._send(200, json.dumps(payload).encode())
            else:
                self._send(404, b'{"error":"not found"}')

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body   = json.loads(self.rfile.read(length) or b'{}')

            if self.path == "/api/ticn/jam":
                ch  = body.get("channel", "VHF")
                dur = float(body.get("duration", 30))
                shared.jam(ch, dur)
                self._send(200, json.dumps({"ok": True, "channel": ch, "duration": dur}).encode())

            elif self.path == "/api/ticn/clear":
                ch = body.get("channel", "VHF")
                shared.clear_jam(ch)
                self._send(200, json.dumps({"ok": True, "channel": ch}).encode())

            else:
                self._send(404, b'{"error":"not found"}')

    return Handler


# ══════════════════════════════════════════════════════════════════════════════
# 메인
# ══════════════════════════════════════════════════════════════════════════════

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
    """공격 에이전트 UDP JAM 신호 수신"""
    sock = bind_udp(JAM_LISTEN_PORT)
    print(f"[TICN]  JAM 수신 대기  :{JAM_LISTEN_PORT}")
    while True:
        try:
            data, _ = sock.recvfrom(1024)
            msg = json.loads(data.decode())
            shared.jam(msg.get("channel", "VHF"), float(msg.get("duration", 30)))
        except Exception as e:
            print(f"[TICN]  JAM 파싱 오류: {e}")


def main():
    shared     = SharedState()
    tmmr_nodes: dict[str, TMMRNode] = {}
    ticn       = TICNNetwork()

    # HTTP 상태 API
    http_srv = HTTPServer(("0.0.0.0", STATUS_PORT), make_http_handler(shared, tmmr_nodes, ticn))
    threading.Thread(target=http_srv.serve_forever, daemon=True).start()
    print(f"[TICN]  HTTP API  :{STATUS_PORT}  →  /api/ticn/status")

    # JAM UDP 리스너
    threading.Thread(target=jam_udp_listener, args=(shared,), daemon=True).start()

    # 소켓 바인딩
    cc_sock  = bind_udp(CC_LISTEN_PORT)
    ugv_sock = bind_udp(UGV_LISTEN_PORT)
    cmd_sock = bind_udp(CMD_LISTEN_PORT)
    out_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    print("[ROUTER] ── TICN 전술 라우터 시작 ──────────────────────────")
    print(f"         CC(UAV)  :{CC_LISTEN_PORT}  /  UGV :{UGV_LISTEN_PORT}  /  CMD :{CMD_LISTEN_PORT}")
    print(f"         Fan-out ×{len(FAN_OUT)}: " + ", ".join(f"{n}:{p}" for n,_,p in FAN_OUT))

    while True:
        readable, _, _ = select.select([cc_sock, ugv_sock, cmd_sock], [], [], 1)
        for sock in readable:
            data, addr = sock.recvfrom(8192)
            try:
                payload = json.loads(data.decode())
            except json.JSONDecodeError:
                print("[ROUTER] 잘못된 패킷 드롭")
                continue

            # ── Command 경로 (QoS 우선 — TICN 손실 최소화)
            if sock is cmd_sock:
                payload.update({"router_forwarded_at": time.time(), "via": "TICN/TMMR"})
                out_sock.sendto(json.dumps(payload).encode(), (CC_CMD_HOST, CC_CMD_PORT))
                print(f"[CMD]  [{payload.get('command')}]  {addr[0]} → CC:{CC_CMD_PORT}")
                continue

            # ── Telemetry 경로: TMMR → TICN 처리
            payload["router_received_at"] = time.time()
            pid = payload.get('platform_id', 'UNKNOWN')

            # TMMR 노드 획득/생성
            if pid not in tmmr_nodes:
                tmmr_nodes[pid] = TMMRNode(pid)
            tmmr = tmmr_nodes[pid]

            # 거리 계산
            lat = payload.get('lat') or 0
            lon = payload.get('lon') or 0
            alt = payload.get('alt') or 0
            dist_km = haversine(ROUTER_LAT, ROUTER_LON, lat, lon) if (lat and lon) else 0.0

            # ── TMMR 레이어 처리
            jammed = shared.active_jammed()
            tmmr.update_rssi(dist_km, alt, jammed)
            tmmr.auto_hop(jammed, lambda ev: (shared.log(ev), ticn.log(ev)))
            tmmr.adjust_tx_power(dist_km)

            # ── TICN 레이어 처리
            ticn.update_link(pid, dist_km, tmmr)
            result = ticn.route(payload, tmmr)

            if result is None:
                print(f"[TICN]  DROP  {pid}  LQ={ticn.links.get(pid, LinkState()).quality}  jam={tmmr.jam_detected}")
                continue

            # fan-out
            failed = []
            for name, host, port in FAN_OUT:
                try:
                    out_sock.sendto(json.dumps(result).encode(), (host, port))
                except Exception:
                    failed.append(name)

            t = result.get('tmmr', {})
            n = result.get('ticn', {})
            status = f"fan-out×{len(FAN_OUT)-len(failed)}" + (f" fail:{','.join(failed)}" if failed else "")
            print(
                f"[TICN]  {pid}  "
                f"wf={t.get('waveform')}  RSSI={t.get('rssi_dbm')}dBm  "
                f"TX={t.get('tx_power_pct')}%  LQ={n.get('link_quality')}  "
                f"loss={n.get('loss_pct')}%  dist={n.get('dist_km')}km  {status}"
            )


if __name__ == "__main__":
    main()
