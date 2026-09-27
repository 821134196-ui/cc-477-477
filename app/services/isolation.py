"""隔离复核：仅护士长可解除；解除后玩具必须回到待清洁重新走工序。"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from ..db import row_to_dict
from ..domain import DomainError
from .core import get_staff, get_toy, now_iso, transition, tx


def release_isolation(conn, isolation_id: int, staff_id: int, note: str = "",
                      now: Optional[datetime] = None) -> dict:
    """解除隔离：护士长专属权限；关联破损单须先修复；解除后回待清洁。"""
    staff = get_staff(conn, staff_id)
    if staff["role"] != "head_nurse":
        raise DomainError("仅护士长可解除隔离", "forbidden")
    iso_row = conn.execute(
        "SELECT * FROM isolations WHERE id=? AND status='active'",
        (isolation_id,)).fetchone()
    if not iso_row:
        raise DomainError(f"隔离单不存在或已解除: {isolation_id}", "not_found")

    if iso_row["damage_report_id"]:
        rep = conn.execute("SELECT * FROM damage_reports WHERE id=?",
                           (iso_row["damage_report_id"],)).fetchone()
        if rep and rep["repair_status"] not in ("repaired",):
            raise DomainError("破损尚未修复，不能解除隔离", "not_repaired")

    at = now_iso(now)
    with tx(conn):
        conn.execute(
            "UPDATE isolations SET status='released', released_by=?, released_at=?,"
            " release_note=? WHERE id=?",
            (staff_id, at, note or "复核通过", isolation_id))
        transition(conn, get_toy(conn, iso_row["toy_id"]), "pending_clean", staff_id,
                   f"隔离单#{isolation_id}解除，回待清洁重新走工序", at)
    return {"isolation_id": isolation_id, "status": "released"}


def release_batch_isolation(conn, batch_id: int, staff_id: int, note: str = "",
                            now: Optional[datetime] = None) -> dict:
    """按批次批量解除隔离（护士长）。"""
    staff = get_staff(conn, staff_id)
    if staff["role"] != "head_nurse":
        raise DomainError("仅护士长可解除隔离", "forbidden")
    rows = conn.execute(
        "SELECT id FROM isolations WHERE batch_id=? AND status='active'",
        (batch_id,)).fetchall()
    if not rows:
        raise DomainError("该批次没有进行中的隔离", "not_found")
    released = [release_isolation(conn, r["id"], staff_id,
                                  note or "批次复核通过", now) for r in rows]
    return {"batch_id": batch_id, "released": len(released)}


def list_isolations(conn, active_only: bool = False) -> list[dict]:
    sql = ("SELECT i.*, t.code toy_code, t.name toy_name, t.material,"
           " b.code batch_code, s.name isolated_by_name, rs.name released_by_name"
           " FROM isolations i"
           " JOIN toys t ON t.id=i.toy_id"
           " LEFT JOIN disinfection_batches b ON b.id=i.batch_id"
           " JOIN staff s ON s.id=i.isolated_by"
           " LEFT JOIN staff rs ON rs.id=i.released_by")
    if active_only:
        sql += " WHERE i.status='active'"
    sql += " ORDER BY i.id DESC"
    return [row_to_dict(r) for r in conn.execute(sql).fetchall()]
