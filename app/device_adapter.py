"""模拟消毒设备适配器：提交批次返回设备回执。

真实设备对接时替换 run_cycle 即可（HTTP/串口协议），服务层不感知。
script 参数可预置特定批次的结果，用于演示与测试消毒失败/污染场景。
"""
from __future__ import annotations

import itertools
from typing import Optional

from .db import iso, utcnow


class MockDisinfectionDevice:
    def __init__(self, device_id: str = "UV-CAB-01",
                 script: Optional[dict] = None):
        self.device_id = device_id
        # batch_code -> {"result": "success"|"failed", "contamination": bool, "message": str}
        self.script = dict(script or {})
        self._cycles = itertools.count(1)

    def run_cycle(self, batch_code: str, method: str, item_count: int) -> dict:
        cycle = next(self._cycles)
        outcome = self.script.get(batch_code,
                                  {"result": "success", "contamination": False})
        result = outcome.get("result", "success")
        contamination = bool(outcome.get("contamination", False))
        if result == "success":
            message = f"消毒周期完成，{item_count}件合格"
        elif contamination:
            message = "生物指示剂阳性，检出污染风险"
        else:
            message = outcome.get("message", "温度未达标，消毒周期失败")
        return {
            "device_id": self.device_id,
            "cycle_id": f"CYC-{cycle:05d}",
            "batch_code": batch_code,
            "method": method,
            "item_count": item_count,
            "result": result,
            "contamination": contamination,
            "message": message,
            "finished_at": iso(utcnow()),
        }
