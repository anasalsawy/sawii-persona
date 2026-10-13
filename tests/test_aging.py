"""Aging: C's verdict turning into a weight update on the live turn."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import helpers  # noqa: F401  (puts src/ on sys.path)
from fakes import make_c
from helpers import StoreCase
from test_turn import MSG, MindCase

from sawii_persona.config import Settings
from sawii_persona.life.update import REWARD, Updater, reward_for
from sawii_persona.subsystems import Registry


class _Handler(BaseHTTPRequestHandler):
    routes: dict = {}

    def log_message(self, *a):  # quiet
        pass

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(n) or b"{}")
        type(self).routes[self.path].setdefault("hits", []).append(payload)
        code = type(self).routes[self.path].get("code", 200)
        if code >= 400:
            self._json(code, {"detail": "trainer exploded"})
        else:
            self._json(200, {"status": "applied", "steps": 1, "loss": 0.42})


def _start(routes):
    _Handler.routes = routes
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/v1/update"


class AgingTests(StoreCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.registry = Registry()

    def persona_with(self, **kw):
        p = dict(self.persona)
        p.update(kw)
        return p

    def rows(self):
        return self.store._fetchall("SELECT * FROM updates ORDER BY id")

    # ---------------- reward mapping ----------------

    def test_reward_map(self):
        self.assertEqual(reward_for("GREEN"), 1.0)
        self.assertEqual(reward_for("RED"), -1.0)
        self.assertLess(reward_for("YELLOW"), 0)
        self.assertIsNone(reward_for("???"))
        self.assertIsNone(reward_for(None))
        self.assertEqual(set(REWARD), {"GREEN", "YELLOW", "RED"})

    # ---------------- honest no-op ----------------

    async def test_frozen_when_no_endpoint(self):
        u = Updater(Settings(update_url=""), self.store, health=self.registry)
        out = await u.update(self.persona_with(), turn_id=1, level="RED")
        self.assertEqual(out["status"], "frozen")
        # nothing was learned, so nothing may claim it was
        self.assertEqual(self.rows(), [])

    # ---------------- anchor gate ----------------

    async def test_refused_when_drifting(self):
        u = Updater(Settings(update_url="http://127.0.0.1:1/v1/update", juice_floor=0.7),
                    self.store, health=self.registry)
        out = await u.update(self.persona_with(anchor_score=0.4, anchor_at=1.0),
                             turn_id=1, level="RED")
        self.assertEqual(out["status"], "refused")
        self.assertIn("0.4", out["reason"])
        self.assertEqual(self.rows(), [])

    async def test_never_anchored_is_not_drift(self):
        """anchor_score defaults to 0 with anchor_at == 0, i.e. C has never graded
        a rehearsal. That is 'unknown', not 'drifted' - a brand new mind must
        still be allowed to age."""
        u = Updater(Settings(update_url="http://127.0.0.1:1/v1/update", juice_floor=0.7),
                    self.store, health=self.registry)
        p = self.persona_with(anchor_score=0.0, anchor_at=0.0)
        self.assertEqual(p["anchor_score"], 0.0)
        out = await u.update(p, turn_id=1, level="RED")
        self.assertNotEqual(out["status"], "refused")

    async def test_floor_zero_disables_the_gate(self):
        u = Updater(Settings(update_url="http://127.0.0.1:1/v1/update", juice_floor=0.0),
                    self.store, health=self.registry)
        out = await u.update(self.persona_with(anchor_score=0.01, anchor_at=1.0),
                             turn_id=1, level="RED")
        self.assertNotEqual(out["status"], "refused")

    # ---------------- real steps ----------------

    async def test_applied_and_logged(self):
        srv, url = _start({"/v1/update": {}})
        try:
            u = Updater(Settings(update_url=url), self.store, health=self.registry)
            out = await u.update(
                self.persona_with(), turn_id=7, level="YELLOW",
                prompt="did you actually check?", response="No. I guessed and said it as fact.")
            self.assertEqual(out["status"], "applied")
            self.assertEqual(out["reward"], -0.4)
            self.assertEqual(out["detail"]["loss"], 0.42)

            row = self.rows()[0]
            self.assertEqual(row["turn_id"], 7)
            self.assertEqual(row["level"], "YELLOW")
            self.assertAlmostEqual(row["reward"], -0.4)
            self.assertEqual(row["status"], "applied")

            hit = _Handler.routes["/v1/update"]["hits"][0]
            self.assertEqual(hit["turn_id"], 7)
            self.assertAlmostEqual(hit["reward"], -0.4)
            self.assertEqual(hit["persona_id"], self.pid)
            self.assertIn("guessed", hit["response"])
            self.assertEqual(self.registry.get("aging").success_count, 1)
        finally:
            srv.shutdown()

    async def test_failed_step_is_recorded_not_swallowed(self):
        srv, url = _start({"/v1/update": {"code": 500}})
        try:
            u = Updater(Settings(update_url=url), self.store, health=self.registry)
            out = await u.update(self.persona_with(), turn_id=8, level="RED")
            self.assertEqual(out["status"], "failed")
            row = self.rows()[0]
            self.assertEqual(row["status"], "failed")
            self.assertIn("500", row["detail"])
            snap = self.registry.get("aging").snapshot()
            self.assertEqual(snap["error_count"], 1)
            self.assertEqual(snap["success_count"], 0)
            self.assertIn("500", snap["last_error_text"])
        finally:
            srv.shutdown()

    async def test_unreachable_endpoint_does_not_raise(self):
        u = Updater(Settings(update_url="http://127.0.0.1:9/v1/update", update_timeout=1.0),
                    self.store, health=self.registry)
        out = await u.update(self.persona_with(), turn_id=9, level="GREEN")
        self.assertEqual(out["status"], "failed")

    async def test_prompt_is_capped(self):
        srv, url = _start({"/v1/update": {}})
        try:
            u = Updater(Settings(update_url=url, update_max_chars=50), self.store)
            await u.update(self.persona_with(), turn_id=1, level="GREEN",
                           prompt="x" * 500, response="y" * 500)
            hit = _Handler.routes["/v1/update"]["hits"][0]
            self.assertLessEqual(len(hit["prompt"]), 50)
            self.assertLessEqual(len(hit["response"]), 50)
        finally:
            srv.shutdown()

    async def test_unknown_level_teaches_nothing(self):
        """verifier.verify() returns unrated() on its own failures; unrated must
        not push the weights anywhere."""
        srv, url = _start({"/v1/update": {}})
        try:
            u = Updater(Settings(update_url=url), self.store)
            out = await u.update(self.persona_with(), turn_id=2, level="UNRATED")
            self.assertEqual(out["status"], "skipped")
            self.assertEqual(_Handler.routes["/v1/update"].get("hits", []), [])
            self.assertEqual(self.rows(), [])
        finally:
            srv.shutdown()


class _RecordingUpdater:
    """Stands in for the Updater so the test asserts on the wiring, not the HTTP."""

    def __init__(self):
        self.calls = []

    async def update(self, persona, *, turn_id, level, prompt="", response=""):
        self.calls.append({"persona_id": persona["id"], "turn_id": turn_id, "level": level,
                           "prompt": prompt, "response": response,
                           "reward": reward_for(level)})
        return {"status": "applied", "level": level, "reward": reward_for(level)}


class AgingInTurnTests(MindCase):
    """The flag feature in miniature: C grades the turn, and that grade leaves
    the record and enters the weights before the mind moves on."""

    async def test_verdict_drives_an_update(self):
        self.mind.updater = _RecordingUpdater()
        [ev async for ev in self.mind.respond(self.pid, MSG)]
        await self.mind.drain()

        self.assertEqual(len(self.mind.updater.calls), 1, self.mind.updater.calls)
        call = self.mind.updater.calls[0]
        self.assertEqual(call["persona_id"], self.pid)
        self.assertEqual(call["prompt"], MSG)
        self.assertIn(call["level"], {"GREEN", "YELLOW", "RED"})
        self.assertTrue(call["response"].strip())
        # the level taught must be the level C actually recorded
        row = await self.mind.store.one(
            "SELECT level FROM verdicts ORDER BY id DESC LIMIT 1")
        self.assertEqual(row["level"], call["level"])

    async def test_a_red_verdict_pushes_the_other_way(self):
        self.mind.updater = _RecordingUpdater()
        self.mind.c = make_c(level="RED")
        [ev async for ev in self.mind.respond(self.pid, MSG)]
        await self.mind.drain()
        call = self.mind.updater.calls[0]
        self.assertEqual(call["level"], "RED")
        self.assertLess(call["reward"], 0)

    async def test_aging_never_breaks_a_turn(self):
        class _Boom:
            async def update(self, *a, **kw):
                raise RuntimeError("trainer on fire")

        self.mind.updater = _Boom()
        events = [ev async for ev in self.mind.respond(self.pid, MSG)]
        await self.mind.drain()
        self.assertEqual(events[-1]["type"], "done")
        self.assertTrue("".join(e.get("text", "") for e in events if e["type"] == "delta").strip())

    async def test_scratch_carries_the_last_update(self):
        self.mind.updater = _RecordingUpdater()
        [ev async for ev in self.mind.respond(self.pid, MSG)]
        await self.mind.drain()
        self.assertEqual(self.mind.scratch_for(self.pid).aging["status"], "applied")


if __name__ == "__main__":
    import unittest
    unittest.main()