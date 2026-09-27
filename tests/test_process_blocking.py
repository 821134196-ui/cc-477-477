"""工序阻断：状态机强制流转顺序；普通清洁不能解除隔离；材质不兼容拦截。"""
from __future__ import annotations

import unittest
from datetime import timedelta

from app.domain import DomainError
from app.services import cleaning, damage, deployment, disinfection, recovery
from tests.helpers import T0, BaseCase


class TestProcessBlocking(BaseCase):

    def test_cannot_clean_deployed_toy(self):
        tid = self.add_toy("TOY-P01")
        with self.db.connect() as conn:
            deployment.deploy_toy(conn, tid, 1, self.nurse, now=T0)
            with self.assertRaises(DomainError) as cm:
                cleaning.clean_toy(conn, tid, "soak", self.cleaner, now=T0)
            self.assertEqual(cm.exception.code, "bad_transition")

    def test_cannot_deploy_dirty_toy(self):
        tid = self.add_toy("TOY-P02")
        with self.db.connect() as conn:
            deployment.deploy_toy(conn, tid, 1, self.nurse, now=T0)
            recovery.scan_recovery(conn, "TOY-P02", "BIN-A", self.nurse,
                                   now=T0 + timedelta(minutes=30))
            # 待清洁状态直接投放 → 阻断
            with self.assertRaises(DomainError) as cm:
                deployment.deploy_toy(conn, tid, 1, self.nurse, now=T0 + timedelta(minutes=31))
            self.assertEqual(cm.exception.code, "deploy_conflict")

    def test_cannot_batch_uncleaned_toy(self):
        tid = self.add_toy("TOY-P03")
        with self.db.connect() as conn:
            deployment.deploy_toy(conn, tid, 1, self.nurse, now=T0)
            recovery.scan_recovery(conn, "TOY-P03", "BIN-A", self.nurse,
                                   now=T0 + timedelta(minutes=30))
            # 未清洁直接组批 → 阻断（pending_clean → disinfecting 非法）
            with self.assertRaises(DomainError):
                disinfection.create_batch(conn, "plastic", "soak", [tid],
                                          self.cleaner, now=T0)

    def test_cannot_submit_batch_twice(self):
        tid = self.make_cleaned_toy("TOY-P04")
        with self.db.connect() as conn:
            b = disinfection.create_batch(conn, "plastic", "soak", [tid],
                                          self.cleaner, now=T0)
            disinfection.submit_batch(conn, b["batch_id"], self.cleaner, self.adapter,
                                      now=T0 + timedelta(minutes=50))
            with self.assertRaises(DomainError) as cm:
                disinfection.submit_batch(conn, b["batch_id"], self.cleaner, self.adapter,
                                          now=T0 + timedelta(minutes=51))
            self.assertEqual(cm.exception.code, "bad_transition")

    def test_material_incompatible_blocked_and_recorded(self):
        tid = self.add_toy("TOY-P05", material="wood")
        with self.db.connect() as conn:
            deployment.deploy_toy(conn, tid, 1, self.nurse, now=T0)
            recovery.scan_recovery(conn, "TOY-P05", "BIN-A", self.nurse,
                                   now=T0 + timedelta(minutes=30))
            # 木质不能浸泡
            with self.assertRaises(DomainError) as cm:
                cleaning.clean_toy(conn, tid, "soak", self.cleaner, now=T0)
            self.assertEqual(cm.exception.code, "material_incompatible")
            # 也不能机洗
            with self.assertRaises(DomainError):
                cleaning.clean_toy(conn, tid, "machine_wash", self.cleaner, now=T0)
            # 擦拭可以
            cleaning.clean_toy(conn, tid, "wipe", self.cleaner, now=T0)
        exc = self.exceptions("material_incompatible")
        self.assertEqual(len(exc), 2)
        self.assertEqual(self.toy_status(tid), "cleaned")

    def test_normal_cleaning_cannot_release_isolation(self):
        tid = self.add_toy("TOY-P06")
        with self.db.connect() as conn:
            deployment.deploy_toy(conn, tid, 1, self.nurse, now=T0)
            damage.report_damage(conn, tid, "sharp_edge", "边缘开裂", None,
                                 self.nurse, now=T0 + timedelta(minutes=10))
        self.assertEqual(self.toy_status(tid), "isolated")
        self.assertEqual(len(self.active_isolations(tid)), 1)
        with self.db.connect() as conn:
            # 普通清洁对隔离玩具无效
            with self.assertRaises(DomainError):
                cleaning.clean_toy(conn, tid, "soak", self.cleaner, now=T0)
        # 隔离仍然有效，状态未变
        self.assertEqual(self.toy_status(tid), "isolated")
        self.assertEqual(len(self.active_isolations(tid)), 1)

    def test_wrong_bin_recovery_recorded(self):
        tid = self.add_toy("TOY-P07")
        with self.db.connect() as conn:
            deployment.deploy_toy(conn, tid, 1, self.nurse, now=T0)
            # 投在A区却扫入B区回收箱 → 玩具错区异常，但仍正常回收
            recovery.scan_recovery(conn, "TOY-P07", "BIN-B", self.nurse,
                                   now=T0 + timedelta(minutes=30))
        self.assertEqual(len(self.exceptions("wrong_zone")), 1)
        self.assertEqual(self.toy_status(tid), "pending_clean")


if __name__ == "__main__":
    unittest.main()
