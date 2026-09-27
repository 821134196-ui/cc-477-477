"""HTTP 层测试：角色 Cookie、前台只读、工序接口返回。"""
from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from toyrotation.adapters import MockSterilizerAdapter
from toyrotation.app import create_handler
from toyrotation.db import Database
from toyrotation.service import ToyRotationService


class WebCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = Database(Path(cls.tmp.name) / "web.db")
        cls.svc = ToyRotationService(
            cls.db, adapter=MockSterilizerAdapter(),
            photo_dir=Path(cls.tmp.name) / "photos", minute_seconds=60)
        cls.svc.add_zone("Z1", "候诊A区", 3)
        cls.svc.add_toy("T1", "积木", "plastic", 1, 4)
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), create_handler(cls.svc))
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.db.close()
        cls.tmp.cleanup()

    def _req(self, method: str, path: str, body: dict | None = None,
             role: str | None = None) -> tuple[int, dict]:
        url = f"http://127.0.0.1:{self.port}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if role:
            req.add_header("Cookie", f"role={role}")
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode())

    def test_front_desk_state_is_minimal(self) -> None:
        _, res = self._req("GET", "/api/state", role="front_desk")
        data = res["data"]
        self.assertNotIn("toys", data)
        self.assertNotIn("batches", data)
        self.assertNotIn("incidents", data)
        z = data["waiting"]["zones"][0]
        self.assertNotIn("items", z, "前台不应看到在区玩具明细")
        self.assertIn("available", z)
        self.assertIn("next_rotation_at", z)

    def test_front_desk_cannot_deploy_or_release(self) -> None:
        status, res = self._req("POST", "/api/deploy",
                                {"toy_id": "T1", "zone_id": "Z1"}, "front_desk")
        self.assertEqual(status, 403)
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"]["code"], "permission_denied")

        # 先制造一件隔离玩具，再以前台尝试解除
        self.svc.report_damage("T1", "sharp_edge", "setup")
        status, _ = self._req("POST", "/api/toys/T1/release",
                              {"disposition": "store"}, "front_desk")
        self.assertEqual(status, 403)

    def test_staff_deploy_ok_and_nurse_release(self) -> None:
        self.svc.add_toy("T2", "摇铃", "plastic", 0, 2)
        status, res = self._req("POST", "/api/deploy",
                                {"toy_id": "T2", "zone_id": "Z1"}, "staff")
        self.assertEqual(status, 200)
        self.assertTrue(res["ok"])

        # 普通员工不能解除隔离
        self.svc.report_damage("T2", "contamination", "staff")
        status, res = self._req("POST", "/api/toys/T2/release",
                                {"disposition": "reprocess"}, "staff")
        self.assertEqual(status, 403)
        # 护士长可以重新清洁
        status, res = self._req("POST", "/api/toys/T2/release",
                                {"disposition": "reprocess"}, "nurse")
        self.assertEqual(status, 200)
        self.assertTrue(res["ok"])

    def test_concurrent_http_deploy_single_winner(self) -> None:
        ids = [f"T{i}" for i in range(10, 14)]
        for tid in ids:
            self.svc.add_toy(tid, "并发玩具", "plastic", 1, 3)
        results = []

        def deploy(tid: str) -> None:
            status, res = self._req("POST", "/api/deploy",
                                    {"toy_id": "T10", "zone_id": "Z1"}, "staff")
            results.append((tid, status, res["ok"], res.get("error", {}).get("code")))

        threads = [threading.Thread(target=deploy, args=(tid,)) for tid in ids]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        wins = [r for r in results if r[1] == 200 and r[2]]
        self.assertEqual(len(wins), 1, f"HTTP 并发投放应仅一人成功：{results}")

    def test_static_pages_served(self) -> None:
        for path, ctype in [("/", "text/html"), ("/app.css", "text/css"),
                            ("/app.js", "application/javascript")]:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}") as resp:
                self.assertEqual(resp.status, 200)
                self.assertIn(ctype, resp.headers["Content-Type"])


if __name__ == "__main__":
    unittest.main()
