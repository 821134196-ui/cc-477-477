"""种子数据与演示样例。

一条本地启动命令（python -m toyrotation）即重建数据库并生成：
- 候诊区与玩具库基础数据；
- 材质清洁不兼容冲突样例（木质误入机洗批次）；
- 设备消毒失败导致的批量隔离样例；
- 掉件隔离、破损照片、送修与维修完成待复核样例；
- 回收漏扫、玩具错区样例。
不包含任何真实患儿信息。
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

from . import models as M
from .service import ServiceError, ToyRotationService

# (编号, 名称, 图标, 材质, 年龄下限, 年龄上限, 零件数)
TOY_CATALOG = [
    ("T-001", "咬合积木组", "blocks", M.MAT_PLASTIC, 1, 3, 6),
    ("T-002", "手摇铃", "rattle", M.MAT_PLASTIC, 0, 2, 2),
    ("T-003", "形状配对盒", "cube", M.MAT_PLASTIC, 1, 4, 5),
    ("T-004", "小汽车", "car", M.MAT_PLASTIC, 2, 5, 1),
    ("T-005", "数字方块", "blocks", M.MAT_PLASTIC, 2, 6, 10),
    ("T-006", "按摩触感球", "ball", M.MAT_PLASTIC, 0, 3, 1),
    ("T-007", "套圈塔", "ring", M.MAT_PLASTIC, 1, 4, 5),
    ("T-008", "牙胶环", "ring", M.MAT_SILICONE, 0, 2, 1),
    ("T-009", "硅胶积木", "blocks", M.MAT_SILICONE, 1, 3, 4),
    ("T-010", "咬咬棒", "rattle", M.MAT_SILICONE, 0, 2, 1),
    ("T-011", "触感吸盘组", "cube", M.MAT_SILICONE, 1, 4, 4),
    ("T-012", "软胶动物配对", "puzzle", M.MAT_SILICONE, 2, 5, 6),
    ("T-013", "硅胶叠叠杯", "cube", M.MAT_SILICONE, 1, 4, 8),
    ("T-014", "布书", "book", M.MAT_FABRIC, 0, 3, 1),
    ("T-015", "安抚布偶", "ball", M.MAT_FABRIC, 0, 2, 1),
    ("T-016", "布艺骰子组", "blocks", M.MAT_FABRIC, 1, 4, 3),
    ("T-017", "触觉布毯", "book", M.MAT_FABRIC, 0, 3, 1),
    ("T-018", "木质拼图板", "puzzle", M.MAT_WOOD, 3, 6, 9),
    ("T-019", "木珠绕珠架", "ring", M.MAT_WOOD, 2, 5, 1),
    ("T-020", "实木积木桶", "blocks", M.MAT_WOOD, 2, 6, 20),
    ("T-021", "木琴", "rattle", M.MAT_WOOD, 3, 7, 1),
    ("T-022", "金属铃鼓", "ring", M.MAT_METAL, 2, 6, 1),
    ("T-023", "螺母拆装组", "cube", M.MAT_METAL, 3, 7, 12),
    ("T-024", "口琴", "rattle", M.MAT_METAL, 4, 8, 1),
]


def make_placeholder_photo(path: Path, rgb=(214, 117, 99)) -> Path:
    """用标准库生成一张有效的纯色 PNG 作为破损照片占位。"""
    w = h = 240
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))

    def chunk(typ: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + typ + data
                + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF))

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 9))
           + chunk(b"IEND", b""))
    path.write_bytes(png)
    return path


def seed_demo(svc: ToyRotationService) -> dict:
    """生成全套演示数据。返回各样例的编号便于启动时打印。"""
    svc.add_zone("Z-01", "候诊A区", 8)
    svc.add_zone("Z-02", "候诊B区", 6)
    for tid, name, icon, mat, amin, amax, parts in TOY_CATALOG:
        svc.add_toy(tid, name, mat, amin, amax, parts_total=parts, icon=icon)

    staff, nurse = "员工-李", "护士长-周"
    summary: dict = {"incidents": []}

    # 1) 先投放一批已完成一轮使用的玩具并回收扫描进入回收箱
    pre_deploy = {"Z-01": ["T-003", "T-004", "T-005", "T-019", "T-023"],
                  "Z-02": ["T-010", "T-011", "T-015"]}
    for zone, tids in pre_deploy.items():
        for tid in tids:
            svc.deploy(tid, zone, staff)
    for tid in ["T-003", "T-004", "T-005", "T-010", "T-011", "T-015", "T-019", "T-023"]:
        svc.scan_recycle(tid, staff)

    # 2) 候诊区当前陈列（可用数量与轮换时间）
    for tid, zone in [("T-001", "Z-01"), ("T-002", "Z-01"), ("T-009", "Z-02"),
                      ("T-014", "Z-02"), ("T-018", "Z-01"), ("T-022", "Z-01")]:
        svc.deploy(tid, zone, staff)
    for tid, zone in [("T-006", "Z-01"), ("T-007", "Z-01"), ("T-008", "Z-01"),
                      ("T-012", "Z-02"), ("T-013", "Z-02"), ("T-016", "Z-02"),
                      ("T-017", "Z-02")]:
        svc.deploy(tid, zone, staff)

    # 3) 正常批次：塑料 -> 消毒 -> 干燥中
    b_plastic = svc.create_batch(M.MAT_PLASTIC, staff, toy_ids=["T-003", "T-004", "T-005"])
    svc.run_disinfection(b_plastic, staff)
    svc.start_drying(b_plastic, staff)
    summary["drying_batch"] = b_plastic

    # 4) 材质冲突样例：布艺机洗批次中误加入木质玩具（只可擦拭）
    b_fabric = svc.create_batch(M.MAT_FABRIC, staff, toy_ids=["T-015"])
    try:
        svc.add_to_batch(b_fabric, "T-019", staff, method=M.CLEAN_MACHINE)
    except ServiceError as e:
        summary["material_conflict"] = {"toy": "T-019", "batch": b_fabric, "error": e.message}

    # 5) 消毒失败 -> 批量隔离样例
    b_silicone = svc.create_batch(M.MAT_SILICONE, staff, toy_ids=["T-010", "T-011"])
    svc.adapter.force_next_result(False)
    receipt = svc.run_disinfection(b_silicone, staff)
    svc.quarantine_batch(b_silicone, staff, reason=M.Q_BATCH_RISK,
                         note="设备回执温度未达标，整批隔离待查")
    summary["batch_quarantine"] = {"batch": b_silicone, "device": receipt["device_batch_no"]}

    # 6) 掉件 -> 隔离 -> 送修 -> 维修完成（仍须护士长复核）
    photo = make_placeholder_photo(svc.photo_dir / "T-002-damage.png")
    svc.report_damage("T-002", M.INC_LOOSE_PART, staff,
                      note="摇铃内珠掉落，存在误吞风险", photo_path=str(photo))
    svc.send_to_repair("T-002", staff)
    svc.complete_repair("T-002", staff, parts_restored=1, note="已补装内珠并封牢")
    summary["repair_sample"] = "T-002"

    # 7) 锐边立即隔离样例
    photo2 = make_placeholder_photo(svc.photo_dir / "T-018-damage.png", rgb=(198, 90, 78))
    svc.report_damage("T-018", M.Q_SHARP_EDGE, staff,
                      note="拼图板边缘毛刺，可能划伤", photo_path=str(photo2))
    summary["sharp_edge_sample"] = "T-018"

    # 8) 回收漏扫样例（T-001 到轮换时点仍未回收，巡检补登记）
    svc.report_missed_scan("T-001", nurse, note="巡检发现超过轮换时点，未做回收扫描")

    # 9) 玩具错区样例：登记在 Z-01，实物在 Z-02 被扫回
    svc.scan_recycle("T-022", staff, found_zone_id="Z-02")

    summary["incidents"] = [M.INCIDENT_LABELS[i["kind"]] for i in svc.incidents()]
    return summary
