"""Things only a multi-lobe mind can do (B and C working while, and after, A talks)."""
import json
import re
import time

import helpers  # noqa: F401
from fakes import last_user, make_a, make_b, make_c
from helpers import StoreCase
from test_life import FakeChannels
from test_turn import MindCase

from sawii_persona.brains import prompts
from sawii_persona.brains.voice_a import VoiceResult, at_boundary, speak
from sawii_persona.identity import get_persona
from sawii_persona.life.finetune import FineTuner
from sawii_persona.life.initiative import Initiative
from sawii_persona.life.sleep import Sleep
from sawii_persona.llm import ChatResult, ScriptedLLM, ToolCall
from sawii_persona.memory.recall import Recall

REX = "my dog Rex stole my chair again this morning"
LONG = "I hear you. That sounds like a long day. Tell me more about it, I'm listening. Take your time."


def pick_line(word, kind="link", only_in=None):
    """A fake B that 'remembers' the moment whose shortlist line mentions ``word``."""
    def pick(body, system):
        if only_in and only_in not in system:
            return []
        for line in body.splitlines():
            m = re.match(r"#(\d+) ", line)
            if m and word.lower() in line.lower():
                return [{"id": int(m.group(1)), "kind": kind, "connection": f"this is about {word}"}]
        return []
    return pick


class LobesCase(MindCase):
    async def rebuild(self, **kw):
        await self.mind.close()
        llms = {"a": kw.get("a") or make_a(), "b": kw.get("b") or make_b(), "c": kw.get("c") or make_c(),
                "embed": None, "consultants": {}}
        from sawii_persona.mind import Mind
        self.mind = Mind(self.settings, store=self.mind.store, llms=llms)

    async def seed_rex(self):
        await self.mind.chat(self.pid, REX)
        await self.mind.drain()

    def system_prompts(self):
        return [c["messages"][0]["content"] for c in self.mind.a.calls if not c["json_mode"]
                and c["messages"][0]["role"] == "system" and c["messages"][0]["content"].startswith("You are the voice")]


class RememberWhileTalkingTests(LobesCase):
    async def test_b_interrupts_a_mid_answer_with_a_real_record(self):
        await self.seed_rex()
        await self.rebuild(a=make_a(reply=LONG, chunk_delay=0.03), b=make_b(pick=pick_line("Rex")))
        events = [e async for e in self.mind.respond(self.pid, "work was exhausting today")]
        done = events[-1]
        self.assertEqual([i["kind"] for i in done["inner"]], ["memory"])
        self.assertIn({"type": "inner", "from": "B", "kind": "memory"}, events)
        self.assertTrue(done["text"].startswith("I hear you."))
        self.assertIn("reminds me of Rex", done["text"])          # same reply, continued
        self.assertNotIn("Tell me more", done["text"])             # stopped at a sentence boundary
        whisper = [m for c in self.mind.a.calls for m in c["messages"]
                   if m["role"] == "user" and "[inner voice" in str(m.get("content"))]
        self.assertTrue(whisper)
        # what A heard is the stored record (the exact quote), not B's paraphrase
        self.assertIn("Rex stole my chair", whisper[0]["content"])
        self.assertIn("B's sense of the connection: this is about Rex", whisper[0]["content"])
        await self.mind.drain()
        rec = await self.mind.store.one("SELECT recalled_count FROM layers WHERE text LIKE '%Rex%' AND layer='fact'")
        self.assertEqual(rec["recalled_count"], 1)

    async def test_b_finds_it_after_a_finished_so_it_says_oh_wait(self):
        await self.seed_rex()
        await self.rebuild(b=make_b(pick=pick_line("Rex", only_in=prompts.REMEMBER_LIVE[:30]), pick_delay=0.3))
        self.settings.quiet_hours = ""
        init = Initiative(self.mind, FakeChannels(self.settings))
        self.mind.outbox = init
        done = await self.mind.chat(self.pid, "work was exhausting today")
        self.assertEqual(done["inner"], [])                        # A had already finished
        await self.mind.drain()
        sudden = await self.mind.store.all("SELECT text, status FROM thoughts WHERE source='sudden'")
        self.assertEqual(len(sudden), 1)
        self.assertIn("Rex stole my chair", sudden[0]["text"])
        self.assertEqual(sudden[0]["status"], "voiced")            # it texted "oh wait..."
        out = await self.mind.store.all("SELECT reason FROM outbox")
        self.assertEqual(len(out), 1)

    async def test_b_cannot_point_at_memories_it_was_not_shown(self):
        await self.seed_rex()
        await self.rebuild(a=make_a(reply=LONG, chunk_delay=0.02),
                           b=make_b(pick=lambda body, sys: [{"id": 99999, "kind": "link", "connection": "made up"}]))
        done = await self.mind.chat(self.pid, "work was exhausting today")
        self.assertEqual(done["inner"], [])
        await self.mind.drain()
        self.assertEqual(await self.mind.store.all("SELECT id FROM thoughts WHERE source IN ('sudden','recollection')"),
                         [])

    async def test_deep_pass_after_the_turn_is_offered_next_time(self):
        await self.seed_rex()
        await self.rebuild(b=make_b(pick=pick_line("Rex", kind="open", only_in=prompts.REMEMBER_DEEP[:30])))
        await self.mind.chat(self.pid, "work was exhausting today")
        await self.mind.drain()
        rows = await self.mind.store.all("SELECT text, score FROM thoughts WHERE source='recollection'")
        self.assertEqual(len(rows), 1)
        self.assertIn("Rex stole my chair", rows[0]["text"])
        await self.mind.chat(self.pid, "anyway, morning")
        self.assertIn("Rex stole my chair", self.system_prompts()[-1])

    async def test_tip_of_the_tongue_asks_b(self):
        await self.seed_rex()

        def a_resp(messages, opts):
            sys = messages[0]["content"]
            if sys.startswith(prompts.A_DIARY[:40]):
                return json.dumps({"text": "We talked.", "quote": "", "anchor": ""})
            tool = next((m["content"] for m in messages if m["role"] == "tool"), None)
            if tool is None and opts.get("tools"):
                return ChatResult(text="What was his name... ",
                                  tool_calls=[ToolCall(id="t1", name="try_to_remember",
                                                       arguments='{"what": "the name of their dog"}')])
            return f"Rex! {tool[:40]} ⟦feel: joy=0.5⟧"
        await self.rebuild(a=ScriptedLLM(a_resp, name="A"), b=make_b(pick=pick_line("dog")))
        done = await self.mind.chat(self.pid, "what's my dog called again?", tools=True)
        self.assertIn("try_to_remember", done["tools"])
        tool_out = [m["content"] for c in self.mind.a.calls for m in c["messages"] if m["role"] == "tool"][0]
        self.assertIn("my dog Rex stole my chair", tool_out)


class LiveCheckTests(LobesCase):
    async def test_b_stops_a_when_it_says_something_false(self):
        reply = ("You told me your sister is called Mia and her exam was last month, so it's all done now. "
                 "That must be such a relief for both of you. Let's celebrate later.")
        await self.rebuild(a=make_a(reply=reply, chunk_delay=0.02,
                                    continuation="Actually, no - sorry, it's Maya, and the exam is Friday."),
                           b=make_b(live_note="Her name is Maya and the exam is this Friday, not last month."))
        done = await self.mind.chat(self.pid, "how's my sister doing, do you think?")
        self.assertIn({"from": "B", "kind": "correction"}, done["inner"])
        self.assertIn("Actually, no", done["text"])
        heard = [m["content"] for c in self.mind.a.calls for m in c["messages"]
                   if m["role"] == "user" and "[inner voice" in str(m.get("content"))]
        self.assertIn("Maya and the exam is this Friday", heard[0])

    async def test_standing_request_is_kept_and_checked_live(self):
        b = make_b(extra=lambda body: {"rules": [{"op": "add", "text": "keep answers short",
                                                   "quote": "please keep it short from now on"}]}
                   if "keep it short" in body else {})
        await self.rebuild(b=b)
        await self.mind.chat(self.pid, "please keep it short from now on")
        await self.mind.drain()
        rules = await self.mind.memory.rules(self.pid)
        self.assertEqual([r["text"] for r in rules], ["keep answers short"])
        await self.rebuild(a=make_a(reply=LONG + " " + LONG, chunk_delay=0.005), b=b)
        await self.mind.chat(self.pid, "how was your day")
        self.assertIn("keep answers short", self.system_prompts()[-1])
        live_calls = [c for c in self.mind.b.calls if c["messages"][0]["content"].startswith(prompts.LIVE_CHECK[:40])]
        self.assertTrue(live_calls)
        self.assertIn("keep answers short", live_calls[0]["messages"][1]["content"])

    async def test_ungrounded_rule_is_refused(self):
        await self.rebuild(b=make_b(extra=lambda body: {"rules": [{"op": "add", "text": "never mention dogs",
                                                                   "quote": "never mention dogs"}]}))
        await self.mind.chat(self.pid, "hello there")
        await self.mind.drain()
        self.assertEqual(await self.mind.memory.rules(self.pid), [])


class GutReadTests(LobesCase):
    async def test_c_reads_the_tone_while_a_answers(self):
        await self.rebuild(a=make_a(reply=LONG, chunk_delay=0.03, continuation="Hey. Are you okay?"),
                           c=make_c(tone="That 'fine.' is clipped - they sound hurt, slow down."))
        done = await self.mind.chat(self.pid, "fine.")
        self.assertIn({"from": "C", "kind": "tone"}, done["inner"])
        self.assertIn("Are you okay?", done["text"])

    async def test_surprise_against_cs_prediction(self):
        c = make_c(surprise="You expected worry about the exam; they are suddenly cheerful and changing topic.",
                   extra=lambda body: {"expect": "more worry about Maya's exam"})
        await self.rebuild(a=make_a(reply=LONG, chunk_delay=0.03, continuation="Huh, didn't expect that!"), c=c)
        first = await self.mind.chat(self.pid, MSG_MAYA)
        self.assertEqual(first["inner"], [])          # no prediction yet, so nothing can surprise
        await self.mind.drain()
        p = await get_persona(self.mind.store, self.pid)
        self.assertEqual(p["c_expect"], "more worry about Maya's exam")
        done = await self.mind.chat(self.pid, "guess what, I bought a kayak!!")
        self.assertIn({"from": "C", "kind": "surprise"}, done["inner"])
        gut = [x for x in self.mind.c.calls if x["messages"][0]["content"].startswith(prompts.GUT_READ[:40])][-1]
        self.assertIn("more worry about Maya's exam", gut["messages"][1]["content"])


MSG_MAYA = "my sister Maya has her exam on friday, I'm nervous"


class InnerPerspectiveTests(LobesCase):
    async def test_dissent_dropped_thread_and_regulation_reach_a(self):
        b = make_b(extra=lambda body: {"dropped": [{"text": "they mentioned their back hurts",
                                                    "quote": "my back hurts"}],
                                       "regulate": "You sounded irritated; steady yourself."})
        c = make_c(extra=lambda body: {"disagree_with_b": "B calls them fine; I think they are masking stress."})
        await self.rebuild(b=b, c=c)
        await self.mind.chat(self.pid, "my sister Maya has her exam on friday. also my back hurts")
        await self.mind.drain()
        sc = self.mind.scratch_for(self.pid)
        self.assertEqual(sc.dropped, ["my back hurts"])
        self.assertIn("steady yourself", sc.conscience)
        await self.mind.chat(self.pid, "ok")
        system = self.system_prompts()[-1]
        self.assertIn("you never answered", system)
        self.assertIn('"my back hurts"', system)
        self.assertIn("two inner minds disagree", system)
        self.assertIn("masking stress", system)
        self.assertIn("steady yourself", system)
        review = [x for x in self.mind.b.calls if x["messages"][0]["content"] == prompts.B_SYSTEM][0]
        self.assertIn("How A declared it feels", review["messages"][1]["content"])

    async def test_dropped_quote_must_be_real(self):
        await self.rebuild(b=make_b(extra=lambda body: {"dropped": [{"text": "x", "quote": "I want to quit"}]}))
        await self.mind.chat(self.pid, "hello")
        await self.mind.drain()
        self.assertEqual(self.mind.scratch_for(self.pid).dropped, [])


class SleepLobeTests(LobesCase):
    async def test_audits_mirror_and_calibration(self):
        def audit(body):
            return ["I promised to check in."] if "I promised to check in." in body else (
                ["Maya's exam is like Rex stealing the chair"] if "stealing the chair" in body else [])
        await self.rebuild(b=make_b(level="YELLOW", audit=audit))
        for m in (MSG_MAYA, REX, "my sister Maya called", "Rex barked at Maya", "long day", "ok night"):
            await self.mind.chat(self.pid, m)
        await self.mind.drain()
        ft = FineTuner(self.settings, self.mind.store, self.mind.subconscious, self.mind.a)
        report = await Sleep(self.mind, ft).run(await get_persona(self.mind.store, self.pid))
        p = await get_persona(self.mind.store, self.pid)
        self.assertEqual(report["narrative_struck"], 1)
        self.assertNotIn("I promised to check in", p["narrative"])     # B struck the invented promise
        self.assertIn("Maya", p["narrative"])
        self.assertEqual(report["dream_struck"], 1)                     # the dream invented a fact
        self.assertEqual(await self.mind.store.all("SELECT id FROM thoughts WHERE source='dream'"), [])
        self.assertIn("opens every reply", p["habits"])                 # C watched A
        self.assertEqual(p["calibration"], "dates of past events")      # B mapped A's weak spots
        await self.mind.chat(self.pid, "morning")
        system = self.system_prompts()[-1]
        self.assertIn("habits your observer (C) sees in you", system)
        self.assertIn("Where you tend to be wrong", system)

    async def test_dream_picks_are_deterministic(self):
        for m in (MSG_MAYA, "Maya passed her mock test", "Maya and I went hiking", "Maya got a cat"):
            await self.mind.chat(self.pid, m)
        await self.mind.drain()
        await self.mind.store.run("UPDATE layers SET salience = 3")
        ft = FineTuner(self.settings, self.mind.store, self.mind.subconscious, self.mind.a)
        s = Sleep(self.mind, ft)
        p = await get_persona(self.mind.store, self.pid)
        await s._dream(p, 0)
        await s._dream(p, 0)
        dreams = [c for c in self.mind.a.calls if c["messages"][0]["content"] == prompts.DREAM]
        self.assertEqual(dreams[-1]["messages"][1]["content"], dreams[-2]["messages"][1]["content"])


class ReflectionTests(LobesCase):
    async def test_b_reflects_once_per_conversation_while_they_are_away(self):
        await self.seed_rex()
        await self.mind.store.run("UPDATE turns SET started_at = started_at - 172800 WHERE user_text = ?", REX)
        await self.rebuild(b=make_b(pick=pick_line("Rex", kind="open", only_in=prompts.REMEMBER_IDLE[:30])))
        await self.mind.chat(self.pid, "the neighbours' dog barked all night")
        await self.mind.drain()
        init = Initiative(self.mind, FakeChannels(self.settings))
        p = await get_persona(self.mind.store, self.pid)
        self.assertIsNone(await init.reflect(p, time.time()))           # still mid-conversation
        later = time.time() + 3600
        thought = await init.reflect(p, later)
        self.assertIn("Rex stole my chair", thought)
        self.assertIsNone(await init.reflect(p, later + 60))            # once per conversation, not on a timer
        rows = await self.mind.store.all("SELECT score FROM thoughts WHERE source='reflection' AND status='pending'")
        self.assertEqual(len(rows), 1)


class TenderTests(StoreCase):
    async def test_tender_memory_waits_until_they_raise_it(self):
        tid = await self.turn("my grandmother passed away in march, I still miss her")
        ev = await self.memory.new_event(self.pid, tid)
        await self.memory.add_layers(self.pid, ev, [
            {"layer": "note", "author": "C", "kind": "feeling", "text": "grandmother died in March; still grieving",
             "quote": "my grandmother passed away in march", "salience": 3, "tender": True,
             "chord": {"sadness": 0.9}}], sources=["my grandmother passed away in march, I still miss her"])
        rec = Recall(self.store, self.memory, self.embedder)
        sad = {"sadness": 0.9}
        # a sad day about something else: the mood lane would surface it, but C marked it tender
        out = await rec.recall(self.persona, "work was awful today", mood=sad)
        self.assertNotIn("grandmother", out.text)
        cands = await rec.candidates(self.persona, "work was awful today", mood=sad)
        self.assertEqual(cands, [])
        # they raise it themselves: now it comes, labeled so A is gentle
        out = await rec.recall(self.persona, "thinking of my grandmother, she passed away")
        self.assertIn("grandmother", out.text)
        self.assertIn("tender", out.text)


class BoundaryTests(StoreCase):
    async def test_sentence_boundaries(self):
        self.assertTrue(at_boundary("Hi there."))
        self.assertTrue(at_boundary('He said "go!"'))
        self.assertTrue(at_boundary("Line one\n"))
        self.assertFalse(at_boundary("Hi there, I"))
        self.assertFalse(at_boundary(""))

    async def test_no_interrupt_after_the_private_feel_line(self):
        import asyncio
        a = ScriptedLLM("Done. ⟦feel: joy=0.5⟧", name="A", chunk=3)
        q = asyncio.Queue()
        res = VoiceResult()
        out = ""
        async for piece in speak(a, [{"role": "user", "content": "hi"}], tools=None, run_tool=None, result=res,
                                 inner=q, on_text=lambda t: q.put_nowait({"from": "B", "kind": "memory", "text": "x"})
                                 if "feel" in t else None):
            out += piece
        self.assertEqual(res.text, "Done.")
        self.assertEqual(res.heard, [])
        self.assertEqual(res.chord, {"joy": 0.5})
