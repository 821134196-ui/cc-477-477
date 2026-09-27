"""消毒设备模拟适配器。

真实环境中此处对接消毒机回执；本地实现为可注入结果的模拟器，
返回设备批次号、温度、时长与成功/失败回执。
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass


@dataclass
class DeviceReceipt:
    ok: bool
    device_batch_no: str
    temperature_c: float
    duration_min: int
    message: str

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "device_batch_no": self.device_batch_no,
            "temperature_c": self.temperature_c,
            "duration_min": self.duration_min,
            "message": self.message,
        }


class MockSterilizerAdapter:
    """模拟消毒设备：默认成功；可对下一次或指定批次预置失败。"""

    def __init__(self) -> None:
        self._seq = itertools.count(1)
        self._force_next: bool | None = None  # None=按规则成功
        self._fail_materials: set[str] = set()

    def force_next_result(self, ok: bool | None) -> None:
        """测试/样例用：预置下一次消毒结果。None 恢复默认成功。"""
        self._force_next = ok

    def set_material_failure(self, material: str, fail: bool = True) -> None:
        """让某材质本批次消毒失败（模拟设备对该负载不达标）。"""
        if fail:
            self._fail_materials.add(material)
        else:
            self._fail_materials.discard(material)

    def run_cycle(self, materials: list[str]) -> DeviceReceipt:
        n = next(self._seq)
        device_no = f"DEV-2026-{n:05d}"
        if self._force_next is not None:
            ok = self._force_next
            self._force_next = None
        elif self._fail_materials and any(m in self._fail_materials for m in materials):
            ok = False
        else:
            ok = True
        if ok:
            return DeviceReceipt(True, device_no, 93.0, 12, "消毒达标，设备回执正常")
        return DeviceReceipt(False, device_no, 71.5, 6, "温度/时长未达标，设备回报失败")
