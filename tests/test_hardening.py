"""Regression tests for the production-hardening fixes.

Each test here locks in a bug found by probing the live deployment, so it can
never come back.
"""
import os
import tempfile
import unittest

import helpers  # noqa: F401
from fakes import make_a, make_b, make_c

from starlette.testclient import TestClient

from sawii_persona.api.app import Runtime, create_app
from sawii_persona.config import load_settings
from sawii_persona.mind import Mind


class HardeningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = load_settings(data_dir=self.tmp.name, db_path=os.path.join(self.tmp.name, "h.db"),
                                      admin_token="admin-secret", background_enabled=False,
                                      telegram_secret="tg", email_inbound_secret="em")
        mind = Mind(self.settings, llms={"a": make_a(), "b": make_b(), "c": make_c(), "embed": None,
                                         "consultants": {}})
        self.rt = Runtime(self.settings, mind=mind)
        self.client = TestClient(create_app(self.settings, self.rt)).__enter__()
        r = self.client.post("/admin/personas", headers={"Authorization": "Bearer admin-secret"},
                             json={"name": "Koko", "preset": "warm-friend", "user_name": "Anas"})
        self.key = r.json()["key"]
        self.h = {"Authorization": f"Bearer {self.key}"}

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.rt.mind.store.close()
        self.tmp.cleanup()

    def test_messages_non_numeric_wait_is_400_not_500(self):
        # Was a 500 (uncaught float()); must be a clean 400/200, never a crash.
        r = self.client.get("/v1/messages?wait=abc", headers=self.h)
        self.assertLess(r.status_code, 500)
        self.assertEqual(r.status_code, 200)

    def test_health_subsystems_never_used_is_idle_not_dead(self):
        # A fresh boot must not cry wolf: untouched subsystems are 'idle'.
        body = self.client.get("/health/subsystems").json()
        self.assertTrue(body["summary"]["ok"])
        self.assertEqual(body["summary"]["dead"], [])
        self.assertIn("idle", body["summary"])
        self.assertTrue(body["summary"]["idle"])
        for sub in body["subsystems"].values():
            if sub.get("status") == "idle":
                self.assertFalse(sub["alive"])

    def test_telegram_webhook_malformed_body_is_400(self):
        r = self.client.post("/hooks/telegram", headers={"x-telegram-bot-api-secret-token": "tg"},
                             content=b"{not json", )
        self.assertEqual(r.status_code, 400)

    def test_email_webhook_malformed_body_is_400(self):
        r = self.client.post("/hooks/email?secret=em", content=b"{not json")
        self.assertEqual(r.status_code, 400)

    def test_health_never_raises_even_without_deep_lane(self):
        r = self.client.get("/health")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["ok"])


    def test_cors_allows_the_native_shells(self):
        # The desktop / Android / iOS shells load bundled assets from
        # capacitor://localhost or tauri://localhost and call this server
        # cross-origin. Without CORS headers those builds cannot talk to the
        # backend at all - so this locks the middleware in.
        r = self.client.get("/health", headers={"Origin": "capacitor://localhost"})
        self.assertEqual(r.headers.get("access-control-allow-origin"), "*")

        pre = self.client.options("/v1/persona", headers={
            "Origin": "tauri://localhost",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization,content-type",
        })
        self.assertLess(pre.status_code, 400)
        self.assertEqual(pre.headers.get("access-control-allow-origin"), "*")


if __name__ == "__main__":
    unittest.main()
