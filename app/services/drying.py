"""干燥计时：到期回库、提前释放拦截（干燥提前异常）、调度器到期巡检。"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from ..db import log_exception, parse, row_to_dict
from ..domain import EXC_EARLY_DRY, DomainError
from .core import get_staff, get_toy, now_iso, require_role, transition, tx


def list_drying(conn, now: Optional[datetime] = None) -> list[dict]:
    at = now_iso(now)
    rows = conn.execute(
        "SELECT d.*, t.code toy_code, t.name toy_name, t.material FROM drying_records d"
        " JOIN toys t ON t.id=d.toy_id WHERE d.status='drying' ORDER BY d.due_at").fetchall()
    out = []
    for r in rows:
        d = row_to_dict(r)
        d["remaining_seconds"] = max(0, int((parse(r["due_at"]) - parse(at)).total_seconds()))
        out.append(d)
    return out


def _complete(conn, rec, staff_id, at) -> bool:
    toy = get_toy(conn, rec["toy_id"])
    if toy["status"] != "drying":
        return False  # 干燥期间被隔离等情况：不自动回库
    transition(conn, toy, "in_storage", staff_id,
               f"干燥满{rec['required_minutes']}分钟，回到清洁库", at)
    conn.execute(
        "UPDATE drying_records SET status='done', completed_at=? WHERE id=?",
        (at, rec["id"]))
    return True


def release_drying(conn, record_id: int, staff_id: int,
                   now: Optional[datetime] = None) -> dict:
    """人工上架：未满规定干燥时长 → 记录干燥提前异常并拒绝。"""
    staff = get_staff(conn, staff_id)
    require_role(staff, "dry_release")
    rec = conn.execute("SELECT * FROM drying_records WHERE id=? AND status='drying'",
                       (record_id,)).fetchone()
    if not rec:
        raise DomainError(f"干燥记录不存在或已完成: {record_id}", "not_found")
    at = now_iso(now)

    if parse(at) < parse(rec["due_at"]):
        remaining = int((parse(rec["due_at"]) - parse(at)).total_seconds())
        with tx(conn):
            log_exception(conn, EXC_EARLY_DRY, toy_id=rec["toy_id"],
                          batch_id=rec["batch_id"], actor_id=staff_id, at=at,
                          details=f"干燥规定{rec['required_minutes']}分钟，"
                                  f"提前{remaining}秒尝试上架，已拦截")
        raise DomainError(f"干燥未满规定时长（还剩{remaining}秒），已记录干燥提前", "early_dry")

    with tx(conn):
        if not _complete(conn, rec, staff_id, at):
            raise DomainError("玩具当前不在干燥状态，无法上架", "bad_transition")
    return {"record_id": record_id, "status": "done"}


def complete_due_drying(conn, now: Optional[datetime] = None) -> int:
    """调度器调用：干燥到期的玩具自动回到清洁库。返回完成数。"""
    at = now_iso(now)
    rows = conn.execute(
        "SELECT * FROM drying_records WHERE status='drying' AND due_at<=?", (at,)).fetchall()
    done = 0
    with tx(conn):
        for rec in rows:
            if _complete(conn, rec, None, at):
                done += 1
    return done
