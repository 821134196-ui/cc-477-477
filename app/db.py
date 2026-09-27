"""SQLite 数据访问层：连接管理、建表、周转事件与异常记录的写入助手。"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Optional

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS staff (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    name  TEXT NOT NULL,
    role  TEXT NOT NULL CHECK (role IN ('head_nurse','nurse','cleaner','front_desk'))
);

CREATE TABLE IF NOT EXISTS zones (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    code             TEXT UNIQUE NOT NULL,
    name             TEXT NOT NULL,
    age_min          INTEGER NOT NULL DEFAULT 0,
    age_max          INTEGER NOT NULL DEFAULT 12,
    capacity         INTEGER NOT NULL DEFAULT 12,
    rotation_minutes INTEGER NOT NULL DEFAULT 120   -- 投放后多久应轮换回收
);

CREATE TABLE IF NOT EXISTS recovery_bins (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    code    TEXT UNIQUE NOT NULL,
    zone_id INTEGER NOT NULL REFERENCES zones(id),
    name    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS toys (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    code            TEXT UNIQUE NOT NULL,          -- 玩具编号
    name            TEXT NOT NULL,
    material        TEXT NOT NULL,
    age_min         INTEGER NOT NULL,
    age_max         INTEGER NOT NULL,
    status          TEXT NOT NULL DEFAULT 'in_storage',
    current_zone_id INTEGER REFERENCES zones(id),
    drying_minutes  INTEGER NOT NULL,              -- 规定干燥时长
    parts_count     INTEGER NOT NULL DEFAULT 1,    -- 应有零件数
    version         INTEGER NOT NULL DEFAULT 0     -- 乐观锁
);

-- 区域投放表
CREATE TABLE IF NOT EXISTS deployments (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    toy_id        INTEGER NOT NULL REFERENCES toys(id),
    zone_id       INTEGER NOT NULL REFERENCES zones(id),
    slot          TEXT NOT NULL,                   -- 投放时段
    deployed_by   INTEGER NOT NULL REFERENCES staff(id),
    deployed_at   TEXT NOT NULL,
    rotate_due_at TEXT NOT NULL,                   -- 轮换时点
    recovered_at  TEXT,
    status        TEXT NOT NULL DEFAULT 'active',  -- active / recovered / expired
    missed_notified INTEGER NOT NULL DEFAULT 0     -- 漏扫异常是否已记录
);

-- 同一玩具最多一条进行中的投放：数据库级排他保证
CREATE UNIQUE INDEX IF NOT EXISTS uq_active_deployment
    ON deployments(toy_id) WHERE status = 'active';

-- 回收扫描
CREATE TABLE IF NOT EXISTS recovery_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    toy_id        INTEGER NOT NULL REFERENCES toys(id),
    bin_id        INTEGER NOT NULL REFERENCES recovery_bins(id),
    deployment_id INTEGER REFERENCES deployments(id),
    scanned_by    INTEGER NOT NULL REFERENCES staff(id),
    scanned_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cleaning_tasks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    toy_id      INTEGER NOT NULL REFERENCES toys(id),
    method      TEXT NOT NULL,                     -- wipe / soak / machine_wash
    started_by  INTEGER NOT NULL REFERENCES staff(id),
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    status      TEXT NOT NULL DEFAULT 'in_progress' -- in_progress / done
);

-- 消毒批次（按材质组批）
CREATE TABLE IF NOT EXISTS disinfection_batches (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    code         TEXT UNIQUE NOT NULL,
    material     TEXT NOT NULL,
    method       TEXT NOT NULL,
    device_id    TEXT,
    status       TEXT NOT NULL DEFAULT 'open',     -- open / submitted / completed / failed / isolated
    created_by   INTEGER NOT NULL REFERENCES staff(id),
    created_at   TEXT NOT NULL,
    submitted_at TEXT,
    completed_at TEXT,
    receipt      TEXT                              -- 设备回执 JSON
);

CREATE TABLE IF NOT EXISTS disinfection_batch_items (
    batch_id INTEGER NOT NULL REFERENCES disinfection_batches(id),
    toy_id   INTEGER NOT NULL REFERENCES toys(id),
    PRIMARY KEY (batch_id, toy_id)
);

-- 干燥计时
CREATE TABLE IF NOT EXISTS drying_records (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    toy_id           INTEGER NOT NULL REFERENCES toys(id),
    batch_id         INTEGER NOT NULL REFERENCES disinfection_batches(id),
    started_at       TEXT NOT NULL,
    required_minutes INTEGER NOT NULL,
    due_at           TEXT NOT NULL,                -- 干燥完成时点
    completed_at     TEXT,
    status           TEXT NOT NULL DEFAULT 'drying' -- drying / done
);

-- 破损维修
CREATE TABLE IF NOT EXISTS damage_reports (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    toy_id        INTEGER NOT NULL REFERENCES toys(id),
    damage_type   TEXT NOT NULL,                   -- sharp_edge / missing_part / contamination / other
    description   TEXT,
    photo_path    TEXT,                            -- 本地目录下的照片
    reported_by   INTEGER NOT NULL REFERENCES staff(id),
    reported_at   TEXT NOT NULL,
    repair_status TEXT NOT NULL DEFAULT 'pending', -- pending / in_repair / repaired / scrapped
    repaired_at   TEXT,
    repair_note   TEXT
);

-- 隔离复核
CREATE TABLE IF NOT EXISTS isolations (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    toy_id           INTEGER NOT NULL REFERENCES toys(id),
    reason           TEXT NOT NULL,
    batch_id         INTEGER REFERENCES disinfection_batches(id),
    damage_report_id INTEGER REFERENCES damage_reports(id),
    isolated_by      INTEGER NOT NULL REFERENCES staff(id),
    isolated_at      TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'active', -- active / released
    released_by      INTEGER REFERENCES staff(id),
    released_at      TEXT,
    release_note     TEXT
);

-- 异常分类记录（错区/漏扫/材质不兼容/消毒失败/干燥提前/零件缺失/批量隔离）
CREATE TABLE IF NOT EXISTS exception_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    type       TEXT NOT NULL,
    toy_id     INTEGER,
    batch_id   INTEGER,
    actor_id   INTEGER,
    details    TEXT,
    created_at TEXT NOT NULL
);

-- 周转事件流水
CREATE TABLE IF NOT EXISTS turnover_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    toy_id      INTEGER NOT NULL,
    from_status TEXT,
    to_status   TEXT NOT NULL,
    actor_id    INTEGER,
    details     TEXT,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_turnover_toy ON turnover_events(toy_id);
CREATE INDEX IF NOT EXISTS idx_exception_type ON exception_events(type);
CREATE INDEX IF NOT EXISTS idx_isolation_batch ON isolations(batch_id);
"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


class Database:
    """每个操作开一条连接（SQLite 串行写 + 超时等待即可保证并发安全）。"""

    def __init__(self, path: str):
        self.path = path
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._memory_conn: Optional[sqlite3.Connection] = None
        if path == ":memory:":
            # 测试用内存库：保持一条常驻连接，所有操作共享
            self._memory_conn = self._new_conn()
            self._memory_conn.executescript(SCHEMA)
        else:
            with self.connect() as conn:
                conn.executescript(SCHEMA)

    def _new_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @contextmanager
    def connect(self):
        """`with db.connect() as conn:` —— 文件库用完即关，内存库共享常驻连接。"""
        if self._memory_conn is not None:
            yield self._memory_conn
            return
        conn = self._new_conn()
        try:
            yield conn
        finally:
            conn.close()


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {k: row[k] for k in row.keys()}


def log_turnover(conn, toy_id: int, from_status: Optional[str], to_status: str,
                 actor_id: Optional[int], details: str, at: str) -> None:
    conn.execute(
        "INSERT INTO turnover_events (toy_id, from_status, to_status, actor_id, details, created_at)"
        " VALUES (?,?,?,?,?,?)",
        (toy_id, from_status, to_status, actor_id, details, at),
    )


def log_exception(conn, type_: str, toy_id=None, batch_id=None, actor_id=None,
                  details: str = "", at: str = "") -> None:
    conn.execute(
        "INSERT INTO exception_events (type, toy_id, batch_id, actor_id, details, created_at)"
        " VALUES (?,?,?,?,?,?)",
        (type_, toy_id, batch_id, actor_id, details, at),
    )
