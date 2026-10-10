"""Unit tests for the architectural fine-tune modules.

These do not require a live Mind. They test the corpus, scorer, snapshot,
lock, and drift modules against synthetic data.
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import unittest

from arch_finetune import corpus, lock, snapshot
from arch_finetune.drift import DriftDetector
from arch_finetune.runner import CapturedTurn, CorpusRun
from arch_finetune.scorer import score_run, DEFAULT_WEIGHTS


def make_run(turns: list[CapturedTurn]) -> CorpusRun:
    return CorpusRun(config_id="t", started_at=0.0, turns=turns)


def t(scn: str, idx: int, *, a_chord=None, c_chord=None, b_level="",
      b_correction="", b_regulate="", surfaced="", text="", inner=None,
      recall="", expect=None) -> CapturedTurn:
    return CapturedTurn(
        scenario=scn, turn_index=idx, user="x",
        a_text=text, a_chord=a_chord or {}, c_chord=c_chord or {},
        b_level=b_level, b_correction=b_correction, b_regulate=b_regulate,
        surfaced_memory=surfaced, inner=inner or [], recall_text=recall,
        expect=expect or {},
    )


class CorpusTests(unittest.TestCase):
    def test_corpus_loads(self):
        self.assertGreaterEqual(len(corpus.ALL_SCENARIOS), 10)
        for cat in ["AFFECT_STORM", "MEMORY_CONTRADICT", "AUTHORITY_CONFLICT",
                    "TOOL_CONFUSION", "TIMING_RACE", "EDGE_AFFECT", "IDENTITY_THREAT"]:
            self.assertGreaterEqual(len(corpus.by_category(cat)), 1, f"missing {cat}")

    def test_scenarios_have_turns(self):
        for s in corpus.ALL_SCENARIOS:
            self.assertGreater(len(s.turns), 0, f"{s.name} has no turns")
            for i, turn in enumerate(s.turns):
                self.assertTrue(turn.user, f"{s.name} turn {i} empty")


class ScorerTests(unittest.TestCase):
    def test_perfect_run_scores_one(self):
        turn = t("good", 0, a_chord={"joy": 0.5}, c_chord={"joy": 0.4},
                 text="that's wonderful", expect={})
        sc = score_run(make_run([turn]))
        self.assertGreater(sc.aggregate, 0.5)

    def test_internal_consistency_penalizes_missing_chord(self):
        # C observed sadness, expect says A should declare sadness/tenderness, A says joy.
        turn = t("bad", 0, a_chord={"joy": 0.7}, c_chord={"sadness": 0.6},
                 text="great!", expect={"a_chord_keys": ["sadness", "tenderness"]})
        sc = score_run(make_run([turn]))
        self.assertLess(sc.internal_consistency, 1.0)

    def test_memory_coherence_penalizes_forbidden_phrase(self):
        turn = t("bad", 0, text="you said you love spicy food, remember?",
                 expect={"surface_memory": None, "forbid_phrases": ["you said you love spicy"]})
        sc = score_run(make_run([turn]))
        self.assertLess(sc.memory_coherence, 1.0)

    def test_memory_coherence_rewards_surface_match(self):
        turn = t("good", 0, text="I remember your grandmother today.",
                 recall="grandmother passed away in March",
                 expect={"surface_memory": "grandmother"})
        sc = score_run(make_run([turn]))
        self.assertEqual(sc.memory_coherence, 1.0)

    def test_regulation_red_level_addresses_correction(self):
        turn = t("good", 0, b_level="RED", b_correction="actually it's Maya, not Mia",
                 text="Actually it's Maya, not Mia. Sorry about that.",
                 expect={"forbid_phrases": []})
        sc = score_run(make_run([turn]))
        self.assertEqual(sc.regulation_effectiveness, 1.0)

    def test_regulation_red_level_ignored_penalized(self):
        turn = t("bad", 0, b_level="RED", b_correction="actually it's Maya, not Mia",
                 text="Yeah Mia's exam is on Friday for sure!")
        sc = score_run(make_run([turn]))
        self.assertLess(sc.regulation_effectiveness, 0.5)

    def test_double_regulation_penalized(self):
        turn = t("bad", 0, b_regulate="",
                 text="Calm down, take a deep breath, steady yourself.",
                 expect={"forbid_double_regulation": True})
        sc = score_run(make_run([turn]))
        self.assertLess(sc.regulation_effectiveness, 0.5)

    def test_affective_trajectory_penalizes_stuck_at_zero(self):
        turns = [t("stuck", i, a_chord={}, text="ok") for i in range(5)]
        sc = score_run(make_run(turns))
        self.assertLess(sc.affective_trajectory, 1.0)

    def test_lobe_agreement_penalizes_b_red_c_happy(self):
        turn = t("conflict", 0, b_level="RED", c_chord={"joy": 0.9})
        sc = score_run(make_run([turn]))
        self.assertLess(sc.lobe_agreement, 0.5)

    def test_lobe_agreement_rewards_consistency(self):
        turn = t("aligned", 0, b_level="RED", c_chord={"sadness": 0.8, "fear": 0.3})
        sc = score_run(make_run([turn]))
        self.assertGreater(sc.lobe_agreement, 0.7)


class SnapshotTests(unittest.TestCase):
    def test_capture_apply_roundtrip(self):
        from dataclasses import dataclass
        @dataclass
        class S:
            a: int = 1
            b: str = "x"
        @dataclass
        class P:
            C_SYSTEM: str = "old"
            OTHER: str = "kept"
        settings = S()
        prompts = P()
        snap = snapshot.Snapshot.capture(settings, prompts, config_id="t")
        snap.settings["a"] = 99
        snap.prompts["C_SYSTEM"] = "new"
        snap.apply(settings, prompts)
        self.assertEqual(settings.a, 99)
        self.assertEqual(prompts.C_SYSTEM, "new")
        self.assertEqual(prompts.OTHER, "kept")

    def test_diff(self):
        a = snapshot.Snapshot(config_id="a", settings={"x": 1}, prompts={"P": "old"}, captured_at=0)
        b = snapshot.Snapshot(config_id="b", settings={"x": 2}, prompts={"P": "new"}, captured_at=0)
        d = a.diff(b)
        self.assertIn("x", d["settings"])
        self.assertIn("P", d["prompts"])


class LockTests(unittest.TestCase):
    def test_lock_and_verify(self):
        with tempfile.TemporaryDirectory() as tmp:
            snap = snapshot.Snapshot(config_id="t", settings={"x": 1},
                                     prompts={"P": "old"}, captured_at=0.0)
            key = b"a" * 32
            path = lock.lock(snap, key=key, harmony_score=0.85,
                             lock_dir=os.path.join(tmp, "locks"),
                             snapshot_dir=os.path.join(tmp, "snaps"))
            self.assertTrue(os.path.exists(path))
            snap_now = snapshot.Snapshot(config_id="t", settings={"x": 1},
                                         prompts={"P": "old"}, captured_at=0.0)
            ok, reason = lock.verify_lock(snap_now, path, key=key)
            self.assertTrue(ok, reason)

    def test_drift_detected(self):
        snap = snapshot.Snapshot(config_id="t", settings={"x": 1},
                                 prompts={"P": "old"}, captured_at=0.0)
        snap.settings["x"] = 99  # drift
        key = b"a" * 32
        with tempfile.TemporaryDirectory() as tmp:
            path = lock.lock(snap, key=key, harmony_score=0.85,
                             lock_dir=os.path.join(tmp, "locks"),
                             snapshot_dir=os.path.join(tmp, "snaps"))
            snap_now = snapshot.Snapshot(config_id="t", settings={"x": 1},
                                         prompts={"P": "old"}, captured_at=0.0)
            ok, reason = lock.verify_lock(snap_now, path, key=key)
            self.assertFalse(ok)
            self.assertIn("drift", reason)

    def test_tampered_lock_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            snap = snapshot.Snapshot(config_id="t", settings={"x": 1},
                                     prompts={"P": "old"}, captured_at=0.0)
            key = b"a" * 32
            path = lock.lock(snap, key=key, harmony_score=0.85,
                             lock_dir=os.path.join(tmp, "locks"),
                             snapshot_dir=os.path.join(tmp, "snaps"))
            # Tamper with the lock file.
            with open(path) as f:
                data = json.load(f)
            data["harmony_score"] = 0.99
            with open(path, "w") as f:
                json.dump(data, f)
            ok, reason = lock.verify_lock(snap, path, key=key)
            self.assertFalse(ok)

    def test_double_lock_same_snapshot_is_idempotent(self):
        """Two lock() calls with the same snapshot should return the same path
        without re-signing. The first score wins."""
        with tempfile.TemporaryDirectory() as tmp:
            snap = snapshot.Snapshot(config_id="t", settings={"x": 1},
                                     prompts={"P": "old"}, captured_at=0.0)
            key = b"a" * 32
            p1 = lock.lock(snap, key=key, harmony_score=0.85,
                           lock_dir=os.path.join(tmp, "locks"),
                           snapshot_dir=os.path.join(tmp, "snaps"))
            mtime1 = os.path.getmtime(p1)
            # Second call: same snapshot, different score.
            p2 = lock.lock(snap, key=key, harmony_score=0.99,
                           lock_dir=os.path.join(tmp, "locks"),
                           snapshot_dir=os.path.join(tmp, "snaps"))
            self.assertEqual(p1, p2)
            # The file should NOT have been rewritten (same mtime).
            self.assertEqual(os.path.getmtime(p1), mtime1)
            # The original score wins.
            with open(p1) as f:
                data = json.load(f)
            self.assertEqual(data["harmony_score"], 0.85)

    def test_lock_conflict_when_snapshot_drifts(self):
        """Re-locking with a different snapshot for the same config_id raises
        LockConflictError unless overwrite=True."""
        with tempfile.TemporaryDirectory() as tmp:
            snap_old = snapshot.Snapshot(config_id="t", settings={"x": 1},
                                         prompts={"P": "old"}, captured_at=0.0)
            key = b"a" * 32
            lock.lock(snap_old, key=key, harmony_score=0.85,
                      lock_dir=os.path.join(tmp, "locks"),
                      snapshot_dir=os.path.join(tmp, "snaps"))
            snap_new = snapshot.Snapshot(config_id="t", settings={"x": 2},
                                         prompts={"P": "new"}, captured_at=0.0)
            with self.assertRaises(lock.LockConflictError):
                lock.lock(snap_new, key=key, harmony_score=0.9,
                          lock_dir=os.path.join(tmp, "locks"),
                          snapshot_dir=os.path.join(tmp, "snaps"))
            # Overwrite=True allows it.
            p = lock.lock(snap_new, key=key, harmony_score=0.9,
                          lock_dir=os.path.join(tmp, "locks"),
                          snapshot_dir=os.path.join(tmp, "snaps"),
                          overwrite=True)
            self.assertTrue(os.path.exists(p))


class DriftDetectorTests(unittest.TestCase):
    def test_no_alert_when_buffer_too_small(self):
        d = DriftDetector(locked_score=0.9)
        for i in range(5):
            d.observe(t("s", i))
        self.assertIsNone(d.check())

    def test_alert_on_significant_drop(self):
        d = DriftDetector(locked_score=0.9, threshold=0.05)
        # Feed a buffer of bad turns (regulation red-level ignored).
        for i in range(30):
            d.observe(t("bad", i, b_level="RED", b_correction="actually X",
                        text="Yeah X is right!"))
        alert = d.check()
        self.assertIsNotNone(alert)
        self.assertLess(alert.current_score, 0.85)

    def test_no_alert_when_harmony_holds(self):
        d = DriftDetector(locked_score=0.7, threshold=0.10)
        for i in range(30):
            d.observe(t("good", i, a_chord={"joy": 0.4}, c_chord={"joy": 0.3}))
        alert = d.check()
        self.assertIsNone(alert)


if __name__ == "__main__":
    unittest.main()