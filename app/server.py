"""本地启动入口：python3 -m app.server [--demo] [--db PATH] [--port N]

--demo  首次启动时生成演示数据：材质冲突、批量隔离、破损维修样例。
"""
from __future__ import annotations

import argparse
import logging
import os

from .api import Api, serve
from .db import Database
from .device_adapter import MockDisinfectionDevice
from .scheduler import Scheduler
from .seed import is_fresh, run_demo, seed_base

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main() -> None:
    parser = argparse.ArgumentParser(description="儿童牙科候诊玩具消毒与轮换系统")
    parser.add_argument("--db", default=os.path.join(BASE_DIR, "data", "toys.db"))
    parser.add_argument("--photos", default=os.path.join(BASE_DIR, "photos"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--demo", action="store_true", help="生成演示样例数据")
    parser.add_argument("--interval", type=float, default=5.0, help="调度巡检间隔（秒）")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    db = Database(args.db)
    adapter = MockDisinfectionDevice()
    os.makedirs(args.photos, exist_ok=True)

    if is_fresh(db):
        staff_ids = seed_base(db)
        print("基础数据已初始化：4名员工、3个候诊区、18件玩具")
        if args.demo:
            run_demo(db, args.photos, staff_ids, adapter)
    elif args.demo:
        print("数据库已存在，跳过演示数据生成（删除 data/toys.db 可重新生成）")

    scheduler = Scheduler(db, interval=args.interval)
    scheduler.start()

    api = Api(db, adapter, args.photos)
    web_dir = os.path.join(BASE_DIR, "web")
    try:
        serve(api, web_dir, args.photos, args.host, args.port)
    finally:
        scheduler.stop()


if __name__ == "__main__":
    main()
