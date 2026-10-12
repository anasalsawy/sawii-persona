"""Tests for the native tools: reach_out (channels) and place_call (two-lobe calls)."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sawii_persona.config import load_settings  # noqa: E402
from sawii_persona.identity import create_persona, get_persona  # noqa: E402
from sawii_persona.mind import Mind  # noqa: E402
from sawii_persona.voice.calls import Caller, _twiml  # noqa: E402
from tests.fakes import make_a, make_b, make_c  # noqa: E402


class FakeChannels:
    def __init__(self):
        self.sent = []
        self.calls = []

    async def deliver(self, persona, channel, text, *, subject="", to=None):
        self.sent.append({"channel": channel, "text": text, "to": to})

    async def _twilio(self, resource, form):
        self.calls.append({"resource": resource, "form": form})


def _mind(tmp, **kw):
    s = load_settings(data_dir=tmp, db_path=os.path.join(tmp, "t.db"), **kw)
    return s, Mind(s, llms={"a": make_a(), "b": make_b(), "c": make_c(), "embed": None, "consultants": {}})


class ReachOutTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s, self.mind = _mind(self.tmp.name)
        self.pid, _ = await create_persona(self.mind.store, name="Koko", definition="You are Koko.")
        self.ch = FakeChannels()
        self.mind.channels = self.ch
        self.persona = await get_persona(self.mind.store, self.pid)

    async def asyncTearDown(self):
        self.mind.store.close()
        self.tmp.cleanup()

    async def test_reach_out_sends_on_a_channel(self):
        out = await self.mind.reach_out(self.persona, "telegram", "hey, checking in")
        self.assertIn('"ok": true', out)
        self.assertEqual(self.ch.sent[0]["channel"], "telegram")
        self.assertEqual(self.ch.sent[0]["text"], "hey, checking in")

    async def test_reach_out_rejects_unknown_channel(self):
        out = await self.mind.reach_out(self.persona, "carrier-pigeon", "hi")
        self.assertIn('"ok": false', out)
        self.assertEqual(self.ch.sent, [])

    async def test_reach_out_rejects_empty_text(self):
        out = await self.mind.reach_out(self.persona, "sms", "   ")
        self.assertIn('"ok": false', out)


class CallerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s, self.mind = _mind(self.tmp.name)
        self.pid, _ = await create_persona(self.mind.store, name="Koko", definition="You are Koko.",
                                           contact={"sms": "+15551234567"})
        self.persona = await get_persona(self.mind.store, self.pid)
        self.ch = FakeChannels()
        self.mind.channels = self.ch

    async def asyncTearDown(self):
        self.mind.store.close()
        self.tmp.cleanup()

    async def test_call_unavailable_without_twilio(self):
        c = Caller(self.mind, self.ch, self.s)
        self.assertFalse(c.available())
        res = await c.place(self.persona, "me", "say hello")
        self.assertFalse(res["ok"])
        self.assertEqual(self.ch.calls, [])

    async def test_two_lobe_call_dials_with_director_opening(self):
        self.s.twilio_sid, self.s.twilio_token = "AC1", "tok"
        self.s.twilio_from = "+15550000000"
        self.s.public_base_url = "https://example.test"
        c = Caller(self.mind, self.ch, self.s)
        self.assertTrue(c.available())
        res = await c.place(self.persona, "me", "check on Maya's exam")
        self.assertTrue(res["ok"])
        self.assertEqual(res["to"], "+15551234567")
        self.assertTrue(res["opening"])                      # B directed an opening
        self.assertEqual(self.ch.calls[0]["resource"], "Calls.json")
        self.assertIn("Gather", self.ch.calls[0]["form"]["Twiml"])  # A speaks, then listens

    async def test_place_call_tool_reports_when_calling_unavailable(self):
        self.mind.caller = Caller(self.mind, self.ch, self.s)   # no twilio configured
        out = await self.mind.place_call(self.persona, "me", "say hi")
        self.assertIn('"ok": false', out)


class TwimlTests(unittest.TestCase):
    def test_twiml_gathers_speech(self):
        x = _twiml("hello there", action="https://x/step")
        self.assertIn("<Say>hello there</Say>", x)
        self.assertIn('input="speech"', x)

    def test_twiml_escapes(self):
        x = _twiml("a & b <c>", action="https://x/step", gather=False)
        self.assertIn("a &amp; b &lt;c&gt;", x)


if __name__ == "__main__":
    unittest.main()
