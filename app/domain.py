"""领域常量与规则：玩具状态机、材质-清洁方式矩阵、干燥时长、异常类型、角色。

不保存任何真实患儿信息 —— 系统只登记玩具、区域、员工与周转事件。
"""
from __future__ import annotations

# ---------------------------------------------------------------- 玩具状态机
# in_storage   清洁库（可投放）
# deployed     候诊区（已投放）
# pending_clean 待清洁（已回收）
# cleaned      已清洁（擦拭/浸泡/机洗完成，待组批消毒）
# disinfecting 消毒中（已入消毒批次）
# drying       干燥中（必须满规定时长才能回库）
# isolated     隔离中（锐边/掉件/污染风险；普通清洁不能解除）
# repair       维修中
# retired      报废
TOY_STATUSES = (
    "in_storage", "deployed", "pending_clean", "cleaned",
    "disinfecting", "drying", "isolated", "repair", "retired",
)

STATUS_LABELS = {
    "in_storage": "清洁库",
    "deployed": "候诊区",
    "pending_clean": "待清洁",
    "cleaned": "已清洁",
    "disinfecting": "消毒中",
    "drying": "干燥中",
    "isolated": "隔离中",
    "repair": "维修中",
    "retired": "已报废",
}

# 允许的状态迁移（服务层强制校验，普通清洁不含 isolated -> *）
TRANSITIONS = {
    "in_storage": {"deployed"},
    "deployed": {"pending_clean", "isolated"},
    "pending_clean": {"cleaned", "isolated"},
    "cleaned": {"disinfecting", "isolated"},
    "disinfecting": {"drying", "pending_clean", "isolated"},  # 消毒失败回待清洁
    "drying": {"in_storage", "isolated"},
    "isolated": {"repair", "pending_clean", "retired"},       # 仅护士长解除后进入待清洁
    "repair": {"isolated", "retired"},                        # 修好回隔离待复核 / 报废
    "retired": set(),
}

# ---------------------------------------------------------------- 材质与清洁
MATERIALS = ("plastic", "plush", "wood", "silicone", "metal", "electronic")

MATERIAL_LABELS = {
    "plastic": "硬塑",
    "plush": "毛绒",
    "wood": "木质",
    "silicone": "硅胶",
    "metal": "金属",
    "electronic": "电子",
}

CLEAN_METHODS = ("wipe", "soak", "machine_wash")
CLEAN_METHOD_LABELS = {"wipe": "擦拭", "soak": "浸泡", "machine_wash": "机洗"}

# 每种材质允许的清洁方式（不在列表内即“材质不兼容”）
MATERIAL_CLEAN_MATRIX = {
    "plastic":    {"soak", "machine_wash", "wipe"},
    "plush":      {"machine_wash", "wipe"},
    "wood":       {"wipe"},
    "silicone":   {"soak", "wipe"},
    "metal":      {"soak", "wipe"},
    "electronic": {"wipe"},
}

# 每种材质的默认（推荐）清洁方式
MATERIAL_DEFAULT_METHOD = {
    "plastic": "soak",
    "plush": "machine_wash",
    "wood": "wipe",
    "silicone": "soak",
    "metal": "wipe",
    "electronic": "wipe",
}

# 每种材质的规定干燥时长（分钟）—— 消毒完成后必须干燥满时长才能重新投放
MATERIAL_DRYING_MINUTES = {
    "plastic": 60,
    "plush": 240,
    "wood": 30,
    "silicone": 90,
    "metal": 45,
    "electronic": 15,
}


def method_compatible(material: str, method: str) -> bool:
    return method in MATERIAL_CLEAN_MATRIX.get(material, set())


# ---------------------------------------------------------------- 异常类型
EXC_WRONG_ZONE = "wrong_zone"                 # 玩具错区
EXC_MISSED_SCAN = "missed_scan"               # 回收漏扫
EXC_MATERIAL_INCOMPAT = "material_incompatible"  # 材质不兼容
EXC_DISINFECTION_FAILED = "disinfection_failed"  # 消毒失败
EXC_EARLY_DRY = "early_dry"                   # 干燥提前
EXC_MISSING_PART = "missing_part"             # 零件缺失
EXC_BATCH_ISOLATION = "batch_isolation"       # 批量隔离

EXCEPTION_TYPES = (
    EXC_WRONG_ZONE, EXC_MISSED_SCAN, EXC_MATERIAL_INCOMPAT,
    EXC_DISINFECTION_FAILED, EXC_EARLY_DRY, EXC_MISSING_PART,
    EXC_BATCH_ISOLATION,
)

EXCEPTION_LABELS = {
    EXC_WRONG_ZONE: "玩具错区",
    EXC_MISSED_SCAN: "回收漏扫",
    EXC_MATERIAL_INCOMPAT: "材质不兼容",
    EXC_DISINFECTION_FAILED: "消毒失败",
    EXC_EARLY_DRY: "干燥提前",
    EXC_MISSING_PART: "零件缺失",
    EXC_BATCH_ISOLATION: "批量隔离",
}

# ---------------------------------------------------------------- 破损类型
DAMAGE_TYPES = ("sharp_edge", "missing_part", "contamination", "other")
DAMAGE_LABELS = {
    "sharp_edge": "锐边",
    "missing_part": "零件缺失",
    "contamination": "污染风险",
    "other": "其他",
}

# ---------------------------------------------------------------- 角色权限
ROLE_HEAD_NURSE = "head_nurse"   # 护士长：解除隔离、复核
ROLE_NURSE = "nurse"             # 护士：投放、回收、报损
ROLE_CLEANER = "cleaner"         # 保洁：清洁、消毒、干燥
ROLE_FRONT_DESK = "front_desk"   # 前台：只能查看可用数量与轮换时间

ROLES = (ROLE_HEAD_NURSE, ROLE_NURSE, ROLE_CLEANER, ROLE_FRONT_DESK)
ROLE_LABELS = {
    ROLE_HEAD_NURSE: "护士长",
    ROLE_NURSE: "护士",
    ROLE_CLEANER: "保洁员",
    ROLE_FRONT_DESK: "前台",
}

# 各操作允许的角色（服务层与 API 双层校验）
OP_ROLES = {
    "deploy": {ROLE_HEAD_NURSE, ROLE_NURSE},
    "recover": {ROLE_HEAD_NURSE, ROLE_NURSE, ROLE_CLEANER},
    "clean": {ROLE_HEAD_NURSE, ROLE_CLEANER},
    "disinfect": {ROLE_HEAD_NURSE, ROLE_CLEANER},
    "dry_release": {ROLE_HEAD_NURSE, ROLE_CLEANER},
    "damage_report": {ROLE_HEAD_NURSE, ROLE_NURSE, ROLE_CLEANER},
    "repair": {ROLE_HEAD_NURSE, ROLE_CLEANER},
    "isolate_release": {ROLE_HEAD_NURSE},  # 仅护士长
}

# ---------------------------------------------------------------- 投放时段
SLOTS = ("morning", "afternoon", "evening")
SLOT_LABELS = {"morning": "上午班", "afternoon": "下午班", "evening": "晚间班"}


def slot_for_hour(hour: int) -> str:
    if hour < 12:
        return "morning"
    if hour < 17:
        return "afternoon"
    return "evening"


class DomainError(Exception):
    """业务规则冲突（状态机非法、排他失败、权限不足等）。"""

    def __init__(self, message: str, code: str = "conflict"):
        super().__init__(message)
        self.code = code
