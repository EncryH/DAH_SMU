import math
import random


class WaveformSpec:
    """TMMR 지원 파형 스펙 (K-WNW 시리즈)"""
    TABLE = {
        'K-WNW/VHF': {'band': '30-88 MHz',   'data_kbps': 512,  'max_range_km': 50,  'base_loss': 0.010, 'jam_resist': 'LOW'},
        'K-WNW/UHF': {'band': '225-512 MHz', 'data_kbps': 2048, 'max_range_km': 25,  'base_loss': 0.020, 'jam_resist': 'MEDIUM'},
        'K-WNW/HF':  {'band': '2-30 MHz',    'data_kbps': 64,   'max_range_km': 300, 'base_loss': 0.080, 'jam_resist': 'HIGH'},
    }
    PRIORITY = ['K-WNW/VHF', 'K-WNW/UHF', 'K-WNW/HF']


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
        self.tx_power     = 80
        self.rssi         = -65.0
        self._rssi_hist: list[float] = []
        self.jam_detected = False
        self.hop_count    = 0

    @property
    def channel(self) -> str:
        return self.waveform.split('/')[-1]

    @property
    def spec(self) -> dict:
        return WaveformSpec.TABLE.get(self.waveform, WaveformSpec.TABLE['K-WNW/VHF'])

    def update_rssi(self, dist_km: float, alt_m: float, jammed: set[str]) -> float:
        """거리·고도 기반 RSSI 계산. 재밍 채널이면 잡음 신호 추가."""
        freq_mhz = 400 if self.channel == 'UHF' else (60 if self.channel == 'VHF' else 10)
        path_loss = 20 * math.log10(max(0.1, dist_km) * 1000) + 20 * math.log10(freq_mhz) - 27.55
        rssi = -30 - path_loss + (alt_m / 600)
        if self.channel in jammed:
            rssi += random.uniform(28, 42)   # 재밍 잡음
        rssi += random.gauss(0, 1.5)
        self.rssi = round(rssi, 1)

        self._rssi_hist.append(self.rssi)
        if len(self._rssi_hist) > self.JAM_WINDOW:
            self._rssi_hist.pop(0)
        avg = sum(self._rssi_hist) / len(self._rssi_hist)
        self.jam_detected = avg > self.RSSI_JAM_THRESHOLD
        return self.rssi

    def auto_hop(self, jammed: set[str], log_fn) -> bool:
        """재밍 감지 시 자동 파형 전환. 재밍 해제 시 K-WNW/VHF 복귀."""
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
        """거리 비례 TX 출력 자동 조절."""
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
