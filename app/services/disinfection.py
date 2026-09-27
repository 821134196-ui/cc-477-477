"""消毒组批：按材质组批、提交模拟设备、按回执分流（成功→干燥 / 失败→待清洁 / 污染→批量隔离）。"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from ..db import log_exception, log_turnover
from ..domain import (CLEAN_METHOD_LABELS, EXC_BATCH_ISOLATION,
                      EXC_DISINFECTION_FAILED, EXC_MATERIAL_INCOMPAT,
                      MATERIAL_DRYING_MINUTES, MATERIAL_LABELS, DomainError,
                      method_compatible)
from .core import get_staff, get_toy, now_iso, require_role, transition, tx


def create_batch(conn, material: str, method: str, toy_ids: list[int],
                 staff_id: int, now: Optional[datetime] = None) -> dict:
    staff = get_staff(conn, staff_id)
    require_role(staff, "disinfect")
    if not toy_ids:
        raise DomainError("消毒批次不能为空", "bad_request")
    at = now_iso(now)

    if not method_compatible(material, method):
        with tx(conn):
            log_exception(conn, EXC_MATERIAL_INCOMPAT, actor_id=staff_id, at=at,
                          details=f"{MATERIAL_LABELS[material]}批次不能使用"
                                  f"{CLEAN_METHOD_LABELS.get(method, method)}消毒")
        raise DomainError("材质不兼容的消毒方式，已记录异常", "material_incompatible")

    seq = conn.execute("SELECT COUNT(*) c FROM disinfection_batches").fetchone()["c"] + 1
    code = f"DB-{at[:10].replace('-', '')}-{seq:03d}"

    with tx(conn):
        cur = conn.execute(
            "INSERT INTO disinfection_batches (code, material, method, status, created_by, created_at)"
            " VALUES (?,?,?,'open',?,?)",
            (code, material, method, staff_id, at))
        batch_id = cur.lastrowid
        for tid in toy_ids:
            toy = get_toy(conn, tid)
            if toy["material"] != material:
                raise DomainError(
                    f"玩具{toy['code']}材质为{MATERIAL_LABELS[toy['material']]}，"
                    f"不能混入{MATERIAL_LABELS[material]}批次", "material_mismatch")
            conn.execute(
                "INSERT INTO disinfection_batch_items (batch_id, toy_id) VALUES (?,?)",
                (batch_id, tid))
            transition(conn, toy, "disinfecting", staff_id, f"编入消毒批次{code}", at)
    return {"batch_id": batch_id, "code": code}


def submit_batch(conn, batch_id: int, staff_id: int, adapter,
                 now: Optional[datetime] = None) -> dict:
    """提交设备并处理回执。adapter.run_cycle 返回模拟设备回执。"""
    staff = get_staff(conn, staff_id)
    require_role(staff, "disinfect")
    batch = conn.execute(
        "SELECT * FROM disinfection_batches WHERE id=?", (batch_id,)).fetchone()
    if not batch:
        raise DomainError(f"批次不存在: {batch_id}", "not_found")
    if batch["status"] != "open":
        raise DomainError(f"批次{batch['code']}已提交过", "bad_transition")
    at = now_iso(now)

    items = conn.execute(
        "SELECT toy_id FROM disinfection_batch_items WHERE batch_id=?",
        (batch_id,)).fetchall()
    receipt = adapter.run_cycle(batch["code"], batch["method"], len(items))
    receipt_json = json.dumps(receipt, ensure_ascii=False)

    with tx(conn):
        conn.execute(
            "UPDATE disinfection_batches SET status=?, device_id=?, submitted_at=?,"
            " completed_at=?, receipt=? WHERE id=?",
            ("completed" if receipt["result"] == "success" else
             ("isolated" if receipt.get("contamination") else "failed"),
             receipt.get("device_id"), at, at, receipt_json, batch_id))

        for row in items:
            toy = get_toy(conn, row["toy_id"])
            if receipt["result"] == "success":
                _enter_drying(conn, toy, batch, staff_id, at)
            elif receipt.get("contamination"):
                _isolate_for_contamination(conn, toy, batch, staff_id, at)
            else:
                transition(conn, toy, "pending_clean", staff_id,
                           f"批次{batch['code']}消毒失败，退回待清洁", at)

        if receipt["result"] != "success":
            log_exception(conn, EXC_DISINFECTION_FAILED, batch_id=batch_id,
                          actor_id=staff_id, at=at,
                          details=f"批次{batch['code']}消毒失败：{receipt.get('message','')}")
        if receipt.get("contamination"):
            log_exception(conn, EXC_BATCH_ISOLATION, batch_id=batch_id,
                          actor_id=staff_id, at=at,
                          details=f"批次{batch['code']}检出污染风险，"
                                  f"{len(items)}件玩具批量隔离")
    return {"batch_id": batch_id, "receipt": receipt}


def _enter_drying(conn, toy, batch, staff_id, at) -> None:
    required = toy["drying_minutes"] or MATERIAL_DRYING_MINUTES[toy["material"]]
    from ..db import parse
    from datetime import timedelta
    due = (parse(at) + timedelta(minutes=required)).isoformat()
    transition(conn, toy, "drying", staff_id,
               f"批次{batch['code']}消毒合格，进入干燥（{required}分钟）", at)
    conn.execute(
        "INSERT INTO drying_records (toy_id, batch_id, started_at, required_minutes, due_at)"
        " VALUES (?,?,?,?,?)",
        (toy["id"], batch["id"], at, required, due))


def _isolate_for_contamination(conn, toy, batch, staff_id, at) -> None:
    transition(conn, toy, "isolated", staff_id,
               f"批次{batch['code']}污染风险，批量隔离", at)
    conn.execute(
        "INSERT INTO isolations (toy_id, reason, batch_id, isolated_by, isolated_at)"
        " VALUES (?,'contamination',?,?,?)",
        (toy["id"], batch["id"], staff_id, at))


def trace_batch(conn, batch_id: int) -> dict:
    """批次追溯：批次 → 玩具 → 隔离记录 → 异常事件。"""
    batch = conn.execute(
        "SELECT * FROM disinfection_batches WHERE id=?", (batch_id,)).fetchone()
    if not batch:
        raise DomainError(f"批次不存在: {batch_id}", "not_found")
    items = conn.execute(
        "SELECT t.* FROM disinfection_batch_items i JOIN toys t ON t.id=i.toy_id"
        " WHERE i.batch_id=? ORDER BY t.code", (batch_id,)).fetchall()
    isolations = conn.execute(
        "SELECT * FROM isolations WHERE batch_id=? ORDER BY id", (batch_id,)).fetchall()
    exceptions = conn.execute(
        "SELECT * FROM exception_events WHERE batch_id=? ORDER BY id", (batch_id,)).fetchall()
    drying = conn.execute(
        "SELECT * FROM drying_records WHERE batch_id=? ORDER BY id", (batch_id,)).fetchall()
    from ..db import row_to_dict
    return {
        "batch": row_to_dict(batch),
        "toys": [row_to_dict(r) for r in items],
        "isolations": [row_to_dict(r) for r in isolations],
        "exceptions": [row_to_dict(r) for r in exceptions],
        "drying_records": [row_to_dict(r) for r in drying],
    }
