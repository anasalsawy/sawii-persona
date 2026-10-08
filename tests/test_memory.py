import random
import time

from helpers import StoreCase

from sawii_persona import emotion
from sawii_persona.memory.grounding import ground
from sawii_persona.memory.recall import Recall
from sawii_persona.memory.records import parse_due


class GroundingTests(StoreCase):
    async def test_ground_returns_original_slice(self):
        src = ["Honestly… I CAN’T stand mornings, my dog Rex wakes me at 5"]
        self.assertEqual(ground("i can't stand mornings", src), "I CAN’T stand mornings")
        self.assertIsNone(ground("I love mornings", src))
        self.assertIsNone(ground("", src))
        self.assertEqual(ground('"my dog Rex wakes me at 5."', src), "my dog Rex wakes me at 5")


class LayerTests(StoreCase):
    async def test_quote_or_die_and_single_writer(self):
        tid = await self.turn("my sister Maya has her exam on friday, I'm nervous for her")
        ev = await self.memory.new_event(self.pid, tid)
        src = await self.memory.user_sources(self.pid)
        ids = await self.memory.add_layers(self.pid, ev, [
            {"layer": "note", "author": "C", "kind": "detail", "text": "Maya's exam is Friday",
             "quote": "my sister Maya has her exam on friday", "salience": 3},
            {"layer": "note", "author": "C", "text": "invented", "quote": "I hate my sister"},  # not said
            {"layer": "note", "author": "B", "text": "wrong author", "quote": "I'm nervous for her"},
            {"layer": "collateral", "author": "B", "text": "worried for Maya", "quote": "I'm nervous for her"},
            {"layer": "fact", "author": "A", "text": "We talked about Maya's exam."},
        ], sources=src)
        self.assertEqual(len(ids), 3)
        rows = await self.store.all("SELECT layer, author, quote FROM layers ORDER BY id")
        self.assertEqual([r["layer"] for r in rows], ["note", "collateral", "fact"])
        self.assertEqual(rows[0]["quote"], "my sister Maya has her exam on friday")

    async def test_supersession_on_revision(self):
        t1 = await self.turn("I love coffee, three cups a day")
        e1 = await self.memory.new_event(self.pid, t1)
        await self.memory.add_layers(self.pid, e1, [{"layer": "note", "author": "C", "kind": "preference",
                                                      "text": "loves coffee", "quote": "I love coffee"}],
                                     sources=await self.memory.user_sources(self.pid))
        t2 = await self.turn("actually I quit coffee, it's tea now")
        e2 = await self.memory.new_event(self.pid, t2)
        await self.memory.add_layers(self.pid, e2, [{"layer": "note", "author": "C", "kind": "preference",
                                                      "text": "quit coffee, drinks tea",
                                                      "quote": "actually I quit coffee, it's tea now"}],
                                     sources=await self.memory.user_sources(self.pid))
        rows = await self.store.all("SELECT text, status, superseded_by FROM layers ORDER BY id")
        self.assertEqual(rows[0]["status"], "superseded")
        self.assertEqual(rows[1]["status"], "active")

    async def test_reconsolidation_keeps_quote(self):
        t = await self.turn("haha my dog keeps stealing my chair")
        e = await self.memory.new_event(self.pid, t)
        [lid] = await self.memory.add_layers(self.pid, e, [{"layer": "note", "author": "C", "kind": "joke",
                                                            "text": "dog steals chair",
                                                            "quote": "my dog keeps stealing my chair"}],
                                             sources=await self.memory.user_sources(self.pid))
        await self.memory.reinterpret(self.pid, lid, "it was a running joke while debugging")
        row = await self.store.one("SELECT quote, interpretations FROM layers WHERE id=?", lid)
        self.assertEqual(row["quote"], "my dog keeps stealing my chair")
        self.assertIn("running joke", row["interpretations"])

    async def test_hypotheses_confirm_contradict(self):
        await self.turn("ugh, fine, maybe. I don't know.")
        src = await self.memory.user_sources(self.pid)
        st = await self.memory.apply_hypotheses(self.pid, [
            {"op": "new", "claim": "hedges before committing", "quotes": ["fine, maybe"]},
            {"op": "new", "claim": "ungrounded", "quotes": ["never said this"]}], src)
        self.assertEqual(st["new"], 1)
        self.assertEqual(st["dropped"], 1)
        [h] = await self.memory.hypotheses(self.pid)
        c0 = h["confidence"]
        await self.memory.apply_hypotheses(self.pid, [{"op": "confirm", "id": h["id"], "quotes": ["I don't know"]}], src)
        [h2] = await self.memory.hypotheses(self.pid)
        self.assertGreater(h2["confidence"], c0)
        await self.memory.apply_hypotheses(self.pid, [{"op": "contradict", "id": h["id"], "quotes": ["fine"],
                                                       "revised_claim": "commits once reassured"}], src)
        active = await self.memory.hypotheses(self.pid)
        self.assertEqual([x["claim"] for x in active], ["commits once reassured"])
        self.assertEqual(active[0]["revision_of"], h["id"])
        # decay: an old unconfirmed hypothesis fades
        await self.store.run("UPDATE hypotheses SET last_confirmed_at = ?", time.time() - 400 * 86400)
        self.assertEqual(await self.memory.fade_hypotheses(self.pid, 21), 1)

    async def test_loops_and_due(self):
        await self.turn("remind me to call mom tomorrow at 6pm", reply="I'll remind you.")
        src = await self.memory.user_sources(self.pid)
        st = await self.memory.apply_loops(self.pid, [
            {"op": "open", "kind": "reminder", "text": "call mom", "quote": "remind me to call mom", "due": "in 2s"},
            {"op": "open", "text": "no quote"}], src, tz="UTC", turn_id=None)
        self.assertEqual(st, {"opened": 1, "closed": 0, "dropped": 1})
        self.assertEqual(await self.memory.due_loops(self.pid), [])
        self.assertEqual(len(await self.memory.due_loops(self.pid, time.time() + 5)), 1)
        self.assertIsNotNone(parse_due("2030-01-01T09:00:00", "Africa/Cairo"))

    async def test_integrity_streak(self):
        for lvl in ["RED", "GREEN", "GREEN", "GREEN"]:
            await self.memory.record_verdict(self.pid, None, lvl, "r", [])
        integ = await self.memory.integrity(self.pid)
        self.assertEqual(integ["green_streak"], 3)
        self.assertEqual(integ["clean_rate"], 0.75)


class RecallTests(StoreCase):
    async def _note(self, user, text, quote, kind="detail", sal=2, chord=None):
        t = await self.turn(user)
        e = await self.memory.new_event(self.pid, t, chord=chord)
        return await self.memory.add_layers(self.pid, e, [
            {"layer": "note", "author": "C", "kind": kind, "text": text, "quote": quote, "salience": sal,
             "chord": chord},
            {"layer": "fact", "author": "A", "text": f"We talked: {text}"}],
            sources=await self.memory.user_sources(self.pid))

    async def test_topic_recall_stitches_layers(self):
        await self._note("my sister Maya has her exam on friday", "Maya's exam Friday",
                         "my sister Maya has her exam on friday")
        await self._note("I bought a red bicycle", "bought red bicycle", "I bought a red bicycle")
        rec = Recall(self.store, self.memory, self.embedder, intrusion_p=0, rng=random.Random(1))
        res = await rec.recall(self.persona, "how did Maya's exam go?")
        self.assertIn("Maya", res.text)
        self.assertIn("[fact·A]", res.text)
        self.assertIn("[note·C·detail]", res.text)
        self.assertNotIn("bicycle", res.text)

    async def test_mood_lane_surfaces_by_feeling(self):
        grief = {"sadness": 0.8, "tenderness": 0.5}
        await self._note("I still miss grandpa's garden", "misses grandpa's garden",
                         "I still miss grandpa's garden", chord=grief, sal=3)
        rec = Recall(self.store, self.memory, self.embedder, intrusion_p=0)
        res = await rec.recall(self.persona, "random unrelated words xyz", mood={"sadness": 0.7, "tenderness": 0.4})
        self.assertIn("grandpa", res.text)
        self.assertIn("feels like now", res.text)

    async def test_unaddressed_queue(self):
        ids = await self._note("btw my knee has been hurting", "knee hurts", "my knee has been hurting", kind="aside")
        await self.store.run("UPDATE layers SET created_at = ?", time.time() - 5 * 86400)
        rec = Recall(self.store, self.memory, self.embedder, intrusion_p=0)
        res = await rec.recall(self.persona, "what should I cook tonight")
        self.assertIn("knee", res.text)
        self.assertIn("nobody ever followed up", res.text)
        await self.memory.mark_recalled(self.pid, res.layer_ids)
        res2 = await rec.recall(self.persona, "what should I cook tonight")
        self.assertNotIn("nobody ever followed up", res2.text)
        self.assertTrue(ids)

    async def test_chord_math(self):
        self.assertEqual(emotion.describe({"joy": 0.6, "sadness": 0.7}), "bittersweet")
        self.assertGreater(emotion.resonance({"joy": .6, "sadness": .7}, {"joy": .5, "sadness": .6}), 0.95)
        self.assertLess(emotion.resonance({"joy": 1}, {"anger": 1}), 0.01)
        self.assertTrue(emotion.color({"anger": 0.9}).startswith("#"))
        self.assertEqual(emotion.parse_declaration("joy=.6 love=0.4"), {"joy": 0.6, "tenderness": 0.4})
