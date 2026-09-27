"""清洁：按材质选择擦拭/浸泡/机洗；材质不兼容记录异常并拒绝。"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from ..db import log_exception
from ..domain import (CLEAN_METHOD_LABELS, EXC_MATERIAL_INCOMPAT,
                      MATERIAL_LABELS, DomainError, method_compatible)
from .core import get_staff, get_toy, now_iso, require_role, transition, tx


def clean_toy(conn, toy_id: int, method: str, staff_id: int,
              now: Optional[datetime] = None) -> dict:
    """执行清洁（单步完成）。隔离玩具走不到这里 —— 普通清洁不能解除隔离。"""
    staff = get_staff(conn, staff_id)
    require_role(staff, "clean")
    toy = get_toy(conn, toy_id)
    at = now_iso(now)

    if not method_compatible(toy["material"], method):
        with tx(conn):
            log_exception(conn, EXC_MATERIAL_INCOMPAT, toy_id=toy_id, actor_id=staff_id,
                          details=f"{MATERIAL_LABELS[toy['material']]}玩具{toy['code']}"
                                  f"不能使用{CLEAN_METHOD_LABELS.get(method, method)}", at=at)
        raise DomainError(
            f"材质不兼容：{MATERIAL_LABELS[toy['material']]}不能"
            f"{CLEAN_METHOD_LABELS.get(method, method)}，已记录异常", "material_incompatible")

    with tx(conn):
        transition(conn, toy, "cleaned", staff_id,
                   f"{CLEAN_METHOD_LABELS[method]}完成", at)
        conn.execute(
            "INSERT INTO cleaning_tasks (toy_id, method, started_by, started_at, finished_at, status)"
            " VALUES (?,?,?,?,?,'done')",
            (toy_id, method, staff_id, at, at))
    return {"toy_id": toy_id, "status": "cleaned", "method": method}
