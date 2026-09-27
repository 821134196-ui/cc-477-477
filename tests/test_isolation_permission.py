"""隔离权限：仅护士长可解除；破损未修复不可解除；前台只读可用数量。"""
from __future__ import annotations

import unittest
from datetime import timedelta

from app.api import Api
from app.domain import DomainError
from app.services import damage, deployment, isolation
from tests.helpers import T0, BaseCase


class TestIsolationPermission(BaseCase):

    def _isolated_toy(self, code="TOY-S01"):
        tid = self.add_toy(code)
        with self.db.connect() as conn:
            deployment.deploy_toy(conn, tid, 1, self.nurse, now=T0)
            rep = damage.report_damage(conn, tid, "sharp_edge", "锐边", None,
                                       self.nurse, now=T0 + timedelta(minutes=10))
        return tid, rep["report_id"]

    def test_only_head_nurse_can_release(self):
        tid, rep_id = self._isolated_toy()
        with self.db.connect() as conn:
            damage.start_repair(conn, rep_id, self.cleaner, now=T0 + timedelta(minutes=20))
            damage.complete_repair(conn, rep_id, "已打磨", self.cleaner,
                                   now=T0 + timedelta(minutes=40))
        iso_id = self.active_isolations(tid)[0]["id"]
        # 护士、保洁都不能解除
        for staff in (self.nurse, self.cleaner):
            with self.db.connect() as conn:
                with self.assertRaises(DomainError) as cm:
                    isolation.release_isolation(conn, iso_id, staff, now=T0)
                self.assertEqual(cm.exception.code, "forbidden")
        self.assertEqual(self.toy_status(tid), "isolated")
        # 护士长可以解除
        with self.db.connect() as conn:
            isolation.release_isolation(conn, iso_id, self.head_nurse,
                                        "复核通过", now=T0 + timedelta(minutes=50))
        self.assertEqual(self.toy_status(tid), "pending_clean")  # 回待清洁重新走工序
        self.assertEqual(len(self.active_isolations(tid)), 0)

    def test_unrepaired_damage_cannot_be_released(self):
        tid, rep_id = self._isolated_toy("TOY-S02")
        iso_id = self.active_isolations(tid)[0]["id"]
        with self.db.connect() as conn:
            with self.assertRaises(DomainError) as cm:
                isolation.release_isolation(conn, iso_id, self.head_nurse, now=T0)
            self.assertEqual(cm.exception.code, "not_repaired")
        self.assertEqual(self.toy_status(tid), "isolated")

    def test_front_desk_api_readonly(self):
        self._isolated_toy("TOY-S03")
        api = Api(self.db, self.adapter, self.tmp.name)
        # 前台可看候诊区汇总（可用数量与轮换时间）
        status, body = api.dispatch("GET", "/api/zones/summary", {}, {}, self.front_desk)
        self.assertEqual(status, 200)
        self.assertIn("available", body["zones"][0])
        self.assertIn("next_rotation_at", body["zones"][0])
        # 其余接口一律 403
        for method, path in [("GET", "/api/toys"), ("GET", "/api/isolations"),
                             ("GET", "/api/exceptions"), ("POST", "/api/deploy"),
                             ("GET", "/api/batches")]:
            status, _ = api.dispatch(method, path, {}, {}, self.front_desk)
            self.assertEqual(status, 403, f"{path} 应对前台禁止")
        # 护士长不受限
        status, _ = api.dispatch("GET", "/api/isolations", {}, {}, self.head_nurse)
        self.assertEqual(status, 200)

    def test_api_isolation_release_permission(self):
        tid, rep_id = self._isolated_toy("TOY-S04")
        with self.db.connect() as conn:
            damage.start_repair(conn, rep_id, self.cleaner, now=T0 + timedelta(minutes=20))
            damage.complete_repair(conn, rep_id, "已修复", self.cleaner,
                                   now=T0 + timedelta(minutes=30))
        iso_id = self.active_isolations(tid)[0]["id"]
        api = Api(self.db, self.adapter, self.tmp.name)
        # 护士通过 API 解除 → 403
        status, _ = api.dispatch("POST", f"/api/isolations/{iso_id}/release",
                                 {}, {}, self.nurse)
        self.assertEqual(status, 403)
        # 护士长 → 200
        status, _ = api.dispatch("POST", f"/api/isolations/{iso_id}/release",
                                 {}, {}, self.head_nurse)
        self.assertEqual(status, 200)


if __name__ == "__main__":
    unittest.main()
