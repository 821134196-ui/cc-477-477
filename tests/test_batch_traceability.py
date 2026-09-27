"""批次追溯：失败批次全链可查；污染批次批量隔离；漏扫巡检标记。"""
from __future__ import annotations

import unittest
from datetime import timedelta

from app.domain import DomainError
from app.services import deployment, disinfection, isolation, recovery
from tests.helpers import T0, BaseCase


class TestBatchTraceability(BaseCase):

    def _batch_of_three(self, script=None):
        ids = [self.make_cleaned_toy(f"TOY-B{i}", material="silicone") for i in (1, 2, 3)]
        with self.db.connect() as conn:
            b = disinfection.create_batch(conn, "silicone", "soak", ids,
                                          self.cleaner, now=T0 + timedelta(minutes=45))
            if script:
                self.adapter.script[b["code"]] = script
            disinfection.submit_batch(conn, b["batch_id"], self.cleaner, self.adapter,
                                      now=T0 + timedelta(minutes=50))
        return b["batch_id"], b["code"], ids

    def test_contamination_batch_isolates_all_and_traces(self):
        batch_id, code, ids = self._batch_of_three(
            {"result": "failed", "contamination": True})
        # 三件全部隔离，隔离单挂批次号
        for tid in ids:
            self.assertEqual(self.toy_status(tid), "isolated")
            iso = self.active_isolations(tid)
            self.assertEqual(len(iso), 1)
            self.assertEqual(iso[0]["batch_id"], batch_id)
        # 异常：消毒失败 + 批量隔离
        self.assertEqual(len(self.exceptions("disinfection_failed")), 1)
        exc = self.exceptions("batch_isolation")
        self.assertEqual(len(exc), 1)
        self.assertEqual(exc[0]["batch_id"], batch_id)
        # 批次状态 isolated，追溯链完整
        with self.db.connect() as conn:
            trace = disinfection.trace_batch(conn, batch_id)
        self.assertEqual(trace["batch"]["status"], "isolated")
        self.assertEqual(len(trace["toys"]), 3)
        self.assertEqual(len(trace["isolations"]), 3)
        self.assertEqual(len(trace["exceptions"]), 2)
        self.assertEqual(trace["batch"]["code"], code)
        # 隔离玩具不能投放
        with self.db.connect() as conn:
            with self.assertRaises(DomainError):
                deployment.deploy_toy(conn, ids[0], 1, self.nurse, now=T0)

    def test_plain_failure_returns_to_pending_clean(self):
        batch_id, _, ids = self._batch_of_three(
            {"result": "failed", "contamination": False})
        for tid in ids:
            self.assertEqual(self.toy_status(tid), "pending_clean")
            self.assertEqual(len(self.active_isolations(tid)), 0)
        self.assertEqual(len(self.exceptions("disinfection_failed")), 1)
        self.assertEqual(len(self.exceptions("batch_isolation")), 0)
        with self.db.connect() as conn:
            trace = disinfection.trace_batch(conn, batch_id)
        self.assertEqual(trace["batch"]["status"], "failed")

    def test_batch_release_by_head_nurse_only(self):
        batch_id, _, ids = self._batch_of_three(
            {"result": "failed", "contamination": True})
        with self.db.connect() as conn:
            # 保洁不能批量解除
            with self.assertRaises(DomainError) as cm:
                isolation.release_batch_isolation(conn, batch_id, self.cleaner, now=T0)
            self.assertEqual(cm.exception.code, "forbidden")
            # 护士长批量解除 → 全部回待清洁
            r = isolation.release_batch_isolation(conn, batch_id, self.head_nurse,
                                                  now=T0 + timedelta(hours=1))
        self.assertEqual(r["released"], 3)
        for tid in ids:
            self.assertEqual(self.toy_status(tid), "pending_clean")
        with self.db.connect() as conn:
            trace = disinfection.trace_batch(conn, batch_id)
        self.assertTrue(all(i["status"] == "released" for i in trace["isolations"]))

    def test_missed_scan_flagged_once(self):
        tid = self.add_toy("TOY-B09")
        # 3小时前投放（轮换120分钟+宽限15分钟 → 漏扫）
        with self.db.connect() as conn:
            deployment.deploy_toy(conn, tid, 1, self.nurse,
                                  now=T0 - timedelta(hours=3))
            n = recovery.flag_missed_scans(conn, grace_minutes=15, now=T0)
        self.assertEqual(n, 1)
        self.assertEqual(len(self.exceptions("missed_scan")), 1)
        # 重复巡检不重复记录
        with self.db.connect() as conn:
            n = recovery.flag_missed_scans(conn, grace_minutes=15, now=T0)
        self.assertEqual(n, 0)
        self.assertEqual(len(self.exceptions("missed_scan")), 1)

    def test_success_batch_creates_drying_records(self):
        ids = [self.make_cleaned_toy(f"TOY-K{i}", material="plastic") for i in (1, 2)]
        with self.db.connect() as conn:
            b = disinfection.create_batch(conn, "plastic", "soak", ids,
                                          self.cleaner, now=T0 + timedelta(minutes=45))
            disinfection.submit_batch(conn, b["batch_id"], self.cleaner, self.adapter,
                                      now=T0 + timedelta(minutes=50))
            trace = disinfection.trace_batch(conn, b["batch_id"])
        self.assertEqual(trace["batch"]["status"], "completed")
        self.assertEqual(len(trace["drying_records"]), 2)
        self.assertTrue(all(t["status"] == "drying" for t in trace["toys"]))


if __name__ == "__main__":
    unittest.main()
