"""回收扫描：扫码入回收箱，错区记录，漏扫由调度器巡检。"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from ..db import log_exception, log_turnover, parse
from ..domain import EXC_MISSED_SCAN, EXC_WRONG_ZONE, DomainError
from .core import get_staff, now_iso, require_role, tx


def scan_recovery(conn, toy_code: str, bin_code: str, staff_id: int,
                  now: Optional[datetime] = None) -> dict:
    staff = get_staff(conn, staff_id)
    require_role(staff, "recover")
    toy = conn.execute("SELECT * FROM toys WHERE code=?", (toy_code,)).fetchone()
    if not toy:
        raise DomainError(f"玩具编号不存在: {toy_code}", "not_found")
    bin_ = conn.execute(
        "SELECT * FROM recovery_bins WHERE code=?", (bin_code,)).fetchone()
    if not bin_:
        raise DomainError(f"回收箱编号不存在: {bin_code}", "not_found")
    if toy["status"] != "deployed":
        raise DomainError(f"玩具{toy_code}当前不在候诊区，无法回收", "bad_transition")

    dep = conn.execute(
        "SELECT * FROM deployments WHERE toy_id=? AND status='active'",
        (toy["id"],)).fetchone()
    at = now_iso(now)

    with tx(conn):
        # 扫错回收箱（不属于投放区域）→ 玩具错区异常，仍正常回收
        if dep and bin_["zone_id"] != dep["zone_id"]:
            log_exception(conn, EXC_WRONG_ZONE, toy_id=toy["id"], actor_id=staff_id,
                          details=f"玩具{toy_code}投放于区域{dep['zone_id']}，"
                                  f"却扫入区域{bin_['zone_id']}的回收箱{bin_code}", at=at)
        conn.execute(
            "UPDATE deployments SET status='recovered', recovered_at=? WHERE id=?",
            (at, dep["id"] if dep else None))
        conn.execute(
            "INSERT INTO recovery_events (toy_id, bin_id, deployment_id, scanned_by, scanned_at)"
            " VALUES (?,?,?,?,?)",
            (toy["id"], bin_["id"], dep["id"] if dep else None, staff_id, at))
        conn.execute(
            "UPDATE toys SET status='pending_clean', current_zone_id=NULL, version=version+1"
            " WHERE id=?", (toy["id"],))
        log_turnover(conn, toy["id"], "deployed", "pending_clean", staff_id,
                     f"扫入回收箱{bin_code}，进入待清洁", at)
    return {"toy_id": toy["id"], "status": "pending_clean"}


def flag_missed_scans(conn, grace_minutes: int = 15,
                      now: Optional[datetime] = None) -> int:
    """调度器调用：超过轮换时点+宽限仍未回收扫描 → 回收漏扫异常。返回新标记数。"""
    at = now_iso(now)
    rows = conn.execute(
        "SELECT d.*, t.code toy_code FROM deployments d JOIN toys t ON t.id=d.toy_id"
        " WHERE d.status='active' AND d.missed_notified=0").fetchall()
    count = 0
    with tx(conn):
        for d in rows:
            due = parse(d["rotate_due_at"])
            if (parse(at) - due).total_seconds() > grace_minutes * 60:
                log_exception(conn, EXC_MISSED_SCAN, toy_id=d["toy_id"], actor_id=None,
                              details=f"玩具{d['toy_code']}应于{d['rotate_due_at']}轮换回收，"
                                      f"超过宽限{grace_minutes}分钟仍未扫描", at=at)
                conn.execute("UPDATE deployments SET missed_notified=1 WHERE id=?", (d["id"],))
                count += 1
    return count
