"""种子数据与演示场景：一条命令生成材质冲突、批量隔离和维修样例。

不保存真实患儿信息 —— 员工均为虚构演示账号。
"""
from __future__ import annotations

import base64
from datetime import timedelta

from .db import Database, iso, utcnow
from .device_adapter import MockDisinfectionDevice
from .domain import DomainError
from .services import cleaning, damage, deployment, disinfection, recovery

STAFF = [
    ("王慧", "head_nurse"),
    ("李婷", "nurse"),
    ("赵强", "cleaner"),
    ("陈敏", "front_desk"),
]

ZONES = [
    ("WA", "候诊A区（低幼）", 0, 3, 8, 120),
    ("WB", "候诊B区（学龄前）", 4, 7, 10, 120),
    ("WC", "候诊C区（学龄）", 8, 12, 10, 90),
]

# (code, name, material, age_min, age_max, parts)
TOYS = [
    ("TOY-001", "叠叠乐", "plastic", 0, 3, 7),
    ("TOY-002", "形状配对盒", "plastic", 1, 4, 10),
    ("TOY-003", "洗澡小鸭", "plastic", 0, 3, 1),
    ("TOY-004", "布偶小熊", "plush", 0, 5, 1),
    ("TOY-005", "安抚巾", "plush", 0, 2, 1),
    ("TOY-006", "毛绒小兔", "plush", 1, 6, 1),
    ("TOY-007", "木质串珠", "wood", 2, 6, 12),
    ("TOY-008", "木质拼图", "wood", 3, 7, 9),
    ("TOY-009", "原木积木组", "wood", 2, 8, 24),
    ("TOY-010", "硅胶咬牙胶", "silicone", 0, 2, 1),
    ("TOY-011", "硅胶感官球", "silicone", 0, 4, 1),
    ("TOY-012", "硅胶堆叠杯", "silicone", 1, 5, 6),
    ("TOY-013", "金属小车", "metal", 3, 8, 4),
    ("TOY-014", "磁力片组", "metal", 4, 12, 32),
    ("TOY-015", "金属敲琴", "metal", 2, 7, 9),
    ("TOY-016", "有声故事机", "electronic", 2, 8, 1),
    ("TOY-017", "电子学习板", "electronic", 4, 10, 1),
    ("TOY-018", "口腔模型教具", "plastic", 4, 12, 3),
]

# 演示用破损照片（SVG，本地目录保存）
_DEMO_PHOTO_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="320" height="200">
<rect width="320" height="200" fill="#FDECEA"/>
<rect x="60" y="60" width="200" height="90" rx="8" fill="#F4908A"/>
<path d="M120 60 L150 105 L135 150" stroke="#7A2E28" stroke-width="5" fill="none"/>
<text x="160" y="180" font-size="16" text-anchor="middle" fill="#7A2E28">边缘开裂锐边示意</text>
</svg>"""


def is_fresh(db: Database) -> bool:
    with db.connect() as conn:
        return conn.execute("SELECT COUNT(*) c FROM staff").fetchone()["c"] == 0


def seed_base(db: Database) -> dict:
    """登记员工、候诊区、回收箱、玩具库。"""
    ids = {}
    with db.connect() as conn:
        for name, role in STAFF:
            cur = conn.execute("INSERT INTO staff (name, role) VALUES (?,?)", (name, role))
            ids[role] = cur.lastrowid
        for code, name, amin, amax, cap, rot in ZONES:
            cur = conn.execute(
                "INSERT INTO zones (code, name, age_min, age_max, capacity, rotation_minutes)"
                " VALUES (?,?,?,?,?,?)", (code, name, amin, amax, cap, rot))
            zid = cur.lastrowid
            conn.execute(
                "INSERT INTO recovery_bins (code, zone_id, name) VALUES (?,?,?)",
                (f"BIN-{code}1", zid, f"{name}回收箱"))
        from .domain import MATERIAL_DRYING_MINUTES
        for code, name, mat, amin, amax, parts in TOYS:
            conn.execute(
                "INSERT INTO toys (code, name, material, age_min, age_max, drying_minutes,"
                " parts_count) VALUES (?,?,?,?,?,?,?)",
                (code, name, mat, amin, amax, MATERIAL_DRYING_MINUTES[mat], parts))
        conn.commit()
    return ids


def _toy_id(conn, code: str) -> int:
    return conn.execute("SELECT id FROM toys WHERE code=?", (code,)).fetchone()["id"]


def run_demo(db: Database, photos_dir: str, staff_ids: dict,
             adapter: MockDisinfectionDevice) -> None:
    """演示场景：正常流转 + 材质冲突 + 批量隔离 + 破损维修 + 漏扫样例。"""
    nurse, cleaner, head_nurse = staff_ids["nurse"], staff_ids["cleaner"], staff_ids["head_nurse"]
    now = utcnow()

    with db.connect() as conn:
        z = {r["code"]: r["id"] for r in conn.execute("SELECT * FROM zones")}
        t = {row[0]: _toy_id(conn, row[0]) for row in TOYS}

    # 1) 正常投放：三个候诊区各投几件适龄玩具
    for code, zone in [("TOY-001", "WA"), ("TOY-004", "WA"), ("TOY-010", "WA"),
                       ("TOY-008", "WB"), ("TOY-013", "WB"), ("TOY-016", "WB"),
                       ("TOY-014", "WC"), ("TOY-017", "WC"), ("TOY-018", "WC")]:
        with db.connect() as conn:
            deployment.deploy_toy(conn, t[code], z[zone], nurse)

    # 2) 漏扫样例：一件玩具 3 小时前投放，超过轮换时点仍未回收（调度器会标记）
    with db.connect() as conn:
        deployment.deploy_toy(conn, t["TOY-002"], z["WA"], nurse,
                              now=now - timedelta(hours=3))

    # 3) 正常周转：回收 → 清洁 → 组批 → 消毒成功 → 干燥（批次时间回拨，2分钟后到期）
    with db.connect() as conn:
        deployment.deploy_toy(conn, t["TOY-003"], z["WA"], nurse)
        recovery.scan_recovery(conn, "TOY-001", "BIN-WA1", nurse)
        recovery.scan_recovery(conn, "TOY-003", "BIN-WA1", nurse)
        cleaning.clean_toy(conn, t["TOY-001"], "soak", cleaner)
        cleaning.clean_toy(conn, t["TOY-003"], "soak", cleaner)
        batch = disinfection.create_batch(conn, "plastic", "soak",
                                          [t["TOY-001"], t["TOY-003"]], cleaner)
        disinfection.submit_batch(conn, batch["batch_id"], cleaner, adapter,
                                  now=now - timedelta(minutes=58))  # 60分钟干燥，剩2分钟

    # 4) 材质冲突样例：木质玩具误选浸泡 → 记录“材质不兼容”并拒绝，随后用正确方式清洁
    with db.connect() as conn:
        deployment.deploy_toy(conn, t["TOY-007"], z["WB"], nurse)
        recovery.scan_recovery(conn, "TOY-007", "BIN-WB1", nurse)
        try:
            cleaning.clean_toy(conn, t["TOY-007"], "soak", cleaner)
        except DomainError:
            pass  # 预期失败：材质不兼容已记录
        cleaning.clean_toy(conn, t["TOY-007"], "wipe", cleaner)

    # 5) 批量隔离样例：硅胶批次设备回执污染 → 整批隔离
    with db.connect() as conn:
        for code in ("TOY-011", "TOY-012"):
            deployment.deploy_toy(conn, t[code], z["WA"], nurse)
        for code in ("TOY-010", "TOY-011", "TOY-012"):
            recovery.scan_recovery(conn, code, "BIN-WA1", nurse)
            cleaning.clean_toy(conn, t[code], "soak", cleaner)
        batch = disinfection.create_batch(conn, "silicone", "soak",
                                          [t["TOY-010"], t["TOY-011"], t["TOY-012"]], cleaner)
        adapter.script[batch["code"]] = {"result": "failed", "contamination": True}
        disinfection.submit_batch(conn, batch["batch_id"], cleaner, adapter)

    # 6) 破损维修样例：金属小车锐边（附照片）→ 隔离 → 维修 → 待护士长复核；
    #    木质拼图零件缺失 → 隔离待修（记录“零件缺失”异常）
    photo_path = damage.save_photo(photos_dir, "TOY-013", "sharp_edge.svg",
                                   base64.b64encode(_DEMO_PHOTO_SVG.encode()).decode(),
                                   iso(now))
    with db.connect() as conn:
        rep = damage.report_damage(conn, t["TOY-013"], "sharp_edge",
                                   "车体边缘开裂出现锐边", photo_path, nurse)
        damage.start_repair(conn, rep["report_id"], cleaner)
        damage.complete_repair(conn, rep["report_id"], "已打磨锐边并复检", cleaner)
        damage.report_damage(conn, t["TOY-008"], "missing_part",
                             "拼图缺2片（应有9片）", None, nurse)

    print("演示数据已生成：材质冲突×1、批量隔离×1（3件）、破损维修×2、漏扫待标记×1")
