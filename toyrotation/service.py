"""核心业务服务：投放、回收、清洁组批、消毒、干燥、隔离与轮换。

关键规则：
- 同一玩具同一时刻只能处于一个区域；投放使用 BEGIN IMMEDIATE 立即事务，
  两名员工并发投放仅有一人成功。
- 工序阻断：清洁库 -> 候诊区 -> 回收箱 -> 批次清洁 -> 消毒 -> 干燥(到时) -> 清洁库，
  未完成干燥不得重新投放（记录“干燥提前”）。
- 隔离：锐边/掉件/污染风险立即隔离；普通清洁流程不能解除隔离；
  仅护士长可复核解除，前台只读可用数量。
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta
from pathlib import Path

from . import models as M
from .adapters import MockSterilizerAdapter
from .db import (
    BATCH_STATUS_DISINFECTED,
    BATCH_STATUS_DONE,
    BATCH_STATUS_DRYING,
    BATCH_STATUS_OPEN,
    BATCH_STATUS_QUARANTINED,
    BATCH_STATUS_RUNNING,
    Database,
)


class ServiceError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class NotFound(ServiceError):
    def __init__(self, what: str):
        super().__init__("not_found", f"{what}不存在")


class PermissionDenied(ServiceError):
    def __init__(self, message: str = "当前角色无权执行该操作"):
        super().__init__("permission_denied", message)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


class ToyRotationService:
    def __init__(self, db: Database, adapter: MockSterilizerAdapter | None = None,
                 now=None, photo_dir: str | Path = "data/photos",
                 minute_seconds: int = 60):
        self.db = db
        self.adapter = adapter or MockSterilizerAdapter()
        self._now = now or datetime.now
        self.photo_dir = Path(photo_dir)
        self.photo_dir.mkdir(parents=True, exist_ok=True)
        # 一个“规定分钟”对应的真实秒数：生产为 60；本地演示可缩短以便观察
        self.minute_seconds = minute_seconds
        # 串行化所有写操作；投放排他另由数据库立即事务保证
        self._lock = threading.RLock()

    def _minutes(self, minutes: int) -> timedelta:
        return timedelta(seconds=minutes * self.minute_seconds)

    def now(self) -> datetime:
        return self._now()

    def _reject(self, code: str, message: str):
        """登记违规后拒绝业务操作：先提交 incident 记录，再回滚未完成的业务变更。

        保证“材质不兼容/干燥提前”等违规即使操作被拒绝也留有登记。
        """
        self.db.commit()
        raise ServiceError(code, message)

    # ------------------------------------------------------------------ 基础
    def add_zone(self, zone_id: str, name: str, capacity: int) -> None:
        with self._lock:
            self.db.execute(
                "INSERT OR REPLACE INTO waiting_zones(id,name,capacity) VALUES (?,?,?)",
                (zone_id, name, capacity),
            )
            self.db.commit()

    def add_toy(self, toy_id: str, name: str, material: str, age_min: int,
                age_max: int, parts_total: int = 1, icon: str = "cube",
                clean_method: str | None = None) -> None:
        if material not in M.MATERIALS:
            raise ServiceError("bad_material", f"未知材质：{material}")
        method = clean_method or M.DEFAULT_CLEAN_METHOD[material]
        if method not in M.MATERIAL_CLEAN_COMPAT[material]:
            raise ServiceError("bad_method", f"{M.MATERIAL_LABELS[material]}不允许{ M.CLEAN_LABELS[method]}")
        ts = _iso(self.now())
        with self._lock:
            self.db.execute(
                "INSERT INTO toys(id,name,icon,age_min,age_max,material,clean_method,"
                "parts_total,parts_present,status,current_area,created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (toy_id, name, icon, age_min, age_max, material, method,
                 parts_total, parts_total, M.ST_CLEAN, M.AREA_CLEAN_STORE, ts),
            )
            self.db.commit()

    def get_toy(self, toy_id: str) -> dict:
        row = self.db.query_one("SELECT * FROM toys WHERE id=?", (toy_id,))
        if row is None:
            raise NotFound(f"玩具 {toy_id}")
        return dict(row)

    # ------------------------------------------------------------------ 投放
    def deploy(self, toy_id: str, zone_id: str, actor: str, role: str = M.ROLE_STAFF) -> dict:
        """把玩具从清洁库投放到候诊区。并发调用仅一人成功。"""
        if role == M.ROLE_FRONT:
            raise PermissionDenied("前台只能查看可用数量，不能投放")
        if role not in (M.ROLE_STAFF, M.ROLE_NURSE):
            raise PermissionDenied()
        zone = self.db.query_one("SELECT * FROM waiting_zones WHERE id=?", (zone_id,))
        if zone is None:
            raise NotFound(f"候诊区 {zone_id}")
        now = self.now()
        with self._lock:
            self.db.begin_immediate()  # 立即写锁：并发投放在此串行
            try:
                toy = self.db.query_one("SELECT * FROM toys WHERE id=?", (toy_id,))
                if toy is None:
                    raise NotFound(f"玩具 {toy_id}")
                toy = dict(toy)
                if toy["status"] == M.ST_QUARANTINED or toy["current_area"] == M.AREA_QUARANTINE:
                    raise ServiceError("quarantined", f"{toy_id} 处于隔离状态，禁止投放")
                if toy["status"] == M.ST_DEPLOYED:
                    raise ServiceError("already_deployed",
                                       f"{toy_id} 已在候诊区（{toy['zone_id']}），不能重复投放")
                if toy["status"] == M.ST_DRYING:
                    self.db.log_incident(
                        _iso(now), M.INC_DRY_EARLY, actor, toy_id=toy_id, zone_id=zone_id,
                        note=f"干燥未完成（计划 {toy['dry_ready_at']}）即试图投放")
                    self._reject("dry_early",
                                 f"{toy_id} 干燥未到规定时长（{toy['dry_ready_at']} 完成），禁止投放")
                if toy["status"] != M.ST_CLEAN or toy["current_area"] != M.AREA_CLEAN_STORE:
                    raise ServiceError("wrong_state",
                                       f"{toy_id} 当前为「{M.AREA_LABELS.get(toy['current_area'], toy['current_area'])}」，"
                                       f"须回到清洁库后才能投放")
                if toy["parts_present"] < toy["parts_total"]:
                    raise ServiceError("part_missing",
                                       f"{toy_id} 零件缺失（{toy['parts_present']}/{toy['parts_total']}），禁止投放")
                used = self.db.query_one(
                    "SELECT COUNT(*) c FROM toys WHERE zone_id=? AND status=?",
                    (zone_id, M.ST_DEPLOYED))["c"]
                if used >= zone["capacity"]:
                    raise ServiceError("zone_full",
                                       f"候诊区「{zone['name']}」已满（{used}/{zone['capacity']}）")
                rotation_due = now + self._minutes(M.ROTATION_MINUTES)
                self.db.execute(
                    "UPDATE toys SET status=?,current_area=?,zone_id=?,deployed_at=?,"
                    "rotation_due_at=?,batch_id=NULL WHERE id=?",
                    (M.ST_DEPLOYED, M.AREA_WAITING, zone_id, _iso(now), _iso(rotation_due), toy_id),
                )
                self.db.log_event(_iso(now), M.EV_DEPLOY, actor, toy_id=toy_id, zone_id=zone_id,
                                  detail={"rotation_due_at": _iso(rotation_due)})
                self.db.commit()
            except Exception:
                self.db.rollback()
                raise
        return {"toy_id": toy_id, "zone_id": zone_id, "rotation_due_at": _iso(rotation_due)}

    # ------------------------------------------------------------------ 回收
    def scan_recycle(self, toy_id: str, actor: str, role: str = M.ROLE_STAFF,
                     found_zone_id: str | None = None) -> dict:
        """回收扫描：玩具从候诊区进入回收箱（待清洁）。"""
        if role == M.ROLE_FRONT:
            raise PermissionDenied("前台不能执行回收扫描")
        now = self.now()
        with self._lock:
            self.db.begin_immediate()
            try:
                toy = self.db.query_one("SELECT * FROM toys WHERE id=?", (toy_id,))
                if toy is None:
                    raise NotFound(f"玩具 {toy_id}")
                toy = dict(toy)
                if toy["status"] == M.ST_QUARANTINED:
                    raise ServiceError("quarantined", f"{toy_id} 已隔离，请走隔离复核流程")
                if toy["status"] == M.ST_PENDING_DIRTY:
                    # 重复扫描：幂等返回，不再登记事件
                    return {"toy_id": toy_id, "already_in_bin": True}
                # 错区：实物所在候诊区与登记区不一致，仍回收并登记
                if found_zone_id and toy.get("zone_id") and found_zone_id != toy["zone_id"]:
                    self.db.log_incident(
                        _iso(now), M.INC_WRONG_AREA, actor, toy_id=toy_id,
                        zone_id=found_zone_id,
                        note=f"登记在 {toy['zone_id']}，实物扫描于 {found_zone_id}")
                if toy["status"] != M.ST_DEPLOYED:
                    raise ServiceError("wrong_state",
                                       f"{toy_id} 不在候诊区，无法回收（当前："
                                       f"{M.AREA_LABELS.get(toy['current_area'], toy['current_area'])}）")
                self.db.execute(
                    "UPDATE toys SET status=?,current_area=?,zone_id=NULL,"
                    "deployed_at=NULL,rotation_due_at=NULL WHERE id=?",
                    (M.ST_PENDING_DIRTY, M.AREA_RECYCLE, toy_id),
                )
                self.db.log_event(_iso(now), M.EV_SCAN_RECYCLE, actor, toy_id=toy_id,
                                  zone_id=found_zone_id or toy["zone_id"])
                self.db.commit()
            except Exception:
                self.db.rollback()
                raise
        return {"toy_id": toy_id, "already_in_bin": False}

    def report_missed_scan(self, toy_id: str, actor: str, note: str = "") -> None:
        """登记回收漏扫：玩具已 physical 回收但未扫描，或超过轮换时点仍未回收。"""
        with self._lock:
            toy = self.db.query_one("SELECT * FROM toys WHERE id=?", (toy_id,))
            if toy is None:
                raise NotFound(f"玩具 {toy_id}")
            self.db.log_incident(
                _iso(self.now()), M.INC_MISSED_SCAN, actor, toy_id=toy_id,
                zone_id=toy["zone_id"], note=note or "回收时漏扫/超过轮换时点未回收")
            self.db.commit()

    # ------------------------------------------------------------------ 组批
    def create_batch(self, material: str, actor: str,
                     toy_ids: list[str] | None = None) -> str:
        """按材质组建消毒批次（同材质、同清洁方式）。"""
        if material not in M.MATERIALS:
            raise ServiceError("bad_material", f"未知材质：{material}")
        now = self.now()
        with self._lock:
            day = now.strftime("%Y%m%d")
            seq = self.db.query_one(
                "SELECT COUNT(*) c FROM batches WHERE id LIKE ?", (f"B-{day}-%",))["c"] + 1
            batch_id = f"B-{day}-{seq:03d}"
            self.db.execute(
                "INSERT INTO batches(id,material,clean_method,dry_minutes,status,created_at)"
                " VALUES (?,?,?,?,?,?)",
                (batch_id, material, M.DEFAULT_CLEAN_METHOD[material],
                 M.DRY_MINUTES[material], BATCH_STATUS_OPEN, _iso(now)),
            )
            self.db.log_event(_iso(now), M.EV_BATCH_CREATE, actor, batch_id=batch_id,
                              detail={"material": material})
            self.db.commit()
        if toy_ids:
            for tid in toy_ids:
                self.add_to_batch(batch_id, tid, actor)
        return batch_id

    def add_to_batch(self, batch_id: str, toy_id: str, actor: str,
                     role: str = M.ROLE_STAFF, method: str | None = None) -> None:
        if role == M.ROLE_FRONT:
            raise PermissionDenied("前台不能组批")
        with self._lock:
            self.db.begin_immediate()
            try:
                batch = self.db.query_one("SELECT * FROM batches WHERE id=?", (batch_id,))
                if batch is None:
                    raise NotFound(f"批次 {batch_id}")
                batch = dict(batch)
                if batch["status"] not in (BATCH_STATUS_OPEN, BATCH_STATUS_RUNNING):
                    raise ServiceError("batch_closed",
                                       f"批次 {batch_id} 状态为 {batch['status']}，不能再加入玩具")
                toy = self.db.query_one("SELECT * FROM toys WHERE id=?", (toy_id,))
                if toy is None:
                    raise NotFound(f"玩具 {toy_id}")
                toy = dict(toy)
                if toy["status"] == M.ST_QUARANTINED:
                    raise ServiceError("quarantined", f"{toy_id} 已隔离，普通清洁不能解除隔离")
                # 材质不兼容登记（试图用错误清洁方式）
                wanted = method or batch["clean_method"]
                if wanted not in M.MATERIAL_CLEAN_COMPAT[toy["material"]]:
                    self.db.log_incident(
                        _iso(self.now()), M.INC_MATERIAL_INCOMPAT, actor,
                        toy_id=toy_id, batch_id=batch_id,
                        note=f"{M.MATERIAL_LABELS[toy['material']]}不可{M.CLEAN_LABELS.get(wanted, wanted)}")
                    self._reject("material_incompat",
                                 f"材质不兼容：{M.MATERIAL_LABELS[toy['material']]}"
                                 f"不可{ M.CLEAN_LABELS.get(wanted, wanted)}")
                if toy["material"] != batch["material"]:
                    raise ServiceError("material_mismatch",
                                       f"批次按材质分组：{toy_id} 是"
                                       f"{M.MATERIAL_LABELS[toy['material']]}，本批为"
                                       f"{M.MATERIAL_LABELS[batch['material']]}")
                if toy["status"] != M.ST_PENDING_DIRTY or toy["current_area"] != M.AREA_RECYCLE:
                    raise ServiceError("wrong_state",
                                       f"{toy_id} 不在回收箱，不能加入批次（当前："
                                       f"{M.AREA_LABELS.get(toy['current_area'], toy['current_area'])}）")
                exists = self.db.query_one(
                    "SELECT 1 FROM batch_items WHERE batch_id=? AND toy_id=?",
                    (batch_id, toy_id))
                if not exists:
                    self.db.execute(
                        "INSERT INTO batch_items(batch_id,toy_id) VALUES (?,?)",
                        (batch_id, toy_id))
                self.db.execute(
                    "UPDATE toys SET status=?,current_area=?,batch_id=? WHERE id=?",
                    (M.ST_WASHING, M.AREA_WASH, batch_id, toy_id))
                self.db.log_event(_iso(self.now()), M.EV_BATCH_CREATE, actor,
                                  toy_id=toy_id, batch_id=batch_id,
                                  detail={"action": "added_to_batch", "method": wanted})
                self.db.commit()
            except Exception:
                self.db.rollback()
                raise

    # ------------------------------------------------------------------ 消毒
    def run_disinfection(self, batch_id: str, actor: str,
                         role: str = M.ROLE_STAFF) -> dict:
        if role == M.ROLE_FRONT:
            raise PermissionDenied("前台不能操作消毒")
        with self._lock:
            self.db.begin_immediate()
            try:
                batch = self.db.query_one("SELECT * FROM batches WHERE id=?", (batch_id,))
                if batch is None:
                    raise NotFound(f"批次 {batch_id}")
                batch = dict(batch)
                if batch["status"] not in (BATCH_STATUS_OPEN, BATCH_STATUS_RUNNING):
                    raise ServiceError("batch_state", f"批次 {batch_id} 当前不可消毒")
                items = self.db.query(
                    "SELECT t.* FROM batch_items bi JOIN toys t ON t.id=bi.toy_id "
                    "WHERE bi.batch_id=?", (batch_id,))
                if not items:
                    raise ServiceError("empty_batch", "空批次不能消毒")
                materials = [dict(r)["material"] for r in items]
                receipt = self.adapter.run_cycle(materials)
                now = _iso(self.now())
                self.db.execute(
                    "UPDATE batches SET status=?,device_batch_no=?,receipt=? WHERE id=?",
                    (BATCH_STATUS_RUNNING, receipt.device_batch_no,
                     __import__("json").dumps(receipt.as_dict(), ensure_ascii=False),
                     batch_id))
                if receipt.ok:
                    self.db.execute(
                        "UPDATE batches SET status=?,disinfected_at=? WHERE id=?",
                        (BATCH_STATUS_DISINFECTED, now, batch_id))
                    for r in items:
                        self.db.log_event(now, M.EV_DISINFECT, actor,
                                          toy_id=r["id"], batch_id=batch_id,
                                          detail=receipt.as_dict())
                else:
                    # 消毒失败：玩具停留在清洁中，登记 incident；可重试或整批隔离
                    self.db.log_incident(
                        now, M.INC_DISINFECT_FAILED, actor, batch_id=batch_id,
                        note=f"设备 {receipt.device_batch_no} 回执失败：{receipt.message}",
                        detail=receipt.as_dict())
                    for r in items:
                        self.db.log_event(now, M.EV_DISINFECT, actor,
                                          toy_id=r["id"], batch_id=batch_id,
                                          detail={**receipt.as_dict(), "ok": False})
                self.db.commit()
                return {"batch_id": batch_id, **receipt.as_dict()}
            except Exception:
                self.db.rollback()
                raise

    def quarantine_batch(self, batch_id: str, actor: str,
                         role: str = M.ROLE_STAFF, reason: str = M.Q_BATCH_RISK,
                         note: str = "") -> None:
        """整批隔离（如消毒失败后存在批量污染风险）。"""
        with self._lock:
            self.db.begin_immediate()
            try:
                batch = self.db.query_one("SELECT * FROM batches WHERE id=?", (batch_id,))
                if batch is None:
                    raise NotFound(f"批次 {batch_id}")
                now = _iso(self.now())
                items = self.db.query(
                    "SELECT * FROM batch_items WHERE batch_id=?", (batch_id,))
                for r in items:
                    self._do_quarantine(r["toy_id"], reason, actor, now,
                                        note=note or "批量隔离", batch_id=batch_id)
                self.db.execute("UPDATE batches SET status=? WHERE id=?",
                                (BATCH_STATUS_QUARANTINED, batch_id))
                self.db.log_incident(now, M.INC_BATCH_ISOLATION, actor,
                                     batch_id=batch_id,
                                     note=note or f"批次 {batch_id} 整批隔离")
                self.db.log_event(now, M.EV_BATCH_QUARANTINE, actor, batch_id=batch_id,
                                  detail={"reason": reason, "count": len(items)})
                self.db.commit()
            except Exception:
                self.db.rollback()
                raise

    # ------------------------------------------------------------------ 干燥
    def start_drying(self, batch_id: str, actor: str, role: str = M.ROLE_STAFF) -> None:
        if role == M.ROLE_FRONT:
            raise PermissionDenied("前台不能操作干燥")
        with self._lock:
            self.db.begin_immediate()
            try:
                batch = self.db.query_one("SELECT * FROM batches WHERE id=?", (batch_id,))
                if batch is None:
                    raise NotFound(f"批次 {batch_id}")
                batch = dict(batch)
                if batch["status"] != BATCH_STATUS_DISINFECTED:
                    raise ServiceError("batch_state",
                                       f"批次 {batch_id} 未完成消毒，不能进入干燥")
                now = self.now()
                ready = now + self._minutes(batch["dry_minutes"])
                items = self.db.query(
                    "SELECT * FROM batch_items WHERE batch_id=?", (batch_id,))
                for r in items:
                    self.db.execute(
                        "UPDATE toys SET status=?,current_area=?,dry_ready_at=? WHERE id=?",
                        (M.ST_DRYING, M.AREA_DRYING, _iso(ready), r["toy_id"]))
                    self.db.log_event(_iso(now), M.EV_DRY_START, actor,
                                      toy_id=r["toy_id"], batch_id=batch_id,
                                      detail={"dry_minutes": batch["dry_minutes"],
                                              "dry_ready_at": _iso(ready)})
                self.db.execute("UPDATE batches SET status=? WHERE id=?",
                                (BATCH_STATUS_DRYING, batch_id))
                self.db.commit()
            except Exception:
                self.db.rollback()
                raise

    def complete_drying(self, batch_id: str, actor: str = "system",
                        force: bool = False) -> int:
        """干燥到时，整批回到清洁库。force=False 时提前完成将登记 incident 并拒绝。"""
        with self._lock:
            self.db.begin_immediate()
            try:
                batch = self.db.query_one("SELECT * FROM batches WHERE id=?", (batch_id,))
                if batch is None:
                    raise NotFound(f"批次 {batch_id}")
                if dict(batch)["status"] != BATCH_STATUS_DRYING:
                    raise ServiceError("batch_state", f"批次 {batch_id} 不在干燥中")
                now = self.now()
                items = self.db.query(
                    "SELECT * FROM toys WHERE batch_id=? AND status=?",
                    (batch_id, M.ST_DRYING))
                if not force:
                    for r in items:
                        ready = datetime.strptime(r["dry_ready_at"], "%Y-%m-%d %H:%M:%S")
                        if now < ready:
                            remain = int((ready - now).total_seconds())
                            self.db.log_incident(
                                _iso(now), M.INC_DRY_EARLY, actor,
                                toy_id=r["id"], batch_id=batch_id,
                                note=f"距干燥完成还差 {remain} 秒即试图取用")
                            self._reject(
                                "dry_early",
                                f"{r['id']} 干燥未完成，还差 {remain} 秒（规定干燥到时："
                                f"{r['dry_ready_at']}）")
                for r in items:
                    self.db.execute(
                        "UPDATE toys SET status=?,current_area=?,dry_ready_at=NULL,"
                        "batch_id=NULL WHERE id=?",
                        (M.ST_CLEAN, M.AREA_CLEAN_STORE, r["id"]))
                    self.db.log_event(_iso(now), M.EV_DRY_DONE, actor,
                                      toy_id=r["id"], batch_id=batch_id)
                    self.db.log_event(_iso(now), M.EV_RETURN_STORE, actor,
                                      toy_id=r["id"], batch_id=batch_id)
                self.db.execute("DELETE FROM batch_items WHERE batch_id=?", (batch_id,))
                self.db.execute("UPDATE batches SET status=? WHERE id=?",
                                (BATCH_STATUS_DONE, batch_id))
                self.db.commit()
                return len(items)
            except Exception:
                self.db.rollback()
                raise

    # ------------------------------------------------------------------ 隔离
    def _do_quarantine(self, toy_id: str, reason: str, actor: str, ts: str,
                       note: str = "", batch_id: str | None = None,
                       photo_path: str | None = None) -> None:
        self.db.execute(
            "UPDATE toys SET status=?,current_area=?,zone_id=NULL,deployed_at=NULL,"
            "rotation_due_at=NULL,quarantined_at=?,quarantine_reason=?,quarantine_note=?,"
            "damaged_photo=COALESCE(?,damaged_photo),batch_id=? WHERE id=?",
            (M.ST_QUARANTINED, M.AREA_QUARANTINE, ts, reason, note, photo_path,
             batch_id if batch_id else None, toy_id))
        self.db.log_event(ts, M.EV_QUARANTINE, actor, toy_id=toy_id, batch_id=batch_id,
                          detail={"reason": reason, "note": note})

    def report_damage(self, toy_id: str, kind: str, actor: str,
                      role: str = M.ROLE_STAFF, note: str = "",
                      photo_path: str | None = None, missing_parts: int = 1) -> dict:
        """发现锐边/掉件/污染风险：立即隔离并登记破损与 incident。

        普通清洁流程不能解除隔离；必须由护士长按隔离复核处理。
        """
        if kind not in (M.Q_SHARP_EDGE, M.INC_LOOSE_PART, M.Q_CONTAMINATION):
            raise ServiceError("bad_kind", f"未知破损类型：{kind}")
        with self._lock:
            self.db.begin_immediate()
            try:
                toy = self.db.query_one("SELECT * FROM toys WHERE id=?", (toy_id,))
                if toy is None:
                    raise NotFound(f"玩具 {toy_id}")
                now = _iso(self.now())
                cur = self.db.execute(
                    "INSERT INTO damage_reports(ts,toy_id,kind,note,photo_path,actor)"
                    " VALUES (?,?,?,?,?,?)",
                    (now, toy_id, kind, note, photo_path, actor))
                report_id = int(cur.lastrowid)
                if kind == M.INC_LOOSE_PART:
                    self.db.execute(
                        "UPDATE toys SET parts_present=MAX(0,parts_present-?) WHERE id=?",
                        (missing_parts, toy_id))
                    self.db.log_incident(
                        now, M.INC_PART_MISSING, actor, toy_id=toy_id,
                        note=note or f"掉件 {missing_parts} 个，零件缺失立即隔离")
                self._do_quarantine(toy_id, kind, actor, now, note=note,
                                    photo_path=photo_path)
                self.db.commit()
                return {"report_id": report_id, "toy_id": toy_id, "quarantined": True}
            except Exception:
                self.db.rollback()
                raise

    def send_to_repair(self, toy_id: str, actor: str, role: str = M.ROLE_STAFF) -> None:
        if role == M.ROLE_FRONT:
            raise PermissionDenied()
        with self._lock:
            toy = self.db.query_one("SELECT * FROM toys WHERE id=?", (toy_id,))
            if toy is None:
                raise NotFound(f"玩具 {toy_id}")
            if dict(toy)["status"] != M.ST_QUARANTINED:
                raise ServiceError("wrong_state", "只有隔离中的玩具可以送修")
            self.db.execute("UPDATE toys SET status=? WHERE id=?", (M.ST_REPAIR, toy_id))
            self.db.log_event(_iso(self.now()), M.EV_REPAIR_START, actor, toy_id=toy_id)
            self.db.commit()

    def complete_repair(self, toy_id: str, actor: str, role: str = M.ROLE_STAFF,
                        parts_restored: int = 0, note: str = "") -> None:
        """维修完成：回到隔离状态等待护士长复核（维修本身不解除隔离）。"""
        with self._lock:
            toy = self.db.query_one("SELECT * FROM toys WHERE id=?", (toy_id,))
            if toy is None:
                raise NotFound(f"玩具 {toy_id}")
            if dict(toy)["status"] != M.ST_REPAIR:
                raise ServiceError("wrong_state", "只有维修中的玩具可以登记维修完成")
            if parts_restored:
                self.db.execute(
                    "UPDATE toys SET parts_present=MIN(parts_total,parts_present+?) WHERE id=?",
                    (parts_restored, toy_id))
            self.db.execute(
                "UPDATE toys SET status=?,quarantine_note=? WHERE id=?",
                (M.ST_QUARANTINED,
                 (dict(toy)["quarantine_note"] or "") + f"｜维修完成：{note}", toy_id))
            self.db.log_event(_iso(self.now()), M.EV_REPAIR_DONE, actor, toy_id=toy_id,
                              detail={"parts_restored": parts_restored})
            self.db.commit()

    def release_quarantine(self, toy_id: str, actor: str, role: str,
                           disposition: str = "store", note: str = "") -> None:
        """护士长隔离复核后解除隔离。

        disposition: store=复核合格回清洁库；reprocess=重新清洁消毒；retire=报废。
        前台与普通员工无权操作。
        """
        if role != M.ROLE_NURSE:
            raise PermissionDenied("只有护士长可以解除隔离")
        if disposition not in ("store", "reprocess", "retire"):
            raise ServiceError("bad_disposition", "处置方式必须是 store/reprocess/retire")
        with self._lock:
            self.db.begin_immediate()
            try:
                toy = self.db.query_one("SELECT * FROM toys WHERE id=?", (toy_id,))
                if toy is None:
                    raise NotFound(f"玩具 {toy_id}")
                toy = dict(toy)
                if toy["status"] not in (M.ST_QUARANTINED, M.ST_REPAIR):
                    raise ServiceError("wrong_state", f"{toy_id} 当前未被隔离")
                now = _iso(self.now())
                if disposition == "retire":
                    self.db.execute(
                        "UPDATE toys SET status=?,current_area=?,quarantined_at=NULL,"
                        "quarantine_reason=NULL,quarantine_note=? WHERE id=?",
                        (M.ST_RETIRED, M.AREA_QUARANTINE, note, toy_id))
                    self.db.log_event(now, M.EV_RETIRE, actor, toy_id=toy_id,
                                      detail={"note": note})
                elif disposition == "reprocess":
                    self.db.execute(
                        "UPDATE toys SET status=?,current_area=?,zone_id=NULL,"
                        "quarantined_at=NULL,quarantine_reason=NULL,quarantine_note=?,"
                        "dry_ready_at=NULL,batch_id=NULL WHERE id=?",
                        (M.ST_PENDING_DIRTY, M.AREA_RECYCLE, note, toy_id))
                    self.db.log_event(now, M.EV_RELEASE, actor, toy_id=toy_id,
                                      detail={"disposition": "reprocess", "note": note})
                else:
                    if toy["parts_present"] < toy["parts_total"]:
                        raise ServiceError("part_missing",
                                           "零件仍缺失，不能复核合格回清洁库；请先维修或报废")
                    if toy["quarantine_reason"] in (M.Q_SHARP_EDGE, M.Q_CONTAMINATION) \
                            and "维修完成" not in (toy["quarantine_note"] or ""):
                        raise ServiceError(
                            "risk_unresolved",
                            "锐边/污染风险未经维修处理，不能直接复核合格；"
                            "请先送修，或选择重新清洁消毒、报废")
                    self.db.execute(
                        "UPDATE toys SET status=?,current_area=?,quarantined_at=NULL,"
                        "quarantine_reason=NULL,quarantine_note=?,dry_ready_at=NULL,"
                        "batch_id=NULL WHERE id=?",
                        (M.ST_CLEAN, M.AREA_CLEAN_STORE, note, toy_id))
                    self.db.log_event(now, M.EV_RELEASE, actor, toy_id=toy_id,
                                      detail={"disposition": "store", "note": note})
                self.db.execute("UPDATE incidents SET resolved=1 "
                                "WHERE toy_id=? AND kind IN (?,?,?) AND resolved=0",
                                (toy_id, M.INC_PART_MISSING, M.INC_WRONG_AREA,
                                 M.INC_DISINFECT_FAILED))
                self.db.commit()
            except Exception:
                self.db.rollback()
                raise

    # ------------------------------------------------------------------ 查询
    def zones(self) -> list[dict]:
        return [dict(r) for r in self.db.query("SELECT * FROM waiting_zones ORDER BY id")]

    def toys(self, area: str | None = None) -> list[dict]:
        if area:
            rows = self.db.query("SELECT * FROM toys WHERE current_area=? ORDER BY id", (area,))
        else:
            rows = self.db.query("SELECT * FROM toys ORDER BY id")
        return [dict(r) for r in rows]

    def batches(self, include_done: bool = True) -> list[dict]:
        sql = "SELECT * FROM batches"
        if not include_done:
            sql += " WHERE status != 'done'"
        sql += " ORDER BY id DESC"
        out = []
        for r in self.db.query(sql):
            b = dict(r)
            b["items"] = [dict(x) for x in self.db.query(
                "SELECT t.id,t.name,t.material,t.status FROM batch_items bi "
                "JOIN toys t ON t.id=bi.toy_id WHERE bi.batch_id=? ORDER BY t.id",
                (b["id"],))]
            out.append(b)
        return out

    def incidents(self, only_open: bool = False) -> list[dict]:
        sql = "SELECT * FROM incidents"
        if only_open:
            sql += " WHERE resolved=0"
        sql += " ORDER BY id DESC"
        return [dict(r) for r in self.db.query(sql)]

    def events(self, toy_id: str | None = None, batch_id: str | None = None,
               limit: int = 200) -> list[dict]:
        sql = "SELECT * FROM events WHERE 1=1"
        params: list = []
        if toy_id:
            sql += " AND toy_id=?"
            params.append(toy_id)
        if batch_id:
            sql += " AND batch_id=?"
            params.append(batch_id)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        return [dict(r) for r in self.db.query(sql, tuple(params))]

    def damage_reports(self) -> list[dict]:
        return [dict(r) for r in self.db.query(
            "SELECT * FROM damage_reports ORDER BY id DESC")]

    def waiting_overview(self) -> dict:
        """候诊区视图：只含可用数量与轮换时间（前台可见的最小信息）。"""
        now = self.now()
        zones = []
        for z in self.zones():
            rows = self.db.query(
                "SELECT id,name,rotation_due_at FROM toys WHERE zone_id=? AND status=?",
                (z["id"], M.ST_DEPLOYED))
            items = [dict(r) for r in rows]
            overdue = [t for t in items
                       if datetime.strptime(t["rotation_due_at"], "%Y-%m-%d %H:%M:%S") <= now]
            zones.append({
                "zone_id": z["id"],
                "name": z["name"],
                "capacity": z["capacity"],
                "available": len(items),
                "rotation_overdue": len(overdue),
                "next_rotation_at": min((t["rotation_due_at"] for t in items),
                                        default=None),
                "items": items,
            })
        clean_available = self.db.query_one(
            "SELECT COUNT(*) c FROM toys WHERE status=? AND current_area=?",
            (M.ST_CLEAN, M.AREA_CLEAN_STORE))["c"]
        return {"zones": zones, "clean_store_available": clean_available,
                "checked_at": _iso(now)}

    def tick(self) -> dict:
        """进程内周期任务：完成到时的干燥批次，登记到轮换时点的玩具。"""
        result = {"dried_batches": [], "rotation_due": []}
        with self._lock:
            for b in self.db.query("SELECT * FROM batches WHERE status=?",
                                   (BATCH_STATUS_DRYING,)):
                pending = self.db.query_one(
                    "SELECT MIN(dry_ready_at) m FROM toys WHERE batch_id=? AND status=?",
                    (b["id"], M.ST_DRYING))
                if pending and pending["m"]:
                    ready = datetime.strptime(pending["m"], "%Y-%m-%d %H:%M:%S")
                    if ready <= self.now():
                        n = self.complete_drying(b["id"], actor="scheduler")
                        result["dried_batches"].append({"batch_id": b["id"], "count": n})
            # 轮换时点：到达后登记一次 rotation_due 事件供前台/员工提示
            now = self.now()
            due = self.db.query(
                "SELECT * FROM toys WHERE status=? AND rotation_due_at <= ?",
                (M.ST_DEPLOYED, _iso(now)))
            for t in due:
                flagged = self.db.query_one(
                    "SELECT 1 FROM events WHERE toy_id=? AND event_type=? "
                    "AND ts >= (SELECT deployed_at FROM toys WHERE id=?)",
                    (t["id"], M.EV_RECALL, t["id"]))
                if not flagged:
                    self.db.log_event(_iso(now), M.EV_RECALL, "scheduler",
                                      toy_id=t["id"], zone_id=t["zone_id"],
                                      detail={"rotation_due_at": t["rotation_due_at"]})
                    result["rotation_due"].append(t["id"])
            if result["rotation_due"]:
                self.db.commit()
        return result
