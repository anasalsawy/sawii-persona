"""The anchor: C's wrongness dataset, and the rehearsal that becomes the score.

This is the seed of the aging loop's gate. What it must prove:
  * C records what it judged wrong, joined to what was actually said;
  * the dataset can be replayed without touching the conversation or memory;
  * the score is stored as history, so drift is visible over weeks.
"""
import os
import tempfile
import unittest

import helpers  # noqa: F401  (path setup)
from fakes import make_a, make_b, make_c

from sawii_persona.config import load_settings
from sawii_persona.identity import create_persona, get_persona
from sawii_persona.life import anchor
from sawii_persona.mind import Mind

MSG = "my sister Maya has her exam on friday, I'm nervous"


class AnchorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = load_settings(data_dir=self.tmp.name, db_path=os.path.join(self.tmp.name, "a.db"),
                                      background_enabled=False)
        self.mind = Mind(self.settings, llms={"a": make_a(), "b": make_b(), "c": make_c(level="RED"),
                                              "embed": None, "consultants": {}})
        self.pid, _ = await create_persona(self.mind.store, name="Koko", definition="You are Koko.",
                                           user_name="Anas")

    async def asyncTearDown(self):
        await self.mind.close()
        self.mind.store.close()
        self.tmp.cleanup()

    async def test_the_wrongness_dataset_is_what_was_actually_said(self):
        await self.mind.chat(self.pid, MSG)
        await self.mind.drain()
        cases = await anchor.wrong_cases(self.mind.store, self.pid)
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0]["level"], "RED")
        self.assertIn("Maya", cases[0]["user_text"])      # joined to the real turn
        self.assertTrue(cases[0]["findings"])             # C's evidence travels with it

    async def test_replay_scores_and_is_recorded_as_history(self):
        await self.mind.chat(self.pid, MSG)
        await self.mind.drain()
        before = await self.mind.store.all("SELECT id FROM turns")
        before_layers = await self.mind.store.all("SELECT id FROM layers")

        # A still gets it wrong -> the score is 0
        report = await anchor.anchor_run(self.mind, await get_persona(self.mind.store, self.pid))
        self.assertEqual(report["cases"], 1)
        self.assertEqual(report["passed"], 0)
        self.assertEqual(report["score"], 0.0)

        # a clean verifier now -> the same case passes
        self.mind.c = make_c()
        report = await anchor.anchor_run(self.mind, await get_persona(self.mind.store, self.pid))
        self.assertEqual(report["score"], 1.0)

        runs = await self.mind.store.all("SELECT cases, passed, score FROM anchor_runs ORDER BY id")
        self.assertEqual([r["score"] for r in runs], [0.0, 1.0])
        p = await get_persona(self.mind.store, self.pid)
        self.assertEqual(p["anchor_score"], 1.0)
        self.assertTrue(p["anchor_at"])

        # a rehearsal is not a conversation: no turns, no memory written
        self.assertEqual(len(await self.mind.store.all("SELECT id FROM turns")), len(before))
        self.assertEqual(len(await self.mind.store.all("SELECT id FROM layers")), len(before_layers))

    async def test_no_cases_is_not_a_score(self):
        report = await anchor.anchor_run(self.mind, await get_persona(self.mind.store, self.pid))
        self.assertEqual(report["cases"], 0)
        self.assertIsNone(report["score"])
        self.assertEqual(await self.mind.store.all("SELECT id FROM anchor_runs"), [])


if __name__ == "__main__":
    unittest.main()
