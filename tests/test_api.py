import json
import os
import tempfile
import time
import unittest

import helpers  # noqa: F401
from fakes import make_a, make_b, make_c

from starlette.testclient import TestClient

from sawii_persona.api.app import Runtime, create_app
from sawii_persona.config import load_settings
from sawii_persona.mind import Mind

MSG = "my sister Maya has her exam on friday, I'm nervous. remind me to ask her about it"


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = load_settings(data_dir=self.tmp.name, db_path=os.path.join(self.tmp.name, "a.db"),
                                      admin_token="admin-secret", background_enabled=False,
                                      intrusion_probability=0.0, telegram_secret="tg", email_inbound_secret="em")
        mind = Mind(self.settings, llms={"a": make_a(), "b": make_b(), "c": make_c(), "embed": None,
                                         "consultants": {}})
        self.rt = Runtime(self.settings, mind=mind)
        self.client = TestClient(create_app(self.settings, self.rt)).__enter__()
        r = self.client.post("/admin/personas", headers={"Authorization": "Bearer admin-secret"},
                             json={"name": "Koko", "preset": "warm-friend", "user_name": "Anas",
                                   "timezone": "Africa/Cairo", "contact": {"telegram": "777", "email": "a@b.c"}})
        self.assertEqual(r.status_code, 201)
        self.key = r.json()["key"]
        self.h = {"Authorization": f"Bearer {self.key}"}

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.rt.mind.store.close()
        self.tmp.cleanup()

    def test_auth(self):
        self.assertEqual(self.client.get("/v1/persona").status_code, 401)
        self.assertEqual(self.client.post("/admin/personas", json={}).status_code, 401)
        self.assertEqual(self.client.get("/v1/persona", headers=self.h).json()["name"], "Koko")

    def test_chat_nonstream_and_stream(self):
        r = self.client.post("/v1/chat/completions", headers=self.h,
                             json={"model": "x", "messages": [{"role": "user", "content": MSG}]})
        self.assertEqual(r.status_code, 200)
        text = r.json()["choices"][0]["message"]["content"]
        self.assertIn("Maya", text)
        self.assertNotIn("feel:", text)
        with self.client.stream("POST", "/v1/chat/completions", headers=self.h,
                                json={"stream": True, "messages": [{"role": "user", "content": "hi again"}]}) as s:
            body = "".join(s.iter_text())
        self.assertIn("data: [DONE]", body)
        chunks = [json.loads(l[5:]) for l in body.splitlines() if l.startswith("data: {")]
        streamed = "".join(c["choices"][0]["delta"].get("content", "") for c in chunks)
        self.assertIn("Good luck", streamed)

    def test_persona_clone_and_presets(self):
        self.assertTrue(self.client.get("/v1/persona/presets").json()["presets"])
        r = self.client.put("/v1/persona", headers=self.h, json={
            "name": "Grandpa", "clone": {"name": "Grandpa Sami", "relationship": "grandfather",
                                         "sample_messages": "ya habibi, eat first\ncome sit with me"}})
        d = r.json()["definition"]
        self.assertIn("Grandpa Sami", d)
        self.assertIn("ya habibi, eat first", d)

    def test_memory_audit_wipe_emotion_clock_loops(self):
        self.client.post("/v1/chat/completions", headers=self.h, json={"messages": [{"role": "user", "content": MSG}]})
        time.sleep(0.5)
        mem = self.client.get("/v1/persona/memory", headers=self.h).json()
        self.assertTrue(mem["layers"])
        lid = mem["layers"][0]["id"]
        self.assertTrue(self.client.delete(f"/v1/persona/memory/{lid}", headers=self.h).json()["wiped"])
        emo = self.client.get("/v1/persona/emotion", headers=self.h).json()
        self.assertTrue(emo["persona"]["color"].startswith("#"))
        clk = self.client.get("/v1/persona/clock", headers=self.h).json()
        self.assertIsNotNone(clk["since_your_last_reply_seconds"])
        self.assertEqual(len(self.client.get("/v1/persona/loops", headers=self.h).json()["open"]), 1)

    def test_voice_websocket(self):
        with self.client.websocket_connect("/v1/voice") as ws:
            ws.send_text(json.dumps({"type": "hello", "key": self.key}))
            ready = ws.receive_json()
            self.assertEqual(ready["type"], "ready")
            self.assertEqual(ready["tts"], "client")
            ws.send_text(json.dumps({"type": "text", "text": MSG}))
            kinds = []
            while True:
                m = ws.receive_json()
                kinds.append(m["type"])
                if m["type"] == "done":
                    break
            self.assertIn("say", kinds)
            self.assertIn("delta", kinds)

    def test_voice_rejects_bad_key(self):
        with self.client.websocket_connect("/v1/voice?key=nope") as ws:
            self.assertEqual(ws.receive_json()["type"], "error")

    def test_outbox_long_poll(self):
        import asyncio
        rt = self.rt

        async def push():
            p = await rt.mind.store.one("SELECT * FROM personas")
            await rt.initiative.send(p, "thinking of you", reason="test")
        self.client.portal.call(push)
        msgs = self.client.get("/v1/messages?wait=1", headers=self.h).json()["messages"]
        self.assertEqual(msgs[0]["text"], "thinking of you")
        self.assertEqual(self.client.get("/v1/messages", headers=self.h).json()["messages"], [])

    def test_telegram_and_email_hooks(self):
        self.assertEqual(self.client.post("/hooks/telegram", json={}).status_code, 403)
        r = self.client.post("/hooks/telegram", headers={"X-Telegram-Bot-Api-Secret-Token": "tg"},
                             json={"message": {"chat": {"id": 777}, "text": "hello from telegram"}})
        self.assertEqual(r.status_code, 200)
        r = self.client.post("/hooks/email?secret=em", json={"from": "Anas <a@b.c>", "text": "hello by mail"})
        self.assertEqual(r.status_code, 200)
        time.sleep(0.6)
        turns = self.client.portal.call(self.rt.mind.store.all, "SELECT channel, user_text FROM turns")
        chans = {t["channel"] for t in turns}
        self.assertTrue({"telegram", "email"} <= chans, chans)

    def test_twilio_requires_signature(self):
        r = self.client.post("/hooks/twilio", data={"From": "+1", "Body": "x"})
        self.assertEqual(r.status_code, 403)

    def test_deep_job(self):
        r = self.client.post("/v1/deep", headers=self.h, json={"task": "research phones", "mode": "auto"})
        self.assertEqual(r.status_code, 202)
        job = r.json()["job"]
        for _ in range(30):
            row = self.client.get(f"/v1/deep/{job}", headers=self.h).json()
            if row["status"] in ("done", "failed"):
                break
            time.sleep(0.1)
        self.assertEqual(row["status"], "done")

    def test_health_and_app(self):
        self.assertTrue(self.client.get("/health").json()["ok"])
        self.assertIn("Sawii Persona", self.client.get("/app").text)
