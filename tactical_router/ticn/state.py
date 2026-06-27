import threading
import time


class SharedState:
    """재밍 채널 상태 + 이벤트 로그 — TMMR / TICN 공유"""

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
