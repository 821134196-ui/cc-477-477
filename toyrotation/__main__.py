"""本地启动入口：

    python -m toyrotation

一条命令完成：重建 SQLite、生成材质冲突/批量隔离/维修等样例、
启动进程内干燥与轮换计时、打开 Web 服务。

可选参数：
  --port PORT           端口（默认 8000）
  --host HOST           地址（默认 127.0.0.1）
  --db PATH             数据库路径（默认 data/toyrotation.db）
  --minute-seconds N    一个“规定分钟”对应的真实秒数（默认 10，便于演示干燥/轮换）
  --no-reset            保留现有数据库，不重建
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from .adapters import MockSterilizerAdapter
from .app import serve
from .db import Database
from .scheduler import RotationScheduler
from .seed import seed_demo
from .service import ToyRotationService


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="儿童牙科候诊玩具消毒与轮换系统")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--db", default="data/toyrotation.db")
    parser.add_argument("--minute-seconds", type=int, default=10,
                        help="一个规定分钟对应的真实秒数（演示用，生产请设为 60）")
    parser.add_argument("--no-reset", action="store_true", help="保留现有数据库")
    args = parser.parse_args(argv)

    db_path = Path(args.db)
    if not args.no_reset and db_path.exists():
        db_path.unlink()
        for suffix in ("-wal", "-shm"):
            p = Path(str(db_path) + suffix)
            if p.exists():
                p.unlink()

    db = Database(db_path)
    svc = ToyRotationService(db, adapter=MockSterilizerAdapter(),
                             photo_dir=Path("data/photos"),
                             minute_seconds=args.minute_seconds)

    if not args.no_reset:
        print("正在生成演示样例…")
        summary = seed_demo(svc)
        print(f"  · 消毒干燥中批次：{summary.get('drying_batch')}")
        mc = summary.get("material_conflict")
        if mc:
            print(f"  · 材质冲突：{mc['toy']} 误入机洗批次 {mc['batch']}（已登记并拒绝）")
        bq = summary.get("batch_quarantine")
        if bq:
            print(f"  · 批量隔离：{bq['batch']}（设备 {bq['device']} 消毒失败）")
        print(f"  · 维修样例：{summary.get('repair_sample')} 已维修完成，待护士长复核")
        print(f"  · 锐边隔离：{summary.get('sharp_edge_sample')}")
        print(f"  · 已登记异常：{('、'.join(dict.fromkeys(summary['incidents']))) or '无'}")

    scheduler = RotationScheduler(svc, interval=2.0)
    scheduler.start()
    print(f"进程内调度已启动（每 2 秒检查干燥完成与轮换时点；"
          f"1 规定分钟 = {args.minute_seconds} 秒）")

    try:
        serve(svc, host=args.host, port=args.port)
    finally:
        scheduler.stop()
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
