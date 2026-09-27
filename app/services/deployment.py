"""区域投放表：投放（排他）、候诊区可用数量与轮换时点。"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from ..db import log_exception, parse
from ..domain import (EXC_WRONG_ZONE, SLOT_LABELS, DomainError, slot_for_hour)
from .core import get_staff, get_toy, now_iso, require_role, tx


def deploy_toy(conn, toy_id: int, zone_id: int, staff_id: int,
               now: Optional[datetime] = None) -> dict:
    """投放：同一玩具不能同时出现在两个区域；并发投放仅一人成功。"""
    staff = get_staff(conn, staff_id)
    require_role(staff, "deploy")
    zone = conn.execute("SELECT * FROM zones WHERE id=?", (zone_id,)).fetchone()
    if not zone:
        raise DomainError(f"候诊区不存在: {zone_id}", "not_found")
    toy = get_toy(conn, toy_id)
    at = now_iso(now)

    # 年龄不匹配 → 玩具错区（记录异常并拒绝）
    if toy["age_min"] > zone["age_max"] or toy["age_max"] < zone["age_min"]:
        with tx(conn):
            log_exception(conn, EXC_WRONG_ZONE, toy_id=toy_id, actor_id=staff_id,
                          details=f"玩具{toy['code']}适用{toy['age_min']}-{toy['age_max']}岁，"
                                  f"与候诊区{zone['name']}({zone['age_min']}-{zone['age_max']}岁)不匹配",
                          at=at)
        raise DomainError("玩具适用年龄与候诊区不匹配，已记录玩具错区", "wrong_zone")

    due = (parse(at) + timedelta(minutes=zone["rotation_minutes"])).isoformat()
    slot = slot_for_hour(parse(at).astimezone().hour)

    with tx(conn):
        # 容量校验
        used = conn.execute(
            "SELECT COUNT(*) c FROM deployments WHERE zone_id=? AND status='active'",
            (zone_id,)).fetchone()["c"]
        if used >= zone["capacity"]:
            raise DomainError(f"候诊区{zone['name']}已满（{used}/{zone['capacity']}）", "capacity")

        # 排他核心：仅当玩具在清洁库时才能投出，并发时只有一人 rowcount=1
        cur = conn.execute(
            "UPDATE toys SET status='deployed', current_zone_id=?, version=version+1"
            " WHERE id=? AND status='in_storage'",
            (zone_id, toy_id))
        if cur.rowcount == 0:
            raise DomainError(
                f"玩具{toy['code']}不在清洁库，不能投放（可能已被他人投出）",
                "deploy_conflict")

        conn.execute(
            "INSERT INTO deployments (toy_id, zone_id, slot, deployed_by, deployed_at, rotate_due_at)"
            " VALUES (?,?,?,?,?,?)",
            (toy_id, zone_id, slot, staff_id, at, due))
        from ..db import log_turnover
        log_turnover(conn, toy_id, "in_storage", "deployed", staff_id,
                     f"投放到{zone['name']}（{SLOT_LABELS[slot]}）", at)
    return {"toy_id": toy_id, "zone_id": zone_id, "slot": slot, "rotate_due_at": due}


def zone_summary(conn) -> list[dict]:
    """候诊区视图：只显示可用数量与下一次轮换时间（前台可见的唯一数据）。"""
    zones = conn.execute("SELECT * FROM zones ORDER BY id").fetchall()
    out = []
    for z in zones:
        row = conn.execute(
            "SELECT COUNT(*) c, MIN(rotate_due_at) next_rot FROM deployments"
            " WHERE zone_id=? AND status='active'", (z["id"],)).fetchone()
        out.append({
            "zone_id": z["id"], "code": z["code"], "name": z["name"],
            "age_min": z["age_min"], "age_max": z["age_max"],
            "available": row["c"], "capacity": z["capacity"],
            "next_rotation_at": row["next_rot"],
        })
    return out
