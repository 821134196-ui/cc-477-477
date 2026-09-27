"""Web 应用：基于标准库 http.server 的 JSON API + 静态页面。

角色通过 Cookie 传递（staff / nurse / front_desk）；服务端对每个写操作
强制校验角色，前台只读。破损照片以 data URL 上传，保存到本地 photo_dir。
"""
from __future__ import annotations

import base64
import json
import re
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from . import models as M
from .service import ServiceError, ToyRotationService

STATIC_DIR = Path(__file__).parent / "static"
VALID_ROLES = {M.ROLE_STAFF, M.ROLE_NURSE, M.ROLE_FRONT}


def create_handler(svc: ToyRotationService):
    class Handler(BaseHTTPRequestHandler):
        server_version = "ToyRotation/1.0"

        def log_message(self, fmt, *args):  # 安静日志
            pass

        # --- 工具 ---
        def _cookie_role(self) -> str:
            cookie = SimpleCookie(self.headers.get("Cookie", ""))
            if "role" in cookie and cookie["role"].value in VALID_ROLES:
                return cookie["role"].value
            return M.ROLE_STAFF

        def _json_in(self) -> dict:
            length = int(self.headers.get("Content-Length", 0))
            if not length:
                return {}
            raw = self.rfile.read(length)
            try:
                data = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                raise ServiceError("bad_json", "请求不是合法 JSON")
            return data if isinstance(data, dict) else {}

        def _send_json(self, obj: dict, status: int = 200,
                       extra_headers: list[tuple[str, str]] | None = None) -> None:
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            for k, v in (extra_headers or []):
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _send_file(self, path: Path, ctype: str) -> None:
            if not path.is_file():
                self.send_error(404)
                return
            body = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _guard_role(self, role: str, allowed: set[str]) -> None:
            if role not in allowed:
                from .service import PermissionDenied
                raise PermissionDenied()

        def _call(self, fn, *args, **kwargs):
            try:
                result = fn(*args, **kwargs)
                self._send_json({"ok": True, "data": result if result is not None else {}})
            except ServiceError as e:
                self._send_json({"ok": False, "error": {"code": e.code, "message": e.message}},
                                status=400 if e.code != "permission_denied" else 403)

        # --- GET ---
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            try:
                if path == "/api/state":
                    self._send_json({"ok": True, "data": self._state()})
                elif path.startswith("/api/trace"):
                    self._guard_role(self._cookie_role(),
                                     {M.ROLE_STAFF, M.ROLE_NURSE})
                    qs = dict(p.split("=", 1) for p in parsed.query.split("&") if "=" in p)
                    self._send_json({"ok": True, "data": {
                        "events": svc.events(
                            toy_id=qs.get("toy_id"), batch_id=qs.get("batch_id"), limit=300)
                    }})
                elif path.startswith("/photos/"):
                    name = Path(path[len("/photos/"):]).name
                    self._send_file(svc.photo_dir / name, "image/png")
                elif path == "/" or path == "/index.html":
                    self._send_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
                elif path == "/app.css":
                    self._send_file(STATIC_DIR / "app.css", "text/css; charset=utf-8")
                elif path == "/app.js":
                    self._send_file(STATIC_DIR / "app.js", "application/javascript; charset=utf-8")
                else:
                    self.send_error(404)
            except Exception as e:  # 读取异常兜底
                self._send_json({"ok": False, "error": {"code": "server", "message": str(e)}}, 500)

        def _state(self) -> dict:
            role = self._cookie_role()
            base = {
                "role": role,
                "now": svc.now().strftime("%Y-%m-%d %H:%M:%S"),
                "constants": {
                    "materials": M.MATERIAL_LABELS,
                    "clean_methods": M.CLEAN_LABELS,
                    "areas": M.AREA_LABELS,
                    "incidents": M.INCIDENT_LABELS,
                    "events": M.EVENT_LABELS,
                    "reasons": M.QUARANTINE_REASONS,
                    "roles": M.ROLE_LABELS,
                    "dry_minutes": M.DRY_MINUTES,
                    "rotation_minutes": M.ROTATION_MINUTES,
                },
                "waiting": svc.waiting_overview(),
            }
            if role == M.ROLE_FRONT:
                # 前台只看候诊区可用数量与轮换时间，不暴露玩具/批次/隔离明细
                waiting = svc.waiting_overview()
                for z in waiting["zones"]:
                    z.pop("items", None)
                base["waiting"] = waiting
                return base
            base.update({
                "zones": svc.zones(),
                "toys": svc.toys(),
                "batches": svc.batches(),
                "incidents": svc.incidents(),
                "damage": svc.damage_reports(),
            })
            return base

        # --- POST ---
        def do_POST(self) -> None:
            path = urlparse(self.path).path
            role = self._cookie_role()
            try:
                data = self._json_in()
                if path == "/api/role":
                    new_role = data.get("role", "")
                    if new_role not in VALID_ROLES:
                        raise ServiceError("bad_role", "角色无效")
                    self._send_json({"ok": True, "data": {"role": new_role}},
                                    status=200,
                                    extra_headers=[("Set-Cookie",
                                                    f"role={new_role}; Path=/; SameSite=Lax")])
                    return
                if path == "/api/deploy":
                    self._call(svc.deploy, data["toy_id"], data["zone_id"],
                               "web-user", role)
                elif path == "/api/recycle":
                    self._call(svc.scan_recycle, data["toy_id"], "web-user", role,
                               data.get("found_zone_id") or None)
                elif path == "/api/missed-scan":
                    self._guard_role(role, {M.ROLE_STAFF, M.ROLE_NURSE})
                    self._call(svc.report_missed_scan, data["toy_id"], "web-user",
                               data.get("note", ""))
                elif path == "/api/batches":
                    self._guard_role(role, {M.ROLE_STAFF, M.ROLE_NURSE})
                    self._call(svc.create_batch, data["material"], "web-user",
                               data.get("toy_ids") or None)
                elif re.fullmatch(r"/api/batches/[^/]+/add", path):
                    self._call(svc.add_to_batch, path.split("/")[3],
                               data["toy_id"], "web-user", role,
                               data.get("method") or None)
                elif re.fullmatch(r"/api/batches/[^/]+/disinfect", path):
                    self._call(svc.run_disinfection, path.split("/")[3],
                               "web-user", role)
                elif re.fullmatch(r"/api/batches/[^/]+/dry-start", path):
                    self._call(svc.start_drying, path.split("/")[3], "web-user", role)
                elif re.fullmatch(r"/api/batches/[^/]+/dry-complete", path):
                    self._call(svc.complete_drying, path.split("/")[3], "web-user",
                               bool(data.get("force")))
                elif re.fullmatch(r"/api/batches/[^/]+/quarantine", path):
                    self._guard_role(role, {M.ROLE_STAFF, M.ROLE_NURSE})
                    self._call(svc.quarantine_batch, path.split("/")[3],
                               "web-user", role,
                               data.get("reason", M.Q_BATCH_RISK),
                               data.get("note", ""))
                elif path == "/api/damage":
                    self._guard_role(role, {M.ROLE_STAFF, M.ROLE_NURSE})
                    photo_path = None
                    if data.get("photo_data_url"):
                        photo_path = self._save_photo(data["toy_id"],
                                                      data["photo_data_url"])
                    self._call(svc.report_damage, data["toy_id"], data["kind"],
                               "web-user", role, data.get("note", ""), photo_path)
                elif re.fullmatch(r"/api/toys/[^/]+/repair-start", path):
                    self._call(svc.send_to_repair, path.split("/")[3],
                               "web-user", role)
                elif re.fullmatch(r"/api/toys/[^/]+/repair-complete", path):
                    self._call(svc.complete_repair, path.split("/")[3],
                               "web-user", role,
                               int(data.get("parts_restored", 0)),
                               data.get("note", ""))
                elif re.fullmatch(r"/api/toys/[^/]+/release", path):
                    self._call(svc.release_quarantine, path.split("/")[3],
                               "web-user", role,
                               data.get("disposition", "store"),
                               data.get("note", ""))
                else:
                    self.send_error(404)
            except ServiceError as e:
                self._send_json({"ok": False, "error": {"code": e.code, "message": e.message}},
                                status=400 if e.code != "permission_denied" else 403)
            except KeyError as e:
                self._send_json({"ok": False, "error": {"code": "missing_field",
                                                        "message": f"缺少字段：{e}"}}, 400)

        def _save_photo(self, toy_id: str, data_url: str) -> str:
            m = re.match(r"data:image/(\w+);base64,(.*)", data_url, re.S)
            ext = "png"
            payload = data_url
            if m:
                ext = m.group(1) if m.group(1) in ("png", "jpg", "jpeg", "webp") else "png"
                payload = m.group(2)
            n = len(list(svc.photo_dir.glob(f"{toy_id}-*.{ext}"))) + 1
            safe_id = re.sub(r"[^A-Za-z0-9_-]", "", toy_id) or "toy"
            path = svc.photo_dir / f"{safe_id}-upload-{n}.{ext}"
            path.write_bytes(base64.b64decode(payload))
            return str(path)

    return Handler


def serve(svc: ToyRotationService, host: str = "127.0.0.1", port: int = 8000) -> None:
    httpd = ThreadingHTTPServer((host, port), create_handler(svc))
    print(f"玩具消毒轮换系统已启动：http://{host}:{port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n正在关闭…")
    finally:
        httpd.server_close()
