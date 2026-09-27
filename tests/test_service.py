"""测试：投放排他、工序阻断、干燥边界、隔离权限、批次追溯。

运行：python -m unittest discover -s tests -v
"""
from __future__ import annotations

import json
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from toyrotation import models as M
from toyrotation.adapters import MockSterilizerAdapter
from toyrotation.db import Database
from toyrotation.service import (
    PermissionDenied,
    ServiceError,
    ToyRotationService,
)


class Clock:
    """可控时钟。"""
    def __init__(self, start: datetime | None = None):
        self.t = start or datetime(2026, 9, 27, 9, 0, 0)

    def __call__(self) -> datetime:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += timedelta(seconds=seconds)

    def advance_minutes(self, minutes: float) -> None:
        self.advance(minutes * 60)


class ServiceCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.clock = Clock()
        # 并发测试需要文件库（WAL 才能体现立即事务）
        self.db_path = Path(self.tmp.name) / "test.db"
        self.db = Database(self.db_path)
        self.svc = ToyRotationService(
            self.db, adapter=MockSterilizerAdapter(),
            now=self.clock, photo_dir=Path(self.tmp.name) / "photos",
            minute_seconds=60)
        self.svc.add_zone("Z1", "候诊A区", 5)
        self.svc.add_zone("Z2", "候诊B区", 3)
        for tid, mat, parts in [("T1", M.MAT_PLASTIC, 1), ("T2", M.MAT_WOOD, 1),
                                ("T3", M.MAT_FABRIC, 4), ("T4", M.MAT_SILICONE, 2),
                                ("T5", M.MAT_METAL, 1), ("T6", M.MAT_PLASTIC, 3)]:
            self.svc.add_toy(tid, f"玩具{tid}", mat, 1, 5, parts_total=parts)

    def tearDown(self) -> None:
        self.db.close()
        self.tmp.cleanup()

    def _full_clean_cycle(self, toy_id: str, material: str) -> str:
        """投放 -> 回收 -> 组批 -> 消毒 -> 干燥完成，返回批次号。"""
        self.svc.deploy(toy_id, "Z1", "s")
        self.svc.scan_recycle(toy_id, "s")
        bid = self.svc.create_batch(material, "s", toy_ids=[toy_id])
        self.svc.run_disinfection(bid, "s")
        self.svc.start_drying(bid, "s")
        self.clock.advance_minutes(M.DRY_MINUTES[material])
        self.svc.complete_drying(bid, "s")
        return bid

    # ------------------------------------------------------------ 投放排他
    def test_concurrent_deploy_only_one_succeeds(self) -> None:
        n = 8
        barrier = threading.Barrier(n)
        results: list[bool] = [False] * n
        errors: list[str] = []

        def worker(i: int) -> None:
            try:
                barrier.wait()
                self.svc.deploy("T1", "Z1", f"员工{i}")
                results[i] = True
            except ServiceError as e:
                errors.append(e.code)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(sum(results), 1, "并发投放必须恰好一人成功")
        self.assertEqual(sum(results) + errors.count("already_deployed"), n)
        toy = self.svc.get_toy("T1")
        self.assertEqual(toy["status"], M.ST_DEPLOYED)
        self.assertEqual(toy["current_area"], M.AREA_WAITING)
        self.assertEqual(toy["zone_id"], "Z1")

    def test_toy_cannot_be_in_two_areas(self) -> None:
        self.svc.deploy("T1", "Z1", "s")
        # 同一件不能再投到 Z2
        with self.assertRaises(ServiceError) as cm:
            self.svc.deploy("T1", "Z2", "s2")
        self.assertEqual(cm.exception.code, "already_deployed")
        self.assertEqual(self.svc.get_toy("T1")["zone_id"], "Z1")

    def test_zone_capacity_enforced(self) -> None:
        # Z2 容量 3，投 3 件成功，第 4 件被拒
        for tid in ["T1", "T6", "T5"]:
            self.svc.deploy(tid, "Z2", "s")
        with self.assertRaises(ServiceError) as cm:
            self.svc.deploy("T4", "Z2", "s")
        self.assertEqual(cm.exception.code, "zone_full")

    # ------------------------------------------------------------ 工序阻断
    def test_process_gating_deploy_requires_clean_store(self) -> None:
        # 清洁库 -> 投放 OK
        self.svc.deploy("T1", "Z1", "s")
        # 候诊区玩具不能再次投放
        with self.assertRaises(ServiceError) as cm:
            self.svc.deploy("T1", "Z1", "s")
        self.assertEqual(cm.exception.code, "already_deployed")
        # 回收后必须经过清洁消毒干燥才能投放
        self.svc.scan_recycle("T1", "s")
        with self.assertRaises(ServiceError) as cm:
            self.svc.deploy("T1", "Z1", "s")
        self.assertEqual(cm.exception.code, "wrong_state")

    def test_drying_gating_before_disinfect(self) -> None:
        self.svc.deploy("T1", "Z1", "s")
        self.svc.scan_recycle("T1", "s")
        bid = self.svc.create_batch(M.MAT_PLASTIC, "s", toy_ids=["T1"])
        # 未消毒不能进入干燥
        with self.assertRaises(ServiceError) as cm:
            self.svc.start_drying(bid, "s")
        self.assertEqual(cm.exception.code, "batch_state")

    def test_empty_batch_cannot_disinfect(self) -> None:
        bid = self.svc.create_batch(M.MAT_PLASTIC, "s")
        with self.assertRaises(ServiceError) as cm:
            self.svc.run_disinfection(bid, "s")
        self.assertEqual(cm.exception.code, "empty_batch")

    def test_material_grouping_enforced(self) -> None:
        # 木质只能擦拭批次，不能加入塑料批次
        self.svc.deploy("T1", "Z1", "s")
        self.svc.scan_recycle("T1", "s")
        self.svc.deploy("T2", "Z1", "s")
        self.svc.scan_recycle("T2", "s")
        bid = self.svc.create_batch(M.MAT_PLASTIC, "s", toy_ids=["T1"])
        with self.assertRaises(ServiceError) as cm:
            self.svc.add_to_batch(bid, "T2", "s")
        self.assertEqual(cm.exception.code, "material_mismatch")

    def test_material_incompat_logged_and_rejected(self) -> None:
        # 木质误入机洗方式：拒绝且登记材质不兼容
        self.svc.deploy("T2", "Z1", "s")
        self.svc.scan_recycle("T2", "s")
        bid = self.svc.create_batch(M.MAT_WOOD, "s")  # 木质批次默认擦拭
        before = len(self.svc.incidents())
        with self.assertRaises(ServiceError) as cm:
            self.svc.add_to_batch(bid, "T2", "s", method=M.CLEAN_MACHINE)
        self.assertEqual(cm.exception.code, "material_incompat")
        kinds = [i["kind"] for i in self.svc.incidents()]
        self.assertIn(M.INC_MATERIAL_INCOMPAT, kinds)
        self.assertEqual(len(self.svc.incidents()), before + 1)
        # 被拒后玩具仍在回收箱，没有被错误拉入批次
        self.assertEqual(self.svc.get_toy("T2")["current_area"], M.AREA_RECYCLE)

    # ------------------------------------------------------------ 干燥边界
    def test_dry_boundary_one_second_early_rejected(self) -> None:
        self.svc.deploy("T1", "Z1", "s")
        self.svc.scan_recycle("T1", "s")
        bid = self.svc.create_batch(M.MAT_PLASTIC, "s", toy_ids=["T1"])
        self.svc.run_disinfection(bid, "s")
        self.svc.start_drying(bid, "s")

        # 差 1 秒也不能回库，且登记干燥提前
        self.clock.advance(seconds=M.DRY_MINUTES[M.MAT_PLASTIC] * 60 - 1)
        with self.assertRaises(ServiceError) as cm:
            self.svc.complete_drying(bid, "s")
        self.assertEqual(cm.exception.code, "dry_early")
        self.assertIn(M.INC_DRY_EARLY, [i["kind"] for i in self.svc.incidents()])
        self.assertEqual(self.svc.get_toy("T1")["status"], M.ST_DRYING)

        # 到点后成功回清洁库
        self.clock.advance(seconds=1)
        n = self.svc.complete_drying(bid, "s")
        self.assertEqual(n, 1)
        toy = self.svc.get_toy("T1")
        self.assertEqual(toy["status"], M.ST_CLEAN)
        self.assertEqual(toy["current_area"], M.AREA_CLEAN_STORE)
        self.assertIsNone(toy["dry_ready_at"])

    def test_deploy_drying_toy_logs_dry_early(self) -> None:
        self.svc.deploy("T1", "Z1", "s")
        self.svc.scan_recycle("T1", "s")
        bid = self.svc.create_batch(M.MAT_PLASTIC, "s", toy_ids=["T1"])
        self.svc.run_disinfection(bid, "s")
        self.svc.start_drying(bid, "s")
        with self.assertRaises(ServiceError) as cm:
            self.svc.deploy("T1", "Z1", "s")
        self.assertEqual(cm.exception.code, "dry_early")
        self.assertIn(M.INC_DRY_EARLY, [i["kind"] for i in self.svc.incidents()])

    def test_scheduler_completes_drying_and_flags_rotation(self) -> None:
        # 干燥到时由进程内 tick 自动完成
        self.svc.deploy("T1", "Z1", "s")
        self.svc.scan_recycle("T1", "s")
        bid = self.svc.create_batch(M.MAT_PLASTIC, "s", toy_ids=["T1"])
        self.svc.run_disinfection(bid, "s")
        self.svc.start_drying(bid, "s")
        self.clock.advance_minutes(M.DRY_MINUTES[M.MAT_PLASTIC])
        out = self.svc.tick()
        self.assertEqual([d["batch_id"] for d in out["dried_batches"]], [bid])
        self.assertEqual(self.svc.get_toy("T1")["status"], M.ST_CLEAN)

        # 轮换时点：tick 标记到点玩具
        self.svc.deploy("T1", "Z1", "s")
        self.clock.advance_minutes(M.ROTATION_MINUTES + 1)
        out = self.svc.tick()
        self.assertIn("T1", out["rotation_due"])
        # 再次 tick 不重复标记
        out2 = self.svc.tick()
        self.assertNotIn("T1", out2["rotation_due"])

    # ------------------------------------------------------------ 隔离权限
    def test_quarantine_immediate_and_cleaning_cannot_release(self) -> None:
        self.svc.deploy("T1", "Z1", "s")
        self.svc.report_damage("T1", M.Q_SHARP_EDGE, "员工甲",
                               note="边缘毛刺")
        toy = self.svc.get_toy("T1")
        self.assertEqual(toy["status"], M.ST_QUARANTINED)
        self.assertEqual(toy["current_area"], M.AREA_QUARANTINE)
        # 普通清洁流程（回收/组批）不能解除隔离
        with self.assertRaises(ServiceError) as cm:
            self.svc.scan_recycle("T1", "s")
        self.assertEqual(cm.exception.code, "quarantined")
        bid = self.svc.create_batch(M.MAT_PLASTIC, "s")
        with self.assertRaises(ServiceError) as cm:
            self.svc.add_to_batch(bid, "T1", "s")
        self.assertEqual(cm.exception.code, "quarantined")

    def test_front_desk_read_only(self) -> None:
        with self.assertRaises(PermissionDenied):
            self.svc.deploy("T1", "Z1", "f", role=M.ROLE_FRONT)
        with self.assertRaises(PermissionDenied):
            self.svc.scan_recycle("T1", "f", role=M.ROLE_FRONT)
        overview = self.svc.waiting_overview()
        self.assertIn("clean_store_available", overview)
        for z in overview["zones"]:
            self.assertIn("available", z)
            self.assertIn("next_rotation_at", z)

    def test_staff_cannot_release_quarantine(self) -> None:
        self.svc.report_damage("T1", M.Q_CONTAMINATION, "员工甲")
        with self.assertRaises(PermissionDenied):
            self.svc.release_quarantine("T1", "员工甲", M.ROLE_STAFF)

    def test_nurse_release_rules(self) -> None:
        # 掉件 -> 维修补件 -> 护士长复核合格
        self.svc.report_damage("T6", M.INC_LOOSE_PART, "员工甲",
                               note="少一个零件")
        self.assertEqual(self.svc.get_toy("T6")["parts_present"], 2)
        with self.assertRaises(ServiceError) as cm:
            self.svc.release_quarantine("T6", "护士长", M.ROLE_NURSE)
        self.assertEqual(cm.exception.code, "part_missing")
        self.svc.send_to_repair("T6", "员工甲")
        self.svc.complete_repair("T6", "员工甲", parts_restored=1, note="补件")
        self.svc.release_quarantine("T6", "护士长", M.ROLE_NURSE)
        self.assertEqual(self.svc.get_toy("T6")["status"], M.ST_CLEAN)

        # 锐边未维修不能直接合格，但可报废或重新清洁
        self.svc.report_damage("T1", M.Q_SHARP_EDGE, "员工甲", note="锐边")
        with self.assertRaises(ServiceError) as cm:
            self.svc.release_quarantine("T1", "护士长", M.ROLE_NURSE)
        self.assertEqual(cm.exception.code, "risk_unresolved")
        self.svc.release_quarantine("T1", "护士长", M.ROLE_NURSE,
                                    disposition="reprocess")
        self.assertEqual(self.svc.get_toy("T1")["status"], M.ST_PENDING_DIRTY)

    def test_batch_quarantine_on_disinfect_failure(self) -> None:
        for tid in ["T1", "T6"]:
            self.svc.deploy(tid, "Z1", "s")
            self.svc.scan_recycle(tid, "s")
        bid = self.svc.create_batch(M.MAT_PLASTIC, "s", toy_ids=["T1", "T6"])
        self.svc.adapter.force_next_result(False)
        r = self.svc.run_disinfection(bid, "s")
        self.assertFalse(r["ok"])
        self.assertIn(M.INC_DISINFECT_FAILED,
                      [i["kind"] for i in self.svc.incidents()])
        # 失败后整批隔离
        self.svc.quarantine_batch(bid, "s", note="设备不达标")
        for tid in ["T1", "T6"]:
            self.assertEqual(self.svc.get_toy(tid)["status"], M.ST_QUARANTINED)
        self.assertIn(M.INC_BATCH_ISOLATION,
                      [i["kind"] for i in self.svc.incidents()])

    # ------------------------------------------------------------ 追溯与其他登记
    def test_wrong_area_and_missed_scan_logged(self) -> None:
        self.svc.deploy("T1", "Z1", "s")
        self.svc.scan_recycle("T1", "s", found_zone_id="Z2")
        self.svc.report_missed_scan("T1", "巡检", note="漏扫补登记")
        kinds = [i["kind"] for i in self.svc.incidents()]
        self.assertIn(M.INC_WRONG_AREA, kinds)
        self.assertIn(M.INC_MISSED_SCAN, kinds)

    def test_full_cycle_batch_trace(self) -> None:
        bid = self._full_clean_cycle("T1", M.MAT_PLASTIC)
        # 按玩具追溯：事件链完整
        evs = self.svc.events(toy_id="T1")
        types = [e["event_type"] for e in reversed(evs)]
        for expected in [M.EV_DEPLOY, M.EV_SCAN_RECYCLE, M.EV_BATCH_CREATE,
                         M.EV_DISINFECT, M.EV_DRY_START, M.EV_DRY_DONE,
                         M.EV_RETURN_STORE]:
            self.assertIn(expected, types, f"追溯缺少事件 {expected}")
        # 按批次追溯
        batch_evs = self.svc.events(batch_id=bid)
        self.assertTrue(any(e["event_type"] == M.EV_DISINFECT for e in batch_evs))
        # 设备回执留存在批次上
        batch = next(b for b in self.svc.batches() if b["id"] == bid)
        receipt = json.loads(batch["receipt"])
        self.assertTrue(receipt["ok"])
        self.assertTrue(receipt["device_batch_no"].startswith("DEV-"))

    def test_recycle_scan_idempotent(self) -> None:
        self.svc.deploy("T1", "Z1", "s")
        self.svc.scan_recycle("T1", "s")
        r = self.svc.scan_recycle("T1", "s")
        self.assertTrue(r["already_in_bin"])


if __name__ == "__main__":
    unittest.main()
