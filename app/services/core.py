"""服务层公共助手：事务、时钟、角色校验、状态迁移。"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from typing import Optional

from ..db import iso, log_turnover, parse, utcnow
from ..domain import OP_ROLES, ROLE_LABELS, TRANSITIONS, STATUS_LABELS, DomainError


def now_iso(now: Optional[datetime] = None) -> str:
    return iso(now) if now else iso(utcnow())


@contextmanager
def tx(conn):
    """BEGIN IMMEDIATE：写操作串行化，并发投放时保证仅一人成功。"""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def get_staff(conn, staff_id: int):
    row = conn.execute("SELECT * FROM staff WHERE id=?", (staff_id,)).fetchone()
    if not row:
        raise DomainError(f"员工不存在: {staff_id}", "not_found")
    return row


def require_role(staff, op: str) -> None:
    if staff["role"] not in OP_ROLES[op]:
        raise DomainError(
            f"{ROLE_LABELS[staff['role']]}无权执行该操作", "forbidden")


def get_toy(conn, toy_id: int):
    row = conn.execute("SELECT * FROM toys WHERE id=?", (toy_id,)).fetchone()
    if not row:
        raise DomainError(f"玩具不存在: {toy_id}", "not_found")
    return row


def transition(conn, toy, to_status: str, actor_id: Optional[int],
               details: str, at: str):
    """校验状态机并迁移，写周转事件。返回更新后的状态。"""
    frm = toy["status"]
    if to_status not in TRANSITIONS.get(frm, set()):
        raise DomainError(
            f"非法工序流转：{STATUS_LABELS[frm]} → {STATUS_LABELS[to_status]}",
            "bad_transition")
    cur = conn.execute(
        "UPDATE toys SET status=?, version=version+1 WHERE id=? AND status=?",
        (to_status, toy["id"], frm))
    if cur.rowcount == 0:
        raise DomainError("玩具状态已被并发修改，请刷新后重试", "conflict")
    log_turnover(conn, toy["id"], frm, to_status, actor_id, details, at)
    return to_status
