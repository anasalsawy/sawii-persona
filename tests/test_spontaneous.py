import random
import time

import helpers  # noqa: F401
from fakes import make_a, make_b, make_c
from helpers import StoreCase
from test_life import FakeChannels
from test_turn import MindCase

from sawii_persona.identity import get_persona
from sawii_persona.life.initiative import Initiative
from sawii_persona.memory.spontaneous import Spontaneous


class SpontaneousTests(StoreCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.sp = Spontaneous(self.store, self.embedder, half_life_min=90, band=0.30)

    async def mem(self, user, text, quote, sal=3, age_s=3600):
        t = await self.turn(user)
        e = await self.memory.new_event(self.pid, t)
        ids = await self.memory.add_layers(self.pid, e, [
            {"layer": "note", "author": "C", "kind": "detail", "text": text, "quote": quote, "salience": sal},
            {"layer": "fact", "author": "A", "text": f"We talked: {text}"}],
            sources=await self.memory.user_sources(self.pid))
        await self.store.run("UPDATE layers SET created_at = ? WHERE event_id = ?", time.time() - age_s, e)
        return ids

    async def test_priming_builds_up_then_pops_once(self):
        [nid, fid] = await self.mem("Maya plays the violin in the city orchestra", "Maya plays violin",
                                    "Maya plays the violin")
        await self.sp.prime(self.persona, "I talked to Maya today about groceries", surfaced=[])
        self.assertEqual(await self.sp.pops(self.persona), [])           # touched, not yet there
        await self.sp.prime(self.persona, "Maya says hi", surfaced=[])
        popped = await self.sp.pops(self.persona)                      # ...and now it arrives
        self.assertEqual(len(popped), 1)
        self.assertIn("violin", popped[0]["text"])
        self.assertEqual(await self.sp.pops(self.persona), [])           # not again for days

    async def test_activation_fades(self):
        await self.mem("Maya plays the violin", "Maya plays violin", "Maya plays the violin")
        await self.sp.prime(self.persona, "Maya", surfaced=[])
        await self.sp.prime(self.persona, "Maya again", surfaced=[])
        later = time.time() + 6 * 3600                                   # four half-lives later
        self.assertEqual(await self.sp.pops(self.persona, at=later), [])

    async def test_chain_spreads_to_same_moment_and_same_person(self):
        [n1, f1] = await self.mem("Maya's exam is friday", "Maya's exam Friday", "Maya's exam is friday")
        [n2, f2] = await self.mem("Maya plays the violin", "Maya plays violin", "Maya plays the violin")
        await self.sp.prime(self.persona, "unrelated words entirely", surfaced=[n1])
        rows = {r["id"]: r for r in await self.store.all("SELECT id, activation FROM layers")}
        self.assertGreater(rows[f1]["activation"], 0)    # same moment
        self.assertGreater(rows[n2]["activation"], 0)    # same person, other moment
        self.assertEqual(rows[n1]["activation"], 0)      # the surfaced one is spent

    async def test_late_recall_finds_what_was_missed(self):
        await self.mem("Maya's violin recital is in May", "Maya's violin recital in May",
                       "Maya's violin recital is in May")
        await self.mem("I bought a bike", "bought a bike", "I bought a bike", sal=1)
        late = await self.sp.late_recall(self.persona, "what should I get Maya for her birthday?", already=[],
                                         current_turn=None)
        self.assertIsNotNone(late)
        self.assertIn("recital", late["text"])
        self.assertIsNone(await self.sp.late_recall(self.persona, "what's the weather", already=[],
                                                    current_turn=None))


class SuddenInConversationTests(MindCase):
    async def test_oh_wait_after_answer_and_next_turn(self):
        # an old memory about Maya, never recalled this turn
        await self.mind.chat(self.pid, "my sister Maya has her exam on friday, I'm nervous")
        await self.mind.drain()
        await self.mind.store.run("UPDATE layers SET created_at = created_at - 7200")
        from sawii_persona.memory.recall import RecallResult

        async def misses(*a, **k):  # the fast recall misses it; only the slow search can find it
            return RecallResult()
        self.mind.recall.recall = misses
        sent = []

        class Out:
            async def consider(_, pid):
                sent.append(pid)
        self.mind.outbox = Out()
        self.mind.s.sudden_delay_seconds = 0
        await self.mind.chat(self.pid, "Maya called me")
        await self.mind.drain()
        thoughts = await self.mind.store.all("SELECT source, text FROM thoughts WHERE source='sudden'")
        self.assertTrue(thoughts)
        self.assertIn("Maya", thoughts[0]["text"])
        self.assertEqual(sent, [self.pid])  # the "oh wait" pop-up was triggered right away
        await self.mind.chat(self.pid, "anyway")
        system = [c for c in self.mind.a.calls if not c["json_mode"]][-1]["messages"][0]["content"]
        self.assertIn("just came back to you, out of nowhere", system)

    async def test_idle_pop_becomes_spontaneous_message(self):
        self.settings.quiet_hours = ""
        await self.mind.chat(self.pid, "my sister Maya has her exam on friday, I'm nervous")
        await self.mind.drain()
        await self.mind.store.run("UPDATE layers SET created_at = created_at - 7200, activation = 1.8, "
                                  "activated_at = ?, last_recalled_at = 0", time.time())
        await self.mind.store.update("personas", self.pid, last_user_at=time.time() - 3600)
        init = Initiative(self.mind, FakeChannels(self.settings), rng=random.Random(1))
        acts = await init.tick()
        kinds = [a["action"] for a in acts]
        self.assertIn("pop", kinds)
        self.assertIn("outreach", kinds)
        out = await self.mind.store.all("SELECT text FROM outbox")
        self.assertTrue(out)
