"""Tests for the C-as-harmonist runtime and the dual-lobe feature library."""
import asyncio
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from arch_finetune import features as dual_features
from arch_finetune.editor import EditProposal


class DualLobeFeatureTests(unittest.TestCase):
    def test_library_has_features(self):
        self.assertGreaterEqual(len(dual_features.LIBRARY), 3)
        for f in dual_features.LIBRARY:
            self.assertGreater(len(f.lobes), 1, f"{f.name} must involve 2+ lobes")
            self.assertTrue(f.disharmony)
            self.assertTrue(f.sketch)

    def test_features_cover_disharmonies(self):
        # Each of the named examples from C_SYSTEM should be in the library.
        names = {f.name for f in dual_features.LIBRARY}
        for expected in ["anticipatory_chord", "tender_safeguard",
                         "harmony_bias", "repair_attempt",
                         "silence_aware_initiative"]:
            self.assertIn(expected, names, f"missing dual-lobe feature: {expected}")

    def test_features_target_real_settings_or_prompts(self):
        for f in dual_features.LIBRARY:
            self.assertTrue(
                f.settings_target or f.prompt_target,
                f"{f.name} must target either settings or prompts",
            )

    def test_describe_library_lists_features(self):
        text = dual_features.describe_library()
        for f in dual_features.LIBRARY:
            self.assertIn(f.name, text)


class CSystemExtensionTests(unittest.TestCase):
    """Verify the three new roles are documented in C_SYSTEM."""

    def test_c_system_has_harmony_role(self):
        from sawii_persona.brains import prompts
        self.assertIn("HARMONY AMBASSADOR", prompts.C_SYSTEM)

    def test_c_system_has_humanizer_role(self):
        from sawii_persona.brains import prompts
        self.assertIn("HUMANIZER", prompts.C_SYSTEM)

    def test_c_system_has_architect_role(self):
        from sawii_persona.brains import prompts
        self.assertIn("ARCHITECT", prompts.C_SYSTEM)

    def test_c_system_mentions_harmony_notes_field(self):
        from sawii_persona.brains import prompts
        self.assertIn("harmony_notes", prompts.C_SYSTEM)

    def test_c_system_mentions_humanize_proposal_field(self):
        from sawii_persona.brains import prompts
        self.assertIn("humanize_proposal", prompts.C_SYSTEM)

    def test_c_system_mentions_architect_proposal_field(self):
        from sawii_persona.brains import prompts
        self.assertIn("architect_proposal", prompts.C_SYSTEM)


class ObserverExtensionTests(unittest.TestCase):
    def test_observer_harvests_harmony_notes(self):
        from sawii_persona.brains.observer import _parse_architect_proposal
        # The observer return dict should now include harmony_notes,
        # humanize_proposal, architect_proposal. We verify by calling the
        # parser with realistic input.
        parsed = _parse_architect_proposal({
            "target": "settings.recall_band",
            "rationale": "tighten recall",
            "expected_impact": "memory_coherence up",
        })
        self.assertEqual(parsed["target"], "settings.recall_band")
        self.assertEqual(parsed["rationale"], "tighten recall")

    def test_observer_handles_empty_architect_proposal(self):
        from sawii_persona.brains.observer import _parse_architect_proposal
        self.assertEqual(_parse_architect_proposal(None), {})
        self.assertEqual(_parse_architect_proposal(""), {})

    def test_observer_handles_string_architect_proposal(self):
        from sawii_persona.brains.observer import _parse_architect_proposal
        parsed = _parse_architect_proposal("free text proposal")
        self.assertIn("raw", parsed)
        self.assertEqual(parsed["raw"], "free text proposal")


class HarmonisPassRecordTests(unittest.TestCase):
    def test_summary_includes_baseline_and_final(self):
        from sawii_persona.brains.harmonist import HarmonistPass
        record = HarmonistPass(started_at=0.0, baseline_score=0.5, final_score=0.7)
        text = record.summary()
        self.assertIn("baseline=0.500", text)
        self.assertIn("final=0.700", text)


class LLMEditorTests(unittest.TestCase):
    def test_llm_editor_prompt_lists_editable_surface(self):
        from arch_finetune import editor
        self.assertIn("recall_band", editor.LLM_EDITOR_PROMPT)
        self.assertIn("C_SYSTEM", editor.LLM_EDITOR_PROMPT)

    def test_heuristic_editor_still_works(self):
        # Backwards compat: v1 heuristic editor still exists.
        from arch_finetune import editor
        self.assertTrue(hasattr(editor, "heuristic_editor"))
        self.assertTrue(hasattr(editor, "HEURISTIC_EDIT_LIBRARY"))
        self.assertGreater(len(editor.HEURISTIC_EDIT_LIBRARY), 0)


class SettingsTests(unittest.TestCase):
    def test_harmonist_settings_exist(self):
        from sawii_persona.config import Settings
        s = Settings()
        self.assertEqual(s.harmonist_idle_minutes, 30.0)
        self.assertEqual(s.harmonist_cooldown_minutes, 60.0)
        self.assertEqual(s.harmonist_budget_per_pass, 3)
        self.assertFalse(s.harmonist_lock_on_converge)

    def test_harmonist_settings_overridable_by_env(self):
        os.environ["SAWII_HARMONIST_IDLE_MINUTES"] = "15"
        os.environ["SAWII_HARMONIST_BUDGET"] = "7"
        from sawii_persona.config import Settings
        s = Settings()
        self.assertEqual(s.harmonist_idle_minutes, 15.0)
        self.assertEqual(s.harmonist_budget_per_pass, 7)
        del os.environ["SAWII_HARMONIST_IDLE_MINUTES"]
        del os.environ["SAWII_HARMONIST_BUDGET"]


class CProposeTests(unittest.TestCase):
    """Test that c_propose returns the right shape."""

    def test_c_propose_handles_converged(self):
        from sawii_persona.brains.harmonist import c_propose
        from sawii_persona.llm import ChatResult, ScriptedLLM

        def respond(messages, opts):
            return json.dumps({"target": "none", "rationale": "converged"})

        llm = ScriptedLLM(respond, name="C-test")
        proposal = asyncio.run(c_propose(llm, baseline={"aggregate": 0.95}, recent_rejections=[]))
        self.assertEqual(proposal["target"], "none")
        self.assertEqual(proposal["rationale"], "converged")

    def test_c_propose_returns_structured_proposal(self):
        from sawii_persona.brains.harmonist import c_propose
        from sawii_persona.llm import ScriptedLLM

        def respond(messages, opts):
            return json.dumps({
                "target": "settings.recall_band",
                "new_value": 0.20,
                "rationale": "memory_coherence below 0.6",
                "expected_axis_impact": "memory_coherence up",
            })

        llm = ScriptedLLM(respond, name="C-test")
        proposal = asyncio.run(c_propose(llm, baseline={"aggregate": 0.7}, recent_rejections=[]))
        self.assertEqual(proposal["target"], "settings.recall_band")
        self.assertEqual(proposal["new_value"], 0.20)

    def test_c_propose_flags_observer_payload_as_malformed(self):
        """When C returns the observer payload (e.g. cannot switch roles),
        c_propose must NOT treat the missing target as a real convergence."""
        from sawii_persona.brains.harmonist import c_propose
        from sawii_persona.llm import ScriptedLLM

        observer_payload = {
            "user_chord": {"fear": 0.5, "tenderness": 0.5},
            "notes": [], "readings": [], "hypotheses": [], "entities": [],
            "reinterpret": [], "brief_for_b": "maya matters",
        }

        def respond(messages, opts):
            return json.dumps(observer_payload)

        llm = ScriptedLLM(respond, name="C-test")
        proposal = asyncio.run(c_propose(llm, baseline={"aggregate": 0.7}, recent_rejections=[]))
        self.assertEqual(proposal["target"], "_error")
        self.assertIn("observer payload", proposal["rationale"])

    def test_c_propose_flags_unparseable_response(self):
        from sawii_persona.brains.harmonist import c_propose
        from sawii_persona.llm import ScriptedLLM

        def respond(messages, opts):
            return "not json at all - just prose"

        llm = ScriptedLLM(respond, name="C-test")
        proposal = asyncio.run(c_propose(llm, baseline={"aggregate": 0.7}, recent_rejections=[]))
        self.assertEqual(proposal["target"], "_error")
        self.assertIn("unparseable", proposal["rationale"])

    def test_c_propose_flags_missing_new_value(self):
        from sawii_persona.brains.harmonist import c_propose
        from sawii_persona.llm import ScriptedLLM

        def respond(messages, opts):
            return json.dumps({"target": "settings.recall_band", "rationale": "fix it"})

        llm = ScriptedLLM(respond, name="C-test")
        proposal = asyncio.run(c_propose(llm, baseline={"aggregate": 0.7}, recent_rejections=[]))
        self.assertEqual(proposal["target"], "_error")
        self.assertIn("missing new_value", proposal["rationale"])

    def test_c_propose_flags_unrecognized_target_prefix(self):
        from sawii_persona.brains.harmonist import c_propose
        from sawii_persona.llm import ScriptedLLM

        def respond(messages, opts):
            return json.dumps({"target": "random.field", "new_value": 1})

        llm = ScriptedLLM(respond, name="C-test")
        proposal = asyncio.run(c_propose(llm, baseline={"aggregate": 0.7}, recent_rejections=[]))
        self.assertEqual(proposal["target"], "_error")
        self.assertIn("unrecognized target prefix", proposal["rationale"])

    def test_c_propose_accepts_prompts_target_prefix(self):
        from sawii_persona.brains.harmonist import c_propose
        from sawii_persona.llm import ScriptedLLM

        def respond(messages, opts):
            return json.dumps({
                "target": "prompts.A_SYSTEM",
                "new_value": "new prompt text",
                "rationale": "voice was off",
            })

        llm = ScriptedLLM(respond, name="C-test")
        proposal = asyncio.run(c_propose(llm, baseline={"aggregate": 0.7}, recent_rejections=[]))
        self.assertEqual(proposal["target"], "prompts.A_SYSTEM")
        self.assertEqual(proposal["new_value"], "new prompt text")


class HarmonisPassEndToEndTests(unittest.TestCase):
    """Run the full harmonist pass against the corpus using scripted lobes.

    This is a smoke test - it verifies that:
      1. harmonist_pass runs the corpus baseline
      2. Asks C for a proposal
      3. Applies the proposal
      4. Re-scores
      5. Accepts or reverts
      6. Writes a HarmonistPass record
    """

    def _make_scripted_lobes(self):
        """Build scripted A/B/C LLMs that look like the existing fakes.py ones.

        The C LLM is the interesting one: it must respond to BOTH the regular
        observer prompt (C_SYSTEM) AND the HARMONIST_PROMPT in architect mode.
        We dispatch on which prompt is in the system message.
        """
        from sawii_persona.brains import prompts as p_mod

        def respond_c(messages, opts):
            sys = messages[0]["content"] if messages and messages[0]["role"] == "system" else ""
            if "running in architect mode" in sys:
                # The harmonist prompt. Propose one edit.
                return json.dumps({
                    "target": "settings.recall_band",
                    "new_value": 0.25,
                    "rationale": "tighten recall to reduce false surfaces",
                    "expected_axis_impact": "memory_coherence up",
                })
            # Regular observer mode.
            return json.dumps({
                "user_chord": {"joy": 0.3, "sadness": 0.1},
                "notes": [],
                "readings": [],
                "hypotheses": [],
                "entities": [],
                "reinterpret": [],
                "brief_for_b": "",
                "expect": "user will continue calmly",
                "disagree_with_b": "",
                "harmony_notes": [],
                "humanize_proposal": "",
                "architect_proposal": {},
            })

        def respond_b(messages, opts):
            sys = messages[0]["content"] if messages and messages[0]["role"] == "system" else ""
            return json.dumps({
                "verdict": {"level": "GREEN", "rationale": "fine", "findings": []},
                "correction_required": "",
                "collateral": [],
                "loops": [],
                "conscience": "",
                "afterthought": "",
            })

        def respond_a(messages, opts):
            return "okay."

        from sawii_persona.llm import ScriptedLLM
        return {
            "a": ScriptedLLM(respond_a, name="A-test"),
            "b": ScriptedLLM(respond_b, name="B-test"),
            "c": ScriptedLLM(respond_c, name="C-test"),
        }

    def test_harmonist_pass_runs_end_to_end(self):
        """A full pass with budget=2: C proposes, applies, re-scores, reverts."""
        from sawii_persona.brains.harmonist import harmonist_pass
        from sawii_persona.brains import prompts as prompts_module
        from sawii_persona.config import Settings
        from sawii_persona.llm import ScriptedLLM

        settings = Settings()
        llms = self._make_scripted_lobes()
        record = asyncio.run(harmonist_pass(
            llms["c"], settings=settings, prompts_module=prompts_module,
            budget=2,
        ))
        # The record should have a baseline and a final score.
        self.assertGreater(record.baseline_score, 0.0)
        self.assertGreater(record.final_score, 0.0)
        # With this trivial scripted setup, all proposals will be applied,
        # but the harmony score won't move much because the scripted lobes
        # don't actually fix anything. We just verify the loop ran.
        self.assertGreaterEqual(len(record.proposals), 1)
        # summary() must not raise.
        text = record.summary()
        self.assertIn("Harmonist pass:", text)

    def test_idle_trigger_does_not_fire_when_recent(self):
        """The trigger respects cooldown."""
        import time as _time
        from sawii_persona.brains.harmonist import IdleTrigger
        from sawii_persona.brains import prompts as prompts_module
        from sawii_persona.config import Settings

        settings = Settings()
        llms = self._make_scripted_lobes()
        trigger = IdleTrigger(idle_minutes=1.0, cooldown_minutes=60.0, budget_per_pass=1)
        # Set last_pass_at to recent so cooldown blocks us.
        trigger.last_pass_at = _time.time()
        now = _time.time()
        # Even though we're idle, cooldown blocks us.
        result = asyncio.run(trigger.tick(
            now, last_activity_at=now - 600,
            c_llm=llms["c"], settings=settings, prompts_module=prompts_module,
        ))
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()