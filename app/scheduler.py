"""进程内调度任务：干燥完成时点与轮换时点的周期计算。

- 干燥到期的玩具自动回到清洁库（干燥计时）
- 超过轮换时点+宽限仍未回收扫描的投放记“回收漏扫”异常
"""
from __future__ import annotations

import logging
import threading
from typing import Optional

from .db import Database
from .services.drying import complete_due_drying
from .services.recovery import flag_missed_scans

log = logging.getLogger("scheduler")


class Scheduler:
    def __init__(self, db: Database, interval: float = 5.0,
                 missed_scan_grace_minutes: int = 15):
        self.db = db
        self.interval = interval
        self.grace = missed_scan_grace_minutes
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def tick(self) -> dict:
        """执行一轮巡检（也可由测试直接调用）。"""
        with self.db.connect() as conn:
            dried = complete_due_drying(conn)
            missed = flag_missed_scans(conn, grace_minutes=self.grace)
        if dried or missed:
            log.info("调度巡检：干燥完成 %d 件，漏扫标记 %d 条", dried, missed)
        return {"drying_completed": dried, "missed_scan_flagged": missed}

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self.tick()
            except Exception:  # 调度器不因单次异常退出
                log.exception("调度巡检失败")

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="toy-scheduler",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
