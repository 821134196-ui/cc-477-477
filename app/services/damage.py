"""破损处理：报损（照片存本地目录）→ 立即隔离 → 维修 → 复核。零件缺失另记异常。"""
from __future__ import annotations

import base64
import os
import re
from datetime import datetime
from typing import Optional

from ..db import log_exception, row_to_dict
from ..domain import (DAMAGE_LABELS, DAMAGE_TYPES, EXC_MISSING_PART, DomainError)
from .core import get_staff, get_toy, now_iso, require_role, transition, tx

_SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]")


def save_photo(photos_dir: str, toy_code: str, photo_name: str,
               photo_data_b64: str, at: str) -> str:
    """破损照片保存到本地目录，返回相对路径。"""
    os.makedirs(photos_dir, exist_ok=True)
    ext = os.path.splitext(photo_name or "")[1] or ".bin"
    fname = _SAFE_NAME.sub("_", f"{toy_code}_{at.replace(':', '').replace('+', 'Z')}{ext}")
    path = os.path.join(photos_dir, fname)
    with open(path, "wb") as f:
        f.write(base64.b64decode(photo_data_b64))
    return path


def report_damage(conn, toy_id: int, damage_type: str, description: str,
                  photo_path: Optional[str], staff_id: int,
                  now: Optional[datetime] = None) -> dict:
    """发现锐边、掉件或污染风险 → 立即隔离（普通清洁不能解除）。"""
    staff = get_staff(conn, staff_id)
    require_role(staff, "damage_report")
    if damage_type not in DAMAGE_TYPES:
        raise DomainError(f"未知破损类型: {damage_type}", "bad_request")
    toy = get_toy(conn, toy_id)
    if toy["status"] in ("isolated", "repair", "retired"):
        raise DomainError(f"玩具{toy['code']}已在隔离/维修/报废中", "conflict")
    at = now_iso(now)

    with tx(conn):
        cur = conn.execute(
            "INSERT INTO damage_reports (toy_id, damage_type, description, photo_path,"
            " reported_by, reported_at) VALUES (?,?,?,?,?,?)",
            (toy_id, damage_type, description, photo_path, staff_id, at))
        report_id = cur.lastrowid
        transition(conn, toy, "isolated", staff_id,
                   f"报损：{DAMAGE_LABELS[damage_type]}，立即隔离", at)
        conn.execute(
            "INSERT INTO isolations (toy_id, reason, damage_report_id, isolated_by, isolated_at)"
            " VALUES (?,?,?,?,?)",
            (toy_id, damage_type, report_id, staff_id, at))
        if damage_type == "missing_part":
            log_exception(conn, EXC_MISSING_PART, toy_id=toy_id, actor_id=staff_id, at=at,
                          details=f"玩具{toy['code']}零件缺失：{description or ''}")
    return {"report_id": report_id, "toy_status": "isolated"}


def start_repair(conn, report_id: int, staff_id: int,
                 now: Optional[datetime] = None) -> dict:
    staff = get_staff(conn, staff_id)
    require_role(staff, "repair")
    rep = _get_report(conn, report_id)
    if rep["repair_status"] != "pending":
        raise DomainError("该报损单不在待维修状态", "bad_transition")
    at = now_iso(now)
    with tx(conn):
        conn.execute("UPDATE damage_reports SET repair_status='in_repair' WHERE id=?",
                     (report_id,))
        transition(conn, get_toy(conn, rep["toy_id"]), "repair", staff_id,
                   f"报损单#{report_id}开始维修", at)
    return {"report_id": report_id, "repair_status": "in_repair"}


def complete_repair(conn, report_id: int, note: str, staff_id: int,
                    parts_restored: Optional[int] = None,
                    now: Optional[datetime] = None) -> dict:
    """维修完成 → 回到隔离状态，等待护士长复核解除。"""
    staff = get_staff(conn, staff_id)
    require_role(staff, "repair")
    rep = _get_report(conn, report_id)
    if rep["repair_status"] != "in_repair":
        raise DomainError("该报损单不在维修中", "bad_transition")
    at = now_iso(now)
    with tx(conn):
        conn.execute(
            "UPDATE damage_reports SET repair_status='repaired', repaired_at=?, repair_note=?"
            " WHERE id=?", (at, note, report_id))
        if parts_restored is not None:
            conn.execute("UPDATE toys SET parts_count=? WHERE id=?",
                         (parts_restored, rep["toy_id"]))
        transition(conn, get_toy(conn, rep["toy_id"]), "isolated", staff_id,
                   f"报损单#{report_id}维修完成，待护士长复核", at)
    return {"report_id": report_id, "repair_status": "repaired"}


def scrap(conn, report_id: int, staff_id: int,
          now: Optional[datetime] = None) -> dict:
    """报废：仅护士长；同时关闭关联隔离单。"""
    staff = get_staff(conn, staff_id)
    if staff["role"] != "head_nurse":
        raise DomainError("仅护士长可执行报废", "forbidden")
    rep = _get_report(conn, report_id)
    if rep["repair_status"] in ("repaired", "scrapped"):
        raise DomainError("该报损单已结案", "bad_transition")
    at = now_iso(now)
    with tx(conn):
        conn.execute(
            "UPDATE damage_reports SET repair_status='scrapped', repaired_at=?,"
            " repair_note='报废退役' WHERE id=?", (at, report_id))
        transition(conn, get_toy(conn, rep["toy_id"]), "retired", staff_id,
                   f"报损单#{report_id}报废", at)
        conn.execute(
            "UPDATE isolations SET status='released', released_by=?, released_at=?,"
            " release_note='报废退役，隔离关闭' WHERE damage_report_id=? AND status='active'",
            (staff_id, at, report_id))
    return {"report_id": report_id, "repair_status": "scrapped"}


def list_damage(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT d.*, t.code toy_code, t.name toy_name, s.name reporter"
        " FROM damage_reports d JOIN toys t ON t.id=d.toy_id"
        " JOIN staff s ON s.id=d.reported_by ORDER BY d.id DESC").fetchall()
    return [row_to_dict(r) for r in rows]


def _get_report(conn, report_id: int):
    rep = conn.execute("SELECT * FROM damage_reports WHERE id=?", (report_id,)).fetchone()
    if not rep:
        raise DomainError(f"报损单不存在: {report_id}", "not_found")
    return rep
