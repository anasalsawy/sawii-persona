import asyncio
import json
import os
import random
import sys
import time

import helpers  # noqa: F401
from fakes import make_a, make_b, make_c
from test_turn import MSG, MindCase

from sawii_persona.channels.adapters import Channels, twilio_signature_ok
from sawii_persona.identity import create_persona, get_persona
from sawii_persona.lanes.deep import DeepLane
from sawii_persona.life.finetune import FineTuner, compress_text
from sawii_persona.life.initiative import Initiative
from sawii_persona.life.sleep import Sleep
from sawii_persona.life.subconscious import style_markers
from sawii_persona.voice.realtime import SentenceChunker, SpeechIO, VoiceSession
from sawii_persona.config import ModelEndpoint


class FakeChannels(Channels):
    def __init__(self, settings):
        super().__init__(settings)

    def available(self):
        return ["app", "sms"]

    async def deliver(self, persona, channel, text, *, subject=""):
        if channel == "app":
            return
        self.sent.append({"channel": channel, "text": text})


class SubconsciousTests(MindCase):
    async def test_style_markers(self):
        m = style_markers("W-well I DON'T know... it's just... haha whatever")
        for k in ("hesitant", "halting", "shouting", "guarded", "laughing"):
            self.assertIn(k, m)
        self.assertEqual(style_markers("see you tomorrow then"), [])

    async def test_juice_kept_raw_and_bowl_never_evicts_raw(self):
        self.mind.subconscious.floor = 0.0  # keep everything for this test
        self.mind.subconscious.buffer_rows = 3
        p = await get_persona(self.mind.store, self.pid)
        for i in range(5):
            await self.mind.subconscious.ingest(p, None, f"D-dude I CAN'T... {i}", "ok", chord={"fear": .8}, salience=3)
        rows = await self.mind.store.all("SELECT tier, raw_transcript FROM juice")
        self.assertEqual(len(rows), 5)  # raw juice is never evicted for space
        self.assertTrue(all(r["raw_transcript"].startswith("D-dude") for r in rows))


class SleepTests(MindCase):
    async def test_sleep_cycle_end_to_end_with_trainer_command(self):
        for _ in range(2):
            await self.mind.chat(self.pid, MSG)
        await self.mind.drain()
        self.settings.finetune_min_examples = 2
        self.settings.finetune_provider = "command"
        self.settings.finetune_command = f"{sys.executable} -c \"import sys; open(sys.argv[1]).read()\""
        self.mind.subconscious.floor = 0.0
        p = await get_persona(self.mind.store, self.pid)
        for i in range(3):
            await self.mind.subconscious.ingest(p, None, f"I'm SO scared... {i}!!", f"I'm here {i}", chord={"fear": .9},
                                                salience=3)
        ft = FineTuner(self.settings, self.mind.store, self.mind.subconscious, self.mind.a)
        sleep = Sleep(self.mind, ft, rng=random.Random(2))
        report = await sleep.run(await get_persona(self.mind.store, self.pid))
        self.assertEqual(report["narrative"], "rewritten")
        self.assertEqual(report["psych_profile"], "rewritten")
        self.assertEqual(report["subconscious"]["status"], "baked")
        p = await get_persona(self.mind.store, self.pid)
        self.assertIn("Maya", p["narrative"])
        self.assertIn("worry", p["psych_profile"])
        tiers = {r["tier"] for r in await self.mind.store.all("SELECT tier FROM juice")}
        self.assertEqual(tiers, {"compressed"})  # baked then squeezed
        ds = report["subconscious"]["dataset"]
        with open(ds, encoding="utf-8") as fh:
            lines = [json.loads(x) for x in fh]
        self.assertTrue(any(l["messages"][1]["content"].startswith("I'm SO scared") for l in lines))  # verbatim
        archive = os.path.join(os.path.dirname(ds), "archive.jsonl")
        with open(archive, encoding="utf-8") as fh:
            self.assertIn("ndsmc-raw/2", fh.read())
        # next turn: narrative and psych profile are in A's context
        await self.mind.chat(self.pid, "morning!")
        system = [c for c in self.mind.a.calls if not c["json_mode"]][-1]["messages"][0]["content"]
        self.assertIn("written by you at your last sleep", system)
        self.assertIn("observer's (C) read", system)

    async def test_failed_training_keeps_juice_raw(self):
        self.settings.finetune_min_examples = 1
        self.settings.finetune_provider = "command"
        self.settings.finetune_command = f"{sys.executable} -c \"import sys; sys.exit(3)\""
        self.mind.subconscious.floor = 0.0
        p = await get_persona(self.mind.store, self.pid)
        await self.mind.subconscious.ingest(p, None, "HELP me... please", "here", chord={"fear": .9}, salience=3)
        ft = FineTuner(self.settings, self.mind.store, self.mind.subconscious, self.mind.a)
        rep = await ft.consolidate(await get_persona(self.mind.store, self.pid))
        self.assertEqual(rep["status"], "train_failed")
        rows = await self.mind.store.all("SELECT tier, raw_transcript FROM juice")
        self.assertEqual(rows[0]["tier"], "exported")
        self.assertEqual(rows[0]["raw_transcript"], "HELP me... please")

    async def test_compress_is_lossy_but_local(self):
        out = compress_text("ok. I CAN'T stop thinking about it... she actually LEFT!! anyway.", ["shouting"])
        self.assertIn("CAN'T", out)
        self.assertIn("[shouting]", out)


class InitiativeTests(MindCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.settings.quiet_hours = ""
        self.channels = FakeChannels(self.settings)
        self.init = Initiative(self.mind, self.channels, rng=random.Random(3))

    async def test_reminder_fires_exactly_once(self):
        await self.mind.chat(self.pid, MSG)
        await self.mind.drain()
        acts = await self.init.tick(time.time() + 5)
        self.assertIn("reminder", [a["action"] for a in acts])
        self.assertEqual(len(self.channels.sent), 0)  # app channel: outbox + bus only
        out = await self.mind.store.all("SELECT * FROM outbox")
        self.assertEqual(len(out), 1)
        self.assertIn("Maya", out[0]["text"])
        acts2 = await self.init.tick(time.time() + 10)
        self.assertNotIn("reminder", [a["action"] for a in acts2])
        # the outreach is part of the one stream
        tail = await self.mind.memory.tail(self.pid, 5)
        self.assertEqual(tail[-1]["kind"], "outreach")

    async def test_afterthought_voiced_and_inhibitor(self):
        await self.mind.store.update("personas", self.pid, last_user_at=time.time() - 3600)
        await self.mind.store.insert("thoughts", persona_id=self.pid, text="Maya might like flashcards",
                                     source="afterthought", score=0.75, created_at=time.time())
        acts = await self.init.tick()
        self.assertIn("outreach", [a["action"] for a in acts])
        # daily cap reached -> held
        self.settings.daily_outreach_cap = 1
        await self.mind.store.insert("thoughts", persona_id=self.pid, text="another", source="wander",
                                     score=0.9, created_at=time.time())
        acts = await self.init.tick()
        gate = [a for a in acts if a["action"] == "gate"][0]
        self.assertEqual(gate["verdict"], "hold")

    async def test_quiet_hours_hold(self):
        self.settings.quiet_hours = "00:00-23:59"
        await self.mind.store.update("personas", self.pid, last_user_at=time.time() - 3600)
        await self.mind.store.insert("thoughts", persona_id=self.pid, text="x", source="wander", score=0.9,
                                     created_at=time.time())
        acts = await self.init.tick()
        self.assertEqual([a for a in acts if a["action"] == "gate"][0]["verdict"], "hold")

    async def test_wander_creates_thought(self):
        await self.mind.chat(self.pid, MSG)
        await self.mind.drain()
        p = await get_persona(self.mind.store, self.pid)
        t = await self.init.wander(p)
        self.assertIn("Maya", t)
        rows = await self.mind.store.all("SELECT source, score FROM thoughts WHERE source='wander'")
        self.assertEqual(len(rows), 1)

    async def test_sms_channel_pick(self):
        await self.mind.store.update("personas", self.pid, contact='{"sms":"+15550001"}', preferred_channel="sms")
        p = await get_persona(self.mind.store, self.pid)
        await self.init.send(p, "hey", reason="test")
        self.assertEqual(self.channels.sent[-1]["channel"], "sms")


class DeepTests(MindCase):
    async def test_map_lanes_reduce_and_return(self):
        lane = DeepLane(self.mind, planner=self.mind.b, critic=self.mind.b, max_lanes=4)
        self.mind.deep = lane
        job = await lane.submit(self.pid, "compare three phones")
        await self.mind.drain()
        row = await self.mind.store.one("SELECT * FROM deep_jobs WHERE id=?", job)
        self.assertEqual(row["status"], "done")
        self.assertEqual(row["result"], "merged result")
        self.assertEqual(len(json.loads(row["trace"])["lanes"]), 2)
        await self.mind.chat(self.pid, "anything back?")
        system = [c for c in self.mind.a.calls if not c["json_mode"]][-1]["messages"][0]["content"]
        self.assertIn("Your workshop finished", system)


class VoiceTests(MindCase):
    def test_chunker_cuts_first_sentence_early(self):
        c = SentenceChunker()
        out = c.feed("Well, honestly I think that is ") + c.feed("great. And another thing! ok") + c.flush()
        self.assertEqual(out[0], "Well, honestly I think that is great.")
        self.assertEqual(out[-1], "ok")
        c2 = SentenceChunker()
        self.assertEqual(c2.feed("So listen, about tomorrow's plan, "), ["So listen, about tomorrow's plan,"])

    async def test_voice_session_client_tts_and_barge_in(self):
        sent = []

        async def sj(o):
            sent.append(o)

        async def sb(b):
            sent.append(b)
        speech = SpeechIO(ModelEndpoint("", "", ""), ModelEndpoint("", "", ""))
        p = await get_persona(self.mind.store, self.pid)
        s = VoiceSession(self.mind, p, speech, sj, sb)
        await s.handle_frame({"type": "text", "text": MSG})
        await s.finish()
        says = [o for o in sent if isinstance(o, dict) and o["type"] == "say"]
        self.assertTrue(says)
        done = [o for o in sent if isinstance(o, dict) and o["type"] == "done"][0]
        self.assertIn("Maya", done["text"])
        self.assertIsNotNone(done["first_audio_ms"])
        # barge-in: interrupt before speaking suppresses audio
        sent.clear()
        s.interrupted = False
        await s.on_interrupt()
        await s._say(0, "should not be heard")
        self.assertEqual([o for o in sent if isinstance(o, dict) and o.get("type") == "say"], [])


class ChannelTests(MindCase):
    def test_twilio_signature(self):
        import base64
        import hashlib
        import hmac
        url, params = "https://x.test/hooks/twilio", {"Body": "hi", "From": "+1"}
        data = url + "".join(f"{k}{params[k]}" for k in sorted(params))
        sig = base64.b64encode(hmac.new(b"tok", data.encode(), hashlib.sha1).digest()).decode()
        self.assertTrue(twilio_signature_ok("tok", url, params, sig))
        self.assertFalse(twilio_signature_ok("tok", url, params, "bad"))


class InitiativeSafetyTests(MindCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.settings.quiet_hours = ""
        self.init = Initiative(self.mind, FakeChannels(self.settings), rng=random.Random(3))

    async def test_reminder_never_lost_when_model_skips(self):
        from sawii_persona.llm import ScriptedLLM
        await self.mind.chat(self.pid, MSG)
        await self.mind.drain()
        self.mind.a = ScriptedLLM("SKIP")
        await self.init.tick(time.time() + 5)
        out = await self.mind.store.all("SELECT text FROM outbox")
        self.assertEqual(out[0]["text"], "Reminder: check on Maya's exam")
        self.assertEqual(len(await self.mind.memory.due_loops(self.pid, time.time() + 10)), 0)

    async def test_never_pesters_after_two_unanswered(self):
        await self.mind.store.update("personas", self.pid, last_user_at=time.time() - 10 * 86400,
                                     last_reply_at=time.time() - 10 * 86400)
        p = await get_persona(self.mind.store, self.pid)
        self.assertTrue([c for c in await self.init._candidates(p, time.time()) if c["kind"] == "silence"])
        for _ in range(2):
            await self.mind.store.insert("outbox", persona_id=self.pid, text="hey?", reason="check-in",
                                         created_at=time.time() - 5 * 86400)
        await self.mind.store.update("personas", self.pid, last_reply_at=time.time() - 4 * 86400)
        p = await get_persona(self.mind.store, self.pid)
        self.assertFalse([c for c in await self.init._candidates(p, time.time()) if c["kind"] == "silence"])
