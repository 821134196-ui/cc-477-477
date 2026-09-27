"""SQLite 数据层：表结构与连接管理。

所有周转事件写入 events，违规/异常写入 incidents，支持批次追溯。
线程安全：连接 check_same_thread=False，由 service 层的锁串行化写入，
关键并发操作使用 BEGIN IMMEDIATE 立即事务锁实现投放排他。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS waiting_zones (
    id        TEXT PRIMARY KEY,
    name      TEXT NOT NULL,
    capacity  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS toys (
    id               TEXT PRIMARY KEY,          -- 玩具编号，如 T-001
    name             TEXT NOT NULL,
    icon             TEXT NOT NULL DEFAULT 'cube',
    age_min          INTEGER NOT NULL,          -- 适用年龄下限（岁）
    age_max          INTEGER NOT NULL,          -- 适用年龄上限（岁）
    material         TEXT NOT NULL,
    clean_method     TEXT NOT NULL,             -- 规定清洁方式
    parts_total      INTEGER NOT NULL DEFAULT 1,-- 零件总数（含可拆卸件）
    parts_present    INTEGER NOT NULL DEFAULT 1,-- 现存零件数
    status           TEXT NOT NULL,
    current_area     TEXT NOT NULL,
    zone_id          TEXT,                      -- 投放时所在候诊区
    batch_id         TEXT,                      -- 当前所属消毒批次
    deployed_at      TEXT,                      -- 本次投放时间
    rotation_due_at  TEXT,                      -- 轮换时点
    dry_ready_at     TEXT,                      -- 干燥完成时点
    quarantined_at   TEXT,
    quarantine_reason TEXT,
    quarantine_note  TEXT,
    damaged_photo    TEXT,
    created_at       TEXT NOT NULL,
    FOREIGN KEY (zone_id) REFERENCES waiting_zones(id)
);

CREATE TABLE IF NOT EXISTS batches (
    id               TEXT PRIMARY KEY,          -- 消毒批次号，如 B-20260927-001
    material         TEXT NOT NULL,             -- 批次按材质分组（同材质同方式）
    clean_method     TEXT NOT NULL,
    dry_minutes      INTEGER NOT NULL,
    status           TEXT NOT NULL,             -- open/running/disinfected/drying/done/quarantined
    created_at       TEXT NOT NULL,
    disinfected_at   TEXT,
    device_batch_no  TEXT,
    receipt          TEXT                       -- JSON 设备回执
);

CREATE TABLE IF NOT EXISTS batch_items (
    batch_id  TEXT NOT NULL,
    toy_id    TEXT NOT NULL,
    PRIMARY KEY (batch_id, toy_id),
    FOREIGN KEY (batch_id) REFERENCES batches(id),
    FOREIGN KEY (toy_id) REFERENCES toys(id)
);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    toy_id      TEXT,
    batch_id    TEXT,
    zone_id     TEXT,
    event_type  TEXT NOT NULL,
    actor       TEXT NOT NULL,
    detail      TEXT                            -- JSON
);

CREATE TABLE IF NOT EXISTS incidents (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    kind        TEXT NOT NULL,
    toy_id      TEXT,
    batch_id    TEXT,
    zone_id     TEXT,
    actor       TEXT NOT NULL,
    note        TEXT,
    detail      TEXT,                           -- JSON
    resolved    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS damage_reports (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    toy_id      TEXT NOT NULL,
    kind        TEXT NOT NULL,                  -- sharp_edge / loose_part / contamination
    note        TEXT,
    photo_path  TEXT,
    actor       TEXT NOT NULL,
    FOREIGN KEY (toy_id) REFERENCES toys(id)
);

CREATE INDEX IF NOT EXISTS idx_events_toy ON events(toy_id);
CREATE INDEX IF NOT EXISTS idx_events_batch ON events(batch_id);
CREATE INDEX IF NOT EXISTS idx_incidents_toy ON incidents(toy_id);
CREATE INDEX IF NOT EXISTS idx_toys_area ON toys(current_area);
CREATE INDEX IF NOT EXISTS idx_toys_status ON toys(status);
"""

BATCH_STATUS_OPEN = "open"
BATCH_STATUS_RUNNING = "running"
BATCH_STATUS_DISINFECTED = "disinfected"
BATCH_STATUS_DRYING = "drying"
BATCH_STATUS_DONE = "done"
BATCH_STATUS_QUARANTINED = "quarantined"


class Database:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:" and self.path != "file::memory:?cache=shared":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # --- 基础工具 ---
    def begin_immediate(self) -> None:
        self.conn.execute("BEGIN IMMEDIATE")

    def commit(self) -> None:
        self.conn.commit()

    def rollback(self) -> None:
        self.conn.rollback()

    def execute(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, params)

    def query(self, sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, params).fetchall()

    def query_one(self, sql: str, params: tuple | dict = ()) -> sqlite3.Row | None:
        return self.conn.execute(sql, params).fetchone()

    # --- 事件 / 异常 ---
    def log_event(self, ts: str, event_type: str, actor: str,
                  toy_id: str | None = None, batch_id: str | None = None,
                  zone_id: str | None = None, detail: dict | None = None) -> int:
        cur = self.execute(
            "INSERT INTO events(ts,toy_id,batch_id,zone_id,event_type,actor,detail)"
            " VALUES (?,?,?,?,?,?,?)",
            (ts, toy_id, batch_id, zone_id, event_type, actor,
             json.dumps(detail, ensure_ascii=False) if detail else None),
        )
        return int(cur.lastrowid)

    def log_incident(self, ts: str, kind: str, actor: str,
                     toy_id: str | None = None, batch_id: str | None = None,
                     zone_id: str | None = None, note: str | None = None,
                     detail: dict | None = None) -> int:
        cur = self.execute(
            "INSERT INTO incidents(ts,kind,toy_id,batch_id,zone_id,actor,note,detail)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (ts, kind, toy_id, batch_id, zone_id, actor, note,
             json.dumps(detail, ensure_ascii=False) if detail else None),
        )
        return int(cur.lastrowid)

    def close(self) -> None:
        self.conn.close()
