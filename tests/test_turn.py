import os
import tempfile
import unittest

import helpers  # noqa: F401  (path setup)
from fakes import make_a, make_b, make_c, make_consultant

from sawii_persona.brains.voice_a import FeelFilter, strip_feel
from sawii_persona.config import load_settings
from sawii_persona.identity import create_persona, get_persona
from sawii_persona.mind import Mind
from sawii_persona.store import jload

MSG = "my sister Maya has her exam on friday, I'm nervous. remind me to ask her about it"


class FeelTests(unittest.TestCase):
    def test_filter_streaming_split_marker(self):
        f = FeelFilter()
        out = ""
        for piece in ["Hello the", "re ⟦fe", "el: joy=0.7", " sadness=0.2⟧"]:
            out += f.feed(piece)
        out += f.flush()
        self.assertEqual(out.strip(), "Hello there")
        self.assertEqual(f.chord(), {"joy": 0.7, "sadness": 0.2})

    def test_strip_alt_marker(self):
        text, chord = strip_feel("ok [[feel: anger=0.3]]")
        self.assertEqual(text, "ok")
        self.assertEqual(chord, {"anger": 0.3})

    def test_bracket_text_not_eaten(self):
        text, chord = strip_feel("see [link] and [f] here")
        self.assertEqual(text, "see [link] and [f] here")


class MindCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = load_settings(data_dir=self.tmp.name, db_path=os.path.join(self.tmp.name, "m.db"),
                                      intrusion_probability=0.0, background_enabled=False)
        self.mind = self.build()
        self.pid, self.key = await create_persona(self.mind.store, name="Koko", definition="You are Koko, playful.",
                                                  user_name="Anas")

    def build(self, **kw):
        llms = {"a": kw.get("a") or make_a(use_tools=kw.get("tools", False)), "b": kw.get("b") or make_b(),
                "c": make_c(), "embed": None, "consultants": kw.get("consultants", {})}
        return Mind(self.settings, llms=llms)

    async def asyncTearDown(self):
        await self.mind.close()
        self.mind.store.close()
        self.tmp.cleanup()


class TurnTests(MindCase):
    async def test_full_turn_writes_three_layers(self):
        events = [ev async for ev in self.mind.respond(self.pid, MSG)]
        kinds = [e["type"] for e in events]
        self.assertEqual(kinds[0], "start")
        self.assertEqual(kinds[-1], "done")
        visible = "".join(e["text"] for e in events if e["type"] == "delta")
        self.assertIn("Maya", visible)
        self.assertNotIn("feel", visible)
        self.assertEqual(events[-1]["chord"], {"joy": 0.5, "tenderness": 0.6})
        await self.mind.drain()
        rows = await self.mind.store.all("SELECT layer, author FROM layers ORDER BY id")
        layers = {(r["layer"], r["author"]) for r in rows}
        self.assertTrue({("collateral", "B"), ("note", "C"), ("reading", "C"), ("fact", "A")} <= layers, layers)
        self.assertEqual(len(await self.mind.memory.open_loops(self.pid)), 1)
        self.assertEqual(len(await self.mind.memory.hypotheses(self.pid)), 1)
        ents = await self.mind.store.all("SELECT name FROM entities")
        self.assertEqual(ents[0]["name"], "Maya")
        p = await get_persona(self.mind.store, self.pid)
        self.assertTrue(jload(p["mood"]))
        self.assertTrue(jload(p["user_mood"]))
        self.assertEqual(self.mind.scratch_for(self.pid).conscience, "Ask how they are sleeping.")
        self.assertIn("Maya", self.mind.scratch_for(self.pid).c_brief)
        juice = await self.mind.store.all("SELECT raw_transcript, variance FROM juice")
        # raw transcript, if kept, is byte-exact
        for j in juice:
            self.assertEqual(j["raw_transcript"], MSG)

    async def test_second_turn_context_has_memory_brief_and_time(self):
        await self.mind.chat(self.pid, MSG)
        await self.mind.drain()
        await self.mind.chat(self.pid, "how did Maya do?")
        sent = self.mind.a.calls[-2]["messages"] if self.mind.a.calls[-1]["json_mode"] else self.mind.a.calls[-1]["messages"]
        system = sent[0]["content"]
        self.assertIn("[time] Since your last reply", system)
        self.assertIn("Private note from your adjutant: Ask how they are sleeping.", system)
        self.assertIn("Open loops your adjutant is tracking", system)
        self.assertIn("Your verification record: 1 clean", system)
        self.assertIn("sister Maya's exam Friday", system)
        self.assertIn("You are Koko, playful.", system)
        # tail comes from the server-side journal
        self.assertTrue(any(m["role"] == "assistant" for m in sent[1:]))

    async def test_fetch_consults_tool(self):
        await self.mind.close()
        self.mind = self.build(tools=True, consultants={"thinker": make_consultant()})
        self.pid, _ = await create_persona(self.mind.store, name="K")
        out = await self.mind.chat(self.pid, MSG)
        self.assertIn("fetch_consults", out["tools"])
        self.assertIn("Hang on.", out["text"])
        self.assertIn("Exams on Friday", out["text"])

    async def test_correction_required_reaches_a(self):
        await self.mind.close()
        self.mind = self.build(b=make_b(level="RED", correction="The exam is Friday, not Thursday."))
        self.pid, _ = await create_persona(self.mind.store, name="K")
        await self.mind.chat(self.pid, MSG)
        await self.mind.drain()
        await self.mind.chat(self.pid, "ok")
        system = [c for c in self.mind.a.calls if not c["json_mode"]][-1]["messages"][0]["content"]
        self.assertIn("CORRECTION REQUIRED", system)
        self.assertIn("recently RED", system)
        thoughts = await self.mind.store.all("SELECT source FROM thoughts")
        self.assertIn("correction", [t["source"] for t in thoughts])

    async def test_show_verdict_option(self):
        self.mind.s.show_verdict_to_user = True
        out = await self.mind.chat(self.pid, MSG)
        self.assertEqual(out["verdict"]["level"], "GREEN")

    async def test_a_failure_is_honest(self):
        await self.mind.close()

        def boom(messages, opts):
            raise RuntimeError("provider down")
        from sawii_persona.llm import ScriptedLLM
        self.mind = self.build(a=ScriptedLLM(boom))
        self.pid, _ = await create_persona(self.mind.store, name="K")
        events = [e async for e in self.mind.respond(self.pid, "hi")]
        self.assertIn("error", [e["type"] for e in events])
        self.assertIn("cut off", events[-1]["text"])


class RevisitTests(MindCase):
    async def test_changes_its_mind_out_loud(self):
        await self.mind.chat(self.pid, "is coffee bad for my heart honestly?")
        await self.mind.drain()
        self.mind.b = make_b(level="YELLOW")
        await self.mind.chat(self.pid, "is coffee bad for my heart, honestly?")
        await self.mind.drain()
        rows = await self.mind.store.all("SELECT author, text FROM layers WHERE layer='afterthought'")
        self.assertEqual(len(rows), 1)
        self.assertIn("On reflection", rows[0]["text"])
        self.assertIn("GREEN", rows[0]["text"])
        self.assertIn("YELLOW", rows[0]["text"])
