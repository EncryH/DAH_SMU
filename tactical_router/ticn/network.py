import random
import threading
import time

from .tmmr import TMMRNode


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
        """라우팅 비용 — OLSR ETX 모사"""
        return max(1.0, (100 - self.quality) / 10 + self.loss_pct / 5)


class TICNNetwork:
    """
    TICN 전술정보통신망 — 망 레이어.
    - OLSR 링크 상태 테이블 관리
    - QoS: command 패킷 손실률 우선 저감
    - 패킷 손실 시뮬레이션 (링크 품질 + TMMR 상태 반영)
    """
    QOS_CMD_LOSS_FACTOR = 0.08   # 명령 패킷 손실률 × 8%

    def __init__(self):
        self.links: dict[str, LinkState] = {}
        self.events: list[dict]          = []
        self.rx_total   = 0
        self.drop_total = 0
        self.lock = threading.Lock()

    def log(self, ev: dict):
        ev.setdefault('time', time.time())
        with self.lock:
            self.events = [ev] + self.events[:49]

    def update_link(self, platform_id: str, dist_km: float, tmmr: TMMRNode):
        """TMMR 상태 + 거리로 TICN 링크 품질 갱신"""
        with self.lock:
            lnk  = self.links.setdefault(platform_id, LinkState())
            spec = tmmr.spec

            range_f = max(0.0, 1.0 - (dist_km / spec['max_range_km']) ** 1.5)
            rssi_f  = max(0.0, min(1.0, (tmmr.rssi + 100) / 60))
            power_f = tmmr.tx_power / 100

            lq = int((range_f * 0.5 + rssi_f * 0.35 + power_f * 0.15) * 100)
            lq = max(5, min(100, lq + int(random.gauss(0, 2))))

            lnk.quality    = lq
            lnk.dist_km    = round(dist_km, 2)
            lnk.loss_pct   = round(max(0.0, (65 - lq) / 65) * 40 + spec['base_loss'] * 100, 1)
            lnk.updated_at = time.time()

    def route(self, payload: dict, tmmr: TMMRNode) -> dict | None:
        """
        TICN 망 라우팅. None 반환 → 패킷 드롭.
        TMMR + TICN 메타데이터를 패킷에 주입.
        """
        platform_id = payload.get('platform_id', 'UNKNOWN')
        pkt_type    = payload.get('type', 'telemetry')

        with self.lock:
            self.rx_total += 1
            lnk  = self.links.get(platform_id)
            loss = (lnk.loss_pct / 100) if lnk else 0.01

            if pkt_type == 'command':
                loss *= self.QOS_CMD_LOSS_FACTOR
            if tmmr.jam_detected:
                loss = min(0.92, loss + 0.55)

            if random.random() < loss:
                self.drop_total += 1
                return None

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
