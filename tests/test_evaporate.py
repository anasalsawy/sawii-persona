"""The overflowing cup: what is already consolidated may wear away, gradually.

What this must prove:
  * under budget, the old promise stands - nothing is touched;
  * over budget, records step down ONE rung, never two;
  * the person's own words survive longest;
  * tender marks, recently recalled records and live state are never touched;
  * the journal of turns is not on the ladder at all.
"""
import unittest

import helpers  # noqa: F401  (path setup)

from sawii_persona.memory import evaporate


class CupTests(helpers.StoreCase):
    async def _note(self, text, quote, *, tender=False, layer="note", author="B"):
        tid = await self.turn(quote, "ok")
        ev = await self.memory.new_event(self.pid, tid)
        await self.memory.add_layers(self.pid, ev, [
            {"layer": layer, "author": author, "kind": "detail", "text": text, "quote": quote,
             "anchor": "the exam", "tender": tender}], sources=[quote])
        return tid

    async def _fill(self, n=3, size=400):
        for i in range(n):
            await self._note(f"memory number {i} " + "x" * size, f"they said thing {i}")

    async def test_under_budget_nothing_is_touched(self):
        await self._fill(3)
        report = await evaporate.evaporate(self.store, self.pid, budget=10_000_000)
        self.assertFalse(report["over"])
        self.assertEqual(report["steps"], 0)
        rows = await self.store.all("SELECT fidelity FROM layers WHERE status='active'")
        self.assertTrue(rows)
        self.assertEqual({r["fidelity"] for r in rows}, {"full"})   # the old promise holds

    async def test_over_budget_steps_one_rung_and_keeps_their_words(self):
        await self._fill(3)
        report = await evaporate.evaporate(self.store, self.pid, budget=1)
        self.assertTrue(report["over"])
        self.assertGreater(report["steps"], 0)
        self.assertEqual(report["to_trace"], 0)          # one rung only, never two
        rows = await self.store.all("SELECT fidelity, quote FROM layers WHERE status='active'")
        stepped = [r for r in rows if r["fidelity"] == "gist"]
        self.assertTrue(stepped)
        self.assertTrue(all(r["quote"] for r in stepped))  # the person's words survive

    async def test_a_second_night_lets_the_details_go(self):
        await self._fill(3)
        await evaporate.evaporate(self.store, self.pid, budget=1)
        await evaporate.evaporate(self.store, self.pid, budget=1)
        rows = await self.store.all("SELECT fidelity, text, quote FROM layers WHERE status='active'")
        traced = [r for r in rows if r["fidelity"] == "trace"]
        self.assertTrue(traced)
        self.assertTrue(all(r["quote"] == "" for r in traced))
        self.assertTrue(all("worn away" in r["text"] for r in traced))

    async def test_tender_marks_and_recent_recall_are_never_touched(self):
        await self._note("a painful thing " + "x" * 400, "they said the painful thing", tender=True)
        await self._note("a remembered thing " + "x" * 400, "they said the remembered thing")
        # the second record was recalled yesterday: inside the protection window
        rows = await self.store.all("SELECT id FROM layers WHERE tender = 0")
        await self.store.update("layers", rows[0]["id"], recalled_count=3, last_recalled_at=__import__("time").time())
        report = await evaporate.evaporate(self.store, self.pid, budget=1)
        self.assertEqual(report["steps"], 0)
        self.assertEqual({r["fidelity"] for r in await self.store.all("SELECT fidelity FROM layers")}, {"full"})

    async def test_live_state_and_the_journal_are_not_on_the_ladder(self):
        await self._note("a fact " + "x" * 400, "they said a fact")
        await self.memory.apply_hypotheses(self.pid, [{"op": "new", "claim": "worries about family",
                                                       "quotes": ["they said a fact"]}],
                                           sources=["they said a fact"])
        turns_before = len(await self.store.all("SELECT id FROM turns"))
        await evaporate.evaporate(self.store, self.pid, budget=1)
        self.assertEqual(len(await self.memory.hypotheses(self.pid)), 1)
        self.assertEqual(len(await self.store.all("SELECT id FROM turns")), turns_before)


if __name__ == "__main__":
    unittest.main()
