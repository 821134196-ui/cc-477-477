"""进程内调度：周期性计算干燥完成时点与候诊轮换时点。"""
from __future__ import annotations

import threading
import time


class RotationScheduler:
    """守护线程，每 interval 秒调用一次 service.tick()。"""

    def __init__(self, service, interval: float = 2.0):
        self.service = service
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="rotation-scheduler",
                                        daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self.service.tick()
            except Exception as exc:  # 调度异常不应杀死进程
                print(f"[scheduler] tick 出错: {exc}")

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
