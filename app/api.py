"""HTTP API：stdlib http.server 实现，X-Staff-Id 头鉴权。

前台（front_desk）只能访问 /api/zones/summary 等只读汇总；
解除隔离仅限护士长 —— 服务层二次校验。
"""
from __future__ import annotations

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from .db import Database, row_to_dict
from .device_adapter import MockDisinfectionDevice
from .domain import (CLEAN_METHOD_LABELS, DAMAGE_LABELS, EXCEPTION_LABELS,
                     MATERIAL_CLEAN_MATRIX, MATERIAL_DEFAULT_METHOD,
                     MATERIAL_DRYING_MINUTES, MATERIAL_LABELS, ROLE_FRONT_DESK,
                     ROLE_LABELS, SLOT_LABELS, STATUS_LABELS, DomainError)
from .services import cleaning, damage, deployment, disinfection, drying, isolation, queries, recovery

# 前台允许访问的路径（只能查看可用数量与轮换时间）
_FRONT_DESK_OK = {"/api/meta", "/api/staff", "/api/zones/summary"}


class Api:
    def __init__(self, db: Database, adapter: MockDisinfectionDevice,
                 photos_dir: str):
        self.db = db
        self.adapter = adapter
        self.photos_dir = photos_dir

    # ------------------------------------------------------------ 路由表
    def routes(self):
        return [
            ("GET", r"/api/meta", self.meta),
            ("GET", r"/api/staff", self.staff_list),
            ("GET", r"/api/zones/summary", self.zone_summary),
            ("GET", r"/api/zones", self.zones),
            ("GET", r"/api/toys", self.toys),
            ("POST", r"/api/toys", self.toy_create),
            ("POST", r"/api/deploy", self.deploy),
            ("POST", r"/api/recover", self.recover),
            ("POST", r"/api/clean", self.clean),
            ("GET", r"/api/batches", self.batches),
            ("POST", r"/api/batches", self.batch_create),
            ("POST", r"/api/batches/(\d+)/submit", self.batch_submit),
            ("POST", r"/api/batches/(\d+)/release-isolation", self.batch_release_isolation),
            ("GET", r"/api/batches/(\d+)/trace", self.batch_trace),
            ("GET", r"/api/drying", self.drying_list),
            ("POST", r"/api/drying/(\d+)/release", self.drying_release),
            ("GET", r"/api/damage", self.damage_list),
            ("POST", r"/api/damage", self.damage_report),
            ("POST", r"/api/damage/(\d+)/repair-start", self.repair_start),
            ("POST", r"/api/damage/(\d+)/repair-done", self.repair_done),
            ("POST", r"/api/damage/(\d+)/scrap", self.scrap),
            ("GET", r"/api/isolations", self.isolations),
            ("POST", r"/api/isolations/(\d+)/release", self.isolation_release),
            ("GET", r"/api/exceptions", self.exceptions),
            ("GET", r"/api/turnover", self.turnover),
        ]

    # ------------------------------------------------------------ 分发
    def dispatch(self, method: str, path: str, query: dict, body: dict,
                 staff_id):
        staff = None
        if staff_id is not None:
            with self.db.connect() as conn:
                row = conn.execute("SELECT * FROM staff WHERE id=?", (staff_id,)).fetchone()
                staff = row_to_dict(row) if row else None
        if staff is None:
            return 401, {"error": "缺少或无效的 X-Staff-Id 头"}
        if staff["role"] == ROLE_FRONT_DESK and path not in _FRONT_DESK_OK:
            return 403, {"error": "前台只能查看候诊区可用数量与轮换时间"}

        for m, pattern, handler in self.routes():
            if m != method:
                continue
            match = re.fullmatch(pattern, path)
            if match:
                try:
                    return 200, handler(staff, query, body, *match.groups())
                except DomainError as e:
                    status = {"forbidden": 403, "not_found": 404,
                              "bad_request": 400}.get(e.code, 409)
                    return status, {"error": str(e), "code": e.code}
        return 404, {"error": "接口不存在"}

    # ------------------------------------------------------------ 各端点
    def meta(self, staff, q, b):
        return {
            "status_labels": STATUS_LABELS, "material_labels": MATERIAL_LABELS,
            "method_labels": CLEAN_METHOD_LABELS, "damage_labels": DAMAGE_LABELS,
            "exception_labels": EXCEPTION_LABELS, "role_labels": ROLE_LABELS,
            "slot_labels": SLOT_LABELS,
            "clean_matrix": {k: sorted(v) for k, v in MATERIAL_CLEAN_MATRIX.items()},
            "default_method": MATERIAL_DEFAULT_METHOD,
            "drying_minutes": MATERIAL_DRYING_MINUTES,
        }

    def staff_list(self, staff, q, b):
        with self.db.connect() as conn:
            rows = conn.execute("SELECT id, name, role FROM staff ORDER BY id").fetchall()
            return {"staff": [row_to_dict(r) for r in rows]}

    def zone_summary(self, staff, q, b):
        with self.db.connect() as conn:
            return {"zones": deployment.zone_summary(conn)}

    def zones(self, staff, q, b):
        with self.db.connect() as conn:
            return {"zones": queries.list_zones(conn)}

    def toys(self, staff, q, b):
        with self.db.connect() as conn:
            return {"toys": queries.list_toys(conn, _q1(q, "status"), _q1(q, "material"))}

    def toy_create(self, staff, q, b):
        from .domain import MATERIALS
        if b.get("material") not in MATERIALS:
            raise DomainError("材质不合法", "bad_request")
        drying_min = MATERIAL_DRYING_MINUTES[b["material"]]
        with self.db.connect() as conn:
            cur = conn.execute(
                "INSERT INTO toys (code, name, material, age_min, age_max, drying_minutes,"
                " parts_count) VALUES (?,?,?,?,?,?,?)",
                (b["code"], b["name"], b["material"], int(b.get("age_min", 0)),
                 int(b.get("age_max", 12)), drying_min, int(b.get("parts_count", 1))))
            conn.commit()
            return {"toy_id": cur.lastrowid, "drying_minutes": drying_min}

    def deploy(self, staff, q, b):
        with self.db.connect() as conn:
            return deployment.deploy_toy(conn, int(b["toy_id"]), int(b["zone_id"]),
                                         staff["id"])

    def recover(self, staff, q, b):
        with self.db.connect() as conn:
            return recovery.scan_recovery(conn, str(b["toy_code"]),
                                          str(b["bin_code"]), staff["id"])

    def clean(self, staff, q, b):
        with self.db.connect() as conn:
            return cleaning.clean_toy(conn, int(b["toy_id"]), str(b["method"]),
                                      staff["id"])

    def batches(self, staff, q, b):
        with self.db.connect() as conn:
            return {"batches": queries.list_batches(conn)}

    def batch_create(self, staff, q, b):
        with self.db.connect() as conn:
            return disinfection.create_batch(conn, str(b["material"]), str(b["method"]),
                                             [int(t) for t in b["toy_ids"]], staff["id"])

    def batch_submit(self, staff, q, b, batch_id):
        with self.db.connect() as conn:
            return disinfection.submit_batch(conn, int(batch_id), staff["id"], self.adapter)

    def batch_release_isolation(self, staff, q, b, batch_id):
        with self.db.connect() as conn:
            return isolation.release_batch_isolation(conn, int(batch_id), staff["id"],
                                                     b.get("note", ""))

    def batch_trace(self, staff, q, b, batch_id):
        with self.db.connect() as conn:
            return disinfection.trace_batch(conn, int(batch_id))

    def drying_list(self, staff, q, b):
        with self.db.connect() as conn:
            return {"drying": drying.list_drying(conn)}

    def drying_release(self, staff, q, b, record_id):
        with self.db.connect() as conn:
            return drying.release_drying(conn, int(record_id), staff["id"])

    def damage_list(self, staff, q, b):
        with self.db.connect() as conn:
            return {"damage": damage.list_damage(conn)}

    def damage_report(self, staff, q, b):
        photo_path = None
        if b.get("photo_data"):
            with self.db.connect() as conn:
                toy = conn.execute("SELECT code FROM toys WHERE id=?",
                                   (int(b["toy_id"]),)).fetchone()
            if not toy:
                raise DomainError("玩具不存在", "not_found")
            from .db import iso, utcnow
            photo_path = damage.save_photo(self.photos_dir, toy["code"],
                                           b.get("photo_name", "photo.bin"),
                                           b["photo_data"], iso(utcnow()))
        with self.db.connect() as conn:
            return damage.report_damage(conn, int(b["toy_id"]), str(b["damage_type"]),
                                        str(b.get("description", "")), photo_path,
                                        staff["id"])

    def repair_start(self, staff, q, b, rid):
        with self.db.connect() as conn:
            return damage.start_repair(conn, int(rid), staff["id"])

    def repair_done(self, staff, q, b, rid):
        with self.db.connect() as conn:
            return damage.complete_repair(conn, int(rid), str(b.get("note", "")),
                                          staff["id"], b.get("parts_restored"))

    def scrap(self, staff, q, b, rid):
        with self.db.connect() as conn:
            return damage.scrap(conn, int(rid), staff["id"])

    def isolations(self, staff, q, b):
        with self.db.connect() as conn:
            return {"isolations": isolation.list_isolations(
                conn, active_only=_q1(q, "active") == "1")}

    def isolation_release(self, staff, q, b, iid):
        with self.db.connect() as conn:
            return isolation.release_isolation(conn, int(iid), staff["id"],
                                               b.get("note", ""))

    def exceptions(self, staff, q, b):
        with self.db.connect() as conn:
            return {"exceptions": queries.list_exceptions(conn, _q1(q, "type"))}

    def turnover(self, staff, q, b):
        with self.db.connect() as conn:
            tid = _q1(q, "toy_id")
            return {"turnover": queries.list_turnover(conn, int(tid) if tid else None)}


def _q1(query: dict, key: str):
    v = query.get(key)
    return v[0] if v else None


# ---------------------------------------------------------------- HTTP 层
_CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript",
                  ".css": "text/css", ".svg": "image/svg+xml",
                  ".png": "image/png", ".jpg": "image/jpeg"}


def make_handler(api: Api, web_dir: str, photos_dir: str):
    import os

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # 静默访问日志
            pass

        def _send_json(self, status, obj):
            data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _send_file(self, path):
            ext = os.path.splitext(path)[1]
            try:
                with open(path, "rb") as f:
                    data = f.read()
            except OSError:
                return self._send_json(404, {"error": "文件不存在"})
            self.send_response(200)
            self.send_header("Content-Type",
                             _CONTENT_TYPES.get(ext, "application/octet-stream"))
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _handle(self, method):
            parsed = urlparse(self.path)
            path = parsed.path
            if path.startswith("/api/"):
                length = int(self.headers.get("Content-Length") or 0)
                body = {}
                if length:
                    try:
                        body = json.loads(self.rfile.read(length).decode("utf-8"))
                    except (ValueError, UnicodeDecodeError):
                        return self._send_json(400, {"error": "请求体不是合法 JSON"})
                staff_id = self.headers.get("X-Staff-Id")
                try:
                    staff_id = int(staff_id) if staff_id else None
                except ValueError:
                    staff_id = None
                status, obj = api.dispatch(method, path, parse_qs(parsed.query),
                                           body, staff_id)
                return self._send_json(status, obj)

            # 静态资源
            if path == "/":
                return self._send_file(os.path.join(web_dir, "index.html"))
            if path.startswith("/photos/"):
                name = os.path.basename(path)  # 防目录穿越
                return self._send_file(os.path.join(photos_dir, name))
            safe = os.path.normpath(path).lstrip("/")
            full = os.path.join(web_dir, safe)
            if os.path.commonpath([os.path.abspath(full),
                                   os.path.abspath(web_dir)]) != os.path.abspath(web_dir):
                return self._send_json(403, {"error": "禁止访问"})
            return self._send_file(full)

        def do_GET(self):
            self._handle("GET")

        def do_POST(self):
            self._handle("POST")

    return Handler


def serve(api: Api, web_dir: str, photos_dir: str, host: str, port: int):
    server = ThreadingHTTPServer((host, port), make_handler(api, web_dir, photos_dir))
    print(f"候诊玩具消毒轮换系统已启动: http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
