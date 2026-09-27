"""投放排他：同一玩具不能同时出现在两个区域；并发投放仅一人成功。"""
from __future__ import annotations

import threading
import unittest
from datetime import timedelta

from app.domain import DomainError
from app.services import deployment
from tests.helpers import T0, BaseCase


class TestDeploymentExclusivity(BaseCase):

    def test_same_toy_cannot_be_in_two_zones(self):
        tid = self.add_toy("TOY-T01", age_min=0, age_max=12)  # 年龄跨两个区
        with self.db.connect() as conn:
            deployment.deploy_toy(conn, tid, 1, self.nurse, now=T0)
            # 已投到A区，再投B区必须失败
            with self.assertRaises(DomainError) as cm:
                deployment.deploy_toy(conn, tid, 2, self.nurse, now=T0)
            self.assertEqual(cm.exception.code, "deploy_conflict")
        self.assertEqual(self.toy_status(tid), "deployed")
        with self.db.connect() as conn:
            summary = {z["zone_id"]: z for z in deployment.zone_summary(conn)}
        self.assertEqual(summary[1]["available"], 1)
        self.assertEqual(summary[2]["available"], 0)
        # 全库只有一条进行中投放
        with self.db.connect() as conn:
            n = conn.execute(
                "SELECT COUNT(*) c FROM deployments WHERE toy_id=? AND status='active'",
                (tid,)).fetchone()["c"]
        self.assertEqual(n, 1)

    def test_concurrent_deploy_only_one_succeeds(self):
        tid = self.add_toy("TOY-T02", age_min=0, age_max=12)
        barrier = threading.Barrier(2)
        results = {"ok": 0, "conflict": 0, "other": []}
        lock = threading.Lock()

        def worker(zone_id):
            try:
                with self.db.connect() as conn:
                    barrier.wait(timeout=5)  # 同时到达临界点
                    deployment.deploy_toy(conn, tid, zone_id, self.nurse, now=T0)
                with lock:
                    results["ok"] += 1
            except DomainError as e:
                with lock:
                    if e.code == "deploy_conflict":
                        results["conflict"] += 1
                    else:
                        results["other"].append(str(e))
            except Exception as e:  # noqa: BLE001
                with lock:
                    results["other"].append(repr(e))

        t1 = threading.Thread(target=worker, args=(1,))
        t2 = threading.Thread(target=worker, args=(2,))
        t1.start(); t2.start(); t1.join(10); t2.join(10)

        self.assertEqual(results["other"], [])
        self.assertEqual(results["ok"], 1, "并发投放必须仅一人成功")
        self.assertEqual(results["conflict"], 1)
        # 周转流水中只有一条投放记录
        with self.db.connect() as conn:
            n = conn.execute(
                "SELECT COUNT(*) c FROM turnover_events WHERE toy_id=? AND to_status='deployed'",
                (tid,)).fetchone()["c"]
        self.assertEqual(n, 1)

    def test_capacity_and_age_mismatch(self):
        # 年龄不匹配 → 玩具错区异常
        tid = self.add_toy("TOY-T03", age_min=8, age_max=12)
        with self.db.connect() as conn:
            with self.assertRaises(DomainError) as cm:
                deployment.deploy_toy(conn, tid, 1, self.nurse, now=T0)  # A区0-3岁
            self.assertEqual(cm.exception.code, "wrong_zone")
        self.assertEqual(len(self.exceptions("wrong_zone")), 1)
        self.assertEqual(self.toy_status(tid), "in_storage")

        # 容量：A区容量2，第3件投放失败
        ids = [self.add_toy(f"TOY-C{i}") for i in range(3)]
        with self.db.connect() as conn:
            deployment.deploy_toy(conn, ids[0], 1, self.nurse, now=T0)
            deployment.deploy_toy(conn, ids[1], 1, self.nurse, now=T0)
            with self.assertRaises(DomainError) as cm:
                deployment.deploy_toy(conn, ids[2], 1, self.nurse, now=T0)
            self.assertEqual(cm.exception.code, "capacity")

    def test_front_desk_cannot_deploy(self):
        tid = self.add_toy("TOY-T04")
        with self.db.connect() as conn:
            with self.assertRaises(DomainError) as cm:
                deployment.deploy_toy(conn, tid, 1, self.front_desk, now=T0)
            self.assertEqual(cm.exception.code, "forbidden")


if __name__ == "__main__":
    unittest.main()
