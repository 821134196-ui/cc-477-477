"""干燥边界：未满规定时长拦截（干燥提前异常），到期放行，调度器按时回库。"""
from __future__ import annotations

import unittest
from datetime import timedelta

from app.domain import DomainError
from app.services import disinfection, drying
from tests.helpers import T0, BaseCase


class TestDryingBoundary(BaseCase):

    def _make_drying(self, code="TOY-D01", material="plastic"):
        """plastic 规定干燥 60 分钟；T0+50 消毒完成 → 到期 T0+50+60min。"""
        tid = self.make_cleaned_toy(code, material)
        submit_at = T0 + timedelta(minutes=50)
        with self.db.connect() as conn:
            b = disinfection.create_batch(conn, material,
                                          "soak" if material != "wood" else "wipe",
                                          [tid], self.cleaner, now=T0 + timedelta(minutes=45))
            disinfection.submit_batch(conn, b["batch_id"], self.cleaner, self.adapter,
                                      now=submit_at)
        self.assertEqual(self.toy_status(tid), "drying")
        return tid, submit_at

    def _drying_record(self, toy_id):
        with self.db.connect() as conn:
            return conn.execute(
                "SELECT * FROM drying_records WHERE toy_id=? AND status='drying'",
                (toy_id,)).fetchone()

    def test_early_release_blocked_and_recorded(self):
        tid, submit_at = self._make_drying()
        rec = self._drying_record(tid)
        early = submit_at + timedelta(minutes=59)  # 差1分钟
        with self.db.connect() as conn:
            with self.assertRaises(DomainError) as cm:
                drying.release_drying(conn, rec["id"], self.cleaner, now=early)
            self.assertEqual(cm.exception.code, "early_dry")
        self.assertEqual(len(self.exceptions("early_dry")), 1)
        self.assertEqual(self.toy_status(tid), "drying")  # 仍在干燥

    def test_release_exactly_at_due_succeeds(self):
        tid, submit_at = self._make_drying("TOY-D02")
        rec = self._drying_record(tid)
        due = submit_at + timedelta(minutes=60)  # 恰好满规定时长
        with self.db.connect() as conn:
            drying.release_drying(conn, rec["id"], self.cleaner, now=due)
        self.assertEqual(self.toy_status(tid), "in_storage")
        self.assertEqual(len(self.exceptions("early_dry")), 0)

    def test_scheduler_completes_only_due_records(self):
        tid, submit_at = self._make_drying("TOY-D03")
        # 到期前1分钟：调度不回库
        with self.db.connect() as conn:
            n = drying.complete_due_drying(conn, now=submit_at + timedelta(minutes=59))
        self.assertEqual(n, 0)
        self.assertEqual(self.toy_status(tid), "drying")
        # 到期时刻：自动回库
        with self.db.connect() as conn:
            n = drying.complete_due_drying(conn, now=submit_at + timedelta(minutes=60))
        self.assertEqual(n, 1)
        self.assertEqual(self.toy_status(tid), "in_storage")
        # 周转流水含干燥回库事件
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM turnover_events WHERE toy_id=? AND to_status='in_storage'",
                (tid,)).fetchone()
        self.assertIsNotNone(row)

    def test_drying_list_remaining_time(self):
        tid, submit_at = self._make_drying("TOY-D04")
        with self.db.connect() as conn:
            lst = drying.list_drying(conn, now=submit_at + timedelta(minutes=30))
        self.assertEqual(len(lst), 1)
        self.assertEqual(lst[0]["remaining_seconds"], 30 * 60)


if __name__ == "__main__":
    unittest.main()
