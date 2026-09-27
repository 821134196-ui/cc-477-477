"""测试公共基座：临时文件库 + 最小种子数据。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import Database  # noqa: E402
from app.device_adapter import MockDisinfectionDevice  # noqa: E402
from app.domain import MATERIAL_DRYING_MINUTES  # noqa: E402

T0 = datetime(2026, 9, 27, 9, 0, 0, tzinfo=timezone.utc)


class BaseCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Database(os.path.join(self.tmp.name, "test.db"))
        self.adapter = MockDisinfectionDevice()
        with self.db.connect() as conn:
            for name, role in [("护士长", "head_nurse"), ("护士", "nurse"),
                               ("保洁", "cleaner"), ("前台", "front_desk")]:
                conn.execute("INSERT INTO staff (name, role) VALUES (?,?)", (name, role))
            # 1=护士长 2=护士 3=保洁 4=前台
            conn.execute("INSERT INTO zones (code,name,age_min,age_max,capacity,rotation_minutes)"
                         " VALUES ('ZA','A区',0,3,2,120)")
            conn.execute("INSERT INTO zones (code,name,age_min,age_max,capacity,rotation_minutes)"
                         " VALUES ('ZB','B区',4,12,10,120)")
            conn.execute("INSERT INTO recovery_bins (code,zone_id,name) VALUES ('BIN-A',1,'A箱')")
            conn.execute("INSERT INTO recovery_bins (code,zone_id,name) VALUES ('BIN-B',2,'B箱')")
            conn.commit()
        self.head_nurse, self.nurse, self.cleaner, self.front_desk = 1, 2, 3, 4

    def add_toy(self, code, material="plastic", age_min=0, age_max=3,
                parts=1, drying=None):
        with self.db.connect() as conn:
            cur = conn.execute(
                "INSERT INTO toys (code,name,material,age_min,age_max,drying_minutes,parts_count)"
                " VALUES (?,?,?,?,?,?,?)",
                (code, code + "号", material, age_min, age_max,
                 drying if drying is not None else MATERIAL_DRYING_MINUTES[material], parts))
            conn.commit()
            return cur.lastrowid

    def toy_status(self, toy_id):
        with self.db.connect() as conn:
            return conn.execute("SELECT status FROM toys WHERE id=?",
                                (toy_id,)).fetchone()["status"]

    def exceptions(self, type_=None):
        with self.db.connect() as conn:
            if type_:
                return conn.execute(
                    "SELECT * FROM exception_events WHERE type=?", (type_,)).fetchall()
            return conn.execute("SELECT * FROM exception_events").fetchall()

    def active_isolations(self, toy_id=None):
        with self.db.connect() as conn:
            sql = "SELECT * FROM isolations WHERE status='active'"
            args = ()
            if toy_id:
                sql += " AND toy_id=?"
                args = (toy_id,)
            return conn.execute(sql, args).fetchall()

    def make_cleaned_toy(self, code, material="plastic", age_min=0, age_max=3, zone=1):
        """快捷构造：投放→回收→清洁，返回 toy_id。"""
        from app.services import cleaning, deployment, recovery
        tid = self.add_toy(code, material, age_min, age_max)
        with self.db.connect() as conn:
            deployment.deploy_toy(conn, tid, zone, self.nurse, now=T0)
            recovery.scan_recovery(conn, code, "BIN-A" if zone == 1 else "BIN-B",
                                   self.nurse, now=T0 + timedelta(minutes=30))
            method = {"plastic": "soak", "plush": "machine_wash", "wood": "wipe",
                      "silicone": "soak", "metal": "wipe", "electronic": "wipe"}[material]
            cleaning.clean_toy(conn, tid, method, self.cleaner,
                               now=T0 + timedelta(minutes=40))
        return tid
