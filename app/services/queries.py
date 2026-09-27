"""查询：玩具库、消毒批次（材质分组）、异常记录、周转流水。"""
from __future__ import annotations

import json
from typing import Optional

from ..db import row_to_dict


def list_toys(conn, status: Optional[str] = None,
              material: Optional[str] = None) -> list[dict]:
    sql = ("SELECT t.*, z.name zone_name,"
           " (SELECT COUNT(*) FROM isolations i WHERE i.toy_id=t.id AND i.status='active')"
           "   AS active_isolations"
           " FROM toys t LEFT JOIN zones z ON z.id=t.current_zone_id WHERE 1=1")
    args: list = []
    if status:
        sql += " AND t.status=?"
        args.append(status)
    if material:
        sql += " AND t.material=?"
        args.append(material)
    sql += " ORDER BY t.code"
    return [row_to_dict(r) for r in conn.execute(sql, args).fetchall()]


def list_batches(conn) -> list[dict]:
    """消毒批次按材质分组突出显示（前端按 material 分组渲染）。"""
    batches = conn.execute(
        "SELECT b.*, s.name creator FROM disinfection_batches b"
        " JOIN staff s ON s.id=b.created_by ORDER BY b.id DESC").fetchall()
    out = []
    for b in batches:
        d = row_to_dict(b)
        items = conn.execute(
            "SELECT t.id, t.code, t.name, t.status FROM disinfection_batch_items i"
            " JOIN toys t ON t.id=i.toy_id WHERE i.batch_id=? ORDER BY t.code",
            (b["id"],)).fetchall()
        d["items"] = [row_to_dict(r) for r in items]
        d["receipt"] = json.loads(b["receipt"]) if b["receipt"] else None
        out.append(d)
    return out


def list_exceptions(conn, type_: Optional[str] = None) -> list[dict]:
    sql = ("SELECT e.*, t.code toy_code, b.code batch_code, s.name actor_name"
           " FROM exception_events e"
           " LEFT JOIN toys t ON t.id=e.toy_id"
           " LEFT JOIN disinfection_batches b ON b.id=e.batch_id"
           " LEFT JOIN staff s ON s.id=e.actor_id")
    args: list = []
    if type_:
        sql += " WHERE e.type=?"
        args.append(type_)
    sql += " ORDER BY e.id DESC LIMIT 200"
    return [row_to_dict(r) for r in conn.execute(sql, args).fetchall()]


def list_turnover(conn, toy_id: Optional[int] = None) -> list[dict]:
    sql = ("SELECT v.*, t.code toy_code, s.name actor_name FROM turnover_events v"
           " JOIN toys t ON t.id=v.toy_id LEFT JOIN staff s ON s.id=v.actor_id")
    args: list = []
    if toy_id:
        sql += " WHERE v.toy_id=?"
        args.append(toy_id)
    sql += " ORDER BY v.id DESC LIMIT 200"
    return [row_to_dict(r) for r in conn.execute(sql, args).fetchall()]


def list_zones(conn) -> list[dict]:
    zones = [row_to_dict(r) for r in conn.execute("SELECT * FROM zones ORDER BY id")]
    bins = conn.execute(
        "SELECT b.*, z.name zone_name FROM recovery_bins b"
        " JOIN zones z ON z.id=b.zone_id ORDER BY b.id").fetchall()
    for z in zones:
        z["bins"] = [row_to_dict(b) for b in bins if b["zone_id"] == z["id"]]
    return zones
