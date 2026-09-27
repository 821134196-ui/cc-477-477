"""领域常量：区域、状态、材质、清洁方式、角色与事件类型。"""

# --- 区域（同一玩具同一时刻只能处于一个区域） ---
AREA_CLEAN_STORE = "clean_store"   # 清洁库
AREA_WAITING = "waiting"           # 候诊区
AREA_RECYCLE = "recycle_bin"       # 回收箱（待清洁）
AREA_WASH = "wash"                 # 清洁中
AREA_DRYING = "drying"             # 干燥中
AREA_QUARANTINE = "quarantine"     # 隔离区

AREA_LABELS = {
    AREA_CLEAN_STORE: "清洁库",
    AREA_WAITING: "候诊区",
    AREA_RECYCLE: "回收箱",
    AREA_WASH: "清洁中",
    AREA_DRYING: "干燥区",
    AREA_QUARANTINE: "隔离区",
}

# 玩具生命周期状态
ST_CLEAN = "clean"                 # 在清洁库，可投放
ST_DEPLOYED = "deployed"           # 在候诊区
ST_PENDING_DIRTY = "pending_dirty"  # 回收箱，待清洁
ST_WASHING = "washing"             # 清洁/消毒中
ST_DRYING = "drying"               # 干燥中（未到规定时长，不可投放）
ST_QUARANTINED = "quarantined"     # 隔离中（普通清洁不能解除）
ST_REPAIR = "repair"               # 维修中（隔离的一种处置去向）
ST_RETIRED = "retired"             # 报废

# --- 材质 ---
MAT_PLASTIC = "plastic"     # 硬质塑料
MAT_SILICONE = "silicone"   # 硅胶
MAT_FABRIC = "fabric"       # 布艺
MAT_WOOD = "wood"           # 木质
MAT_METAL = "metal"         # 金属配件

MATERIALS = [MAT_PLASTIC, MAT_SILICONE, MAT_FABRIC, MAT_WOOD, MAT_METAL]
MATERIAL_LABELS = {
    MAT_PLASTIC: "硬质塑料",
    MAT_SILICONE: "硅胶",
    MAT_FABRIC: "布艺",
    MAT_WOOD: "木质",
    MAT_METAL: "金属",
}

# --- 清洁方式 ---
CLEAN_WIPE = "wipe"      # 擦拭
CLEAN_SOAK = "soak"      # 浸泡
CLEAN_MACHINE = "machine"  # 机洗

CLEAN_METHODS = [CLEAN_WIPE, CLEAN_SOAK, CLEAN_MACHINE]
CLEAN_LABELS = {CLEAN_WIPE: "擦拭", CLEAN_SOAK: "浸泡", CLEAN_MACHINE: "机洗"}

# 材质允许的清洁方式（材质不兼容 -> 记录 incident 并拒绝）
MATERIAL_CLEAN_COMPAT = {
    MAT_PLASTIC: {CLEAN_WIPE, CLEAN_SOAK, CLEAN_MACHINE},
    MAT_SILICONE: {CLEAN_WIPE, CLEAN_SOAK, CLEAN_MACHINE},
    MAT_FABRIC: {CLEAN_MACHINE},                 # 布艺只可机洗
    MAT_WOOD: {CLEAN_WIPE},                      # 木质只可擦拭，浸泡/机洗会变形开裂
    MAT_METAL: {CLEAN_WIPE, CLEAN_MACHINE},      # 金属不可浸泡（锈蚀）
}

# 材质推荐（默认）清洁方式
DEFAULT_CLEAN_METHOD = {
    MAT_PLASTIC: CLEAN_WIPE,
    MAT_SILICONE: CLEAN_SOAK,
    MAT_FABRIC: CLEAN_MACHINE,
    MAT_WOOD: CLEAN_WIPE,
    MAT_METAL: CLEAN_MACHINE,
}

# 不同材质消毒后规定干燥时长（分钟）
DRY_MINUTES = {
    MAT_PLASTIC: 20,
    MAT_SILICONE: 30,
    MAT_FABRIC: 45,
    MAT_WOOD: 15,
    MAT_METAL: 25,
}

# 候诊区轮换周期（分钟）：到达轮换时点后系统提示回收轮换
ROTATION_MINUTES = 30

# --- 角色 ---
ROLE_STAFF = "staff"        # 普通员工：投放/回收/清洁/上报隔离
ROLE_NURSE = "nurse"        # 护士长：解除隔离/复核
ROLE_FRONT = "front_desk"   # 前台：仅查看候诊区可用数量与轮换时间

ROLE_LABELS = {
    ROLE_STAFF: "清洁员工",
    ROLE_NURSE: "护士长",
    ROLE_FRONT: "前台",
}

# --- 周转事件类型（SQLite events 表，全程可追溯） ---
EV_DEPLOY = "deploy"
EV_RECALL = "recall"            # 到轮换时点由系统/员工回收
EV_SCAN_RECYCLE = "scan_recycle"
EV_BATCH_CREATE = "batch_create"
EV_DISINFECT = "disinfect"
EV_DRY_START = "dry_start"
EV_DRY_DONE = "dry_done"
EV_RETURN_STORE = "return_store"
EV_QUARANTINE = "quarantine"
EV_BATCH_QUARANTINE = "batch_quarantine"
EV_REPAIR_START = "repair_start"
EV_REPAIR_DONE = "repair_done"
EV_RELEASE = "release"
EV_RETIRE = "retire"

EVENT_LABELS = {
    EV_DEPLOY: "投放候诊区",
    EV_RECALL: "轮换回收",
    EV_SCAN_RECYCLE: "回收扫描",
    EV_BATCH_CREATE: "组建消毒批次",
    EV_DISINFECT: "消毒完成（设备回执）",
    EV_DRY_START: "开始干燥",
    EV_DRY_DONE: "干燥完成",
    EV_RETURN_STORE: "回到清洁库",
    EV_QUARANTINE: "单件隔离",
    EV_BATCH_QUARANTINE: "批量隔离",
    EV_REPAIR_START: "送修",
    EV_REPAIR_DONE: "维修完成",
    EV_RELEASE: "解除隔离",
    EV_RETIRE: "报废",
}

# --- 异常/违规登记类型（incidents 表） ---
INC_WRONG_AREA = "wrong_area"                 # 玩具错区
INC_MISSED_SCAN = "missed_scan"               # 回收漏扫
INC_MATERIAL_INCOMPAT = "material_incompat"   # 材质不兼容
INC_DISINFECT_FAILED = "disinfect_failed"     # 消毒失败
INC_DRY_EARLY = "dry_early"                   # 干燥提前（未到时点试图投放）
INC_PART_MISSING = "part_missing"             # 零件缺失
INC_BATCH_ISOLATION = "batch_isolation"       # 批量隔离

INCIDENT_LABELS = {
    INC_WRONG_AREA: "玩具错区",
    INC_MISSED_SCAN: "回收漏扫",
    INC_MATERIAL_INCOMPAT: "材质清洁不兼容",
    INC_DISINFECT_FAILED: "消毒失败",
    INC_DRY_EARLY: "干燥提前取用",
    INC_PART_MISSING: "零件缺失",
    INC_BATCH_ISOLATION: "批量隔离",
}

# 隔离原因
Q_SHARP_EDGE = "sharp_edge"   # 锐边
INC_LOOSE_PART = "loose_part"  # 掉件（incident 用 part_missing）
Q_CONTAMINATION = "contamination"  # 污染风险
Q_BATCH_RISK = "batch_risk"   # 批次风险（设备消毒失败等）

QUARANTINE_REASONS = {
    Q_SHARP_EDGE: "锐边",
    INC_LOOSE_PART: "掉件",
    Q_CONTAMINATION: "污染风险",
    Q_BATCH_RISK: "批次污染风险",
}
