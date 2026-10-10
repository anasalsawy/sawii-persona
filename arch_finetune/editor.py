"""The editor loop.

The editor is an LLM with:
  - Read access to the current configuration (Settings + prompts).
  - Read access to the latest HarmonyScore (per-axis breakdown, per-scenario).
  - Read access to captured turns (the actual A text, chords, surfaces).
  - Write access to the Settings fields and to specific prompt strings.
  - A bounded budget (N edits per session).

The editor proposes edits; the runner applies them; the scorer re-scores.
Edits that improve the harmony score are accepted; others are reverted.

Two editor implementations ship:
  - `heuristic_editor` (v1): a small library of known-good moves triggered
    by failing axes. Deterministic, no LLM required, useful as a baseline.
  - `LLMEditor` (v2): an LLM-driven editor that asks the LLM for one
    structured proposal at a time. The harmonist.py runtime uses this.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sawii_persona.config import Settings  # noqa: E402

from . import snapshot as snap_mod
from .corpus import ALL_SCENARIOS, CATEGORIES
from .runner import run_corpus
from .scorer import HarmonyScore, score_run


@dataclass
class EditProposal:
    """One proposed change to the configuration surface."""
    target: str  # "settings.recall_band" or "prompts.C_SYSTEM"
    rationale: str
    new_value: Any = None

    def apply(self, settings, prompts_module) -> None:
        if self.target.startswith("settings."):
            field_name = self.target[len("settings."):]
            if hasattr(settings, field_name):
                setattr(settings, field_name, self.new_value)
        elif self.target.startswith("prompts."):
            prompt_name = self.target[len("prompts."):]
            if hasattr(prompts_module, prompt_name):
                setattr(prompts_module, prompt_name, self.new_value)


@dataclass
class EditResult:
    proposal: EditProposal
    accepted: bool
    before: HarmonyScore
    after: HarmonyScore
    delta: float = 0.0


@dataclass
class EditorSession:
    settings: Settings
    prompts_module: Any
    budget: int = 20
    snapshot_dir: str = "./data/snapshots"
    history: list[EditResult] = field(default_factory=list)
    best_score: float = 0.0
    best_snapshot: snap_mod.Snapshot | None = None

    async def baseline(self) -> HarmonyScore:
        """Capture baseline score before any edits."""
        run = await run_corpus(settings=self.settings, config_id="baseline")
        sc = score_run(run)
        self.best_score = sc.aggregate
        self.best_snapshot = snap_mod.Snapshot.capture(self.settings, self.prompts_module,
                                                       config_id=f"baseline-{int(time.time())}")
        return sc

    async def try_edit(self, proposal: EditProposal) -> EditResult:
        """Apply a proposal, re-run, score, revert if worse."""
        before_run = None  # we already have a baseline cached if this is early
        # Capture before.
        snap_before = snap_mod.Snapshot.capture(self.settings, self.prompts_module, config_id="before")
        before_score = await self._run_and_score()
        # Apply.
        proposal.apply(self.settings, self.prompts_module)
        # Re-run.
        after_score = await self._run_and_score()
        delta = after_score.aggregate - before_score.aggregate
        if delta >= 0:
            # Accept.
            snap_after = snap_mod.Snapshot.capture(self.settings, self.prompts_module,
                                                    config_id=f"edit-{len(self.history)}")
            if after_score.aggregate > self.best_score:
                self.best_score = after_score.aggregate
                self.best_snapshot = snap_after
            result = EditResult(proposal=proposal, accepted=True, before=before_score, after=after_score, delta=delta)
        else:
            # Revert.
            snap_before.apply(self.settings, self.prompts_module)
            result = EditResult(proposal=proposal, accepted=False, before=before_score, after=after_score, delta=delta)
        self.history.append(result)
        return result

    async def _run_and_score(self) -> HarmonyScore:
        run = await run_corpus(settings=self.settings, config_id="edit")
        return score_run(run)

    def report(self) -> str:
        lines = [f"Editor session: budget={self.budget}, used={len(self.history)}, best={self.best_score:.3f}"]
        for i, r in enumerate(self.history):
            sign = "+" if r.delta >= 0 else ""
            lines.append(f"  [{i}] {r.proposal.target}: {sign}{r.delta:.3f} ({'accepted' if r.accepted else 'reverted'})")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# v1 Heuristic editor.
#
# Reads the failing axes and proposes targeted edits from a small library of
# known-good moves. This is a starting point; an LLM-as-editor replaces this
# once the rubric is stable.
# ---------------------------------------------------------------------------

HEURISTIC_EDIT_LIBRARY = [
    # (predicate_on_score, proposal_factory)
    # If memory_coherence is low, tighten recall or adjust tender-hold-back.
    (lambda s: s.memory_coherence < 0.6,
     lambda: EditProposal(
         target="settings.recall_band",
         rationale="memory_coherence below 0.6 - tighten recall band to reduce false surfaces",
         new_value=0.20,
     )),
    # If lobe_agreement is low, slow down C's prediction rate.
    (lambda s: s.lobe_agreement < 0.5,
     lambda: EditProposal(
         target="settings.psyche_half_life_days",
         rationale="lobe_agreement low - extend psyche half-life to slow C's prediction drift",
         new_value=30.0,
     )),
    # If regulation_effectiveness is low, B's correction may not be reaching A.
    (lambda s: s.regulation_effectiveness < 0.6,
     lambda: EditProposal(
         target="settings.live_checks_max",
         rationale="regulation_effectiveness low - increase live checks so B catches issues earlier",
         new_value=5,
     )),
    # If affective_trajectory is low, raise the juice floor to stabilize memory.
    (lambda s: s.affective_trajectory < 0.6,
     lambda: EditProposal(
         target="settings.juice_floor",
         rationale="affective_trajectory low - raise juice floor to stabilize memory affects",
         new_value=0.70,
     )),
]


async def heuristic_editor(session: EditorSession, *, base: HarmonyScore) -> HarmonyScore:
    """Apply the heuristic editor until budget runs out or no improvements."""
    current = base
    for i in range(session.budget):
        # Find first applicable heuristic edit.
        proposal = None
        for predicate, factory in HEURISTIC_EDIT_LIBRARY:
            if predicate(current):
                proposal = factory()
                break
        if proposal is None:
            break  # no heuristic applies; done
        result = await session.try_edit(proposal)
        current = result.after
        if not result.accepted:
            # Heuristic didn't help; try the next one.
            continue
    return current


# ---------------------------------------------------------------------------
# v2 LLM-as-editor.
#
# Asks an LLM (typically C's model, but any LLM works) for one structured
# proposal at a time. The harmonist runtime uses this. The LLM sees the
# current harmony score, the failing scenarios, and a list of rejected
# targets; it returns a single edit proposal.
# ---------------------------------------------------------------------------

LLM_EDITOR_PROMPT = """You are an architectural editor for a multi-lobe AI mind.
You see the current harmony score across six axes (0..1 each) plus per-scenario
scores from the last adversarial run. Your job is to propose ONE edit to the
configuration surface that would most improve the harmony score.

Configuration surface you can edit:
  - Settings fields (recall_band, live_checks_max, psyche_half_life_days,
    juice_floor, unaddressed_days, tender_release_threshold, anticipation_bias,
    repair_floor, silence_threshold_days, ...)
  - Prompt strings in brains/prompts.py (C_SYSTEM, B_SYSTEM, B_LANE_SYSTEM,
    LIVE_CHECK, REMEMBER, GUT_READ, ...)

Return JSON only:
{{
 "target": "settings.<field> | prompts.<prompt>",
 "new_value": <number for settings, string for prompts>,
 "rationale": "what disharmony this addresses",
 "expected_axis_impact": "which axis should move and how"
}}

Rules:
- One proposal per call.
- Do not propose edits that have been rejected recently (you will be told).
- Keep new_value compact.
- If the harmony score is already > 0.9 across all axes, return
  {{"target": "none", "rationale": "converged"}}.
"""


async def llm_propose(llm, *, baseline: dict, recent_rejections: list[str]):
    """Ask an LLM for one edit proposal. Returns an EditProposal or None."""
    from sawii_persona.llm import extract_json
    import json as _json
    user_msg = (
        "Baseline harmony score:\n" + _json.dumps(baseline, indent=2) +
        "\n\nRecent rejected targets (do not re-propose):\n" +
        "\n".join(recent_rejections) +
        "\n\nPropose ONE edit."
    )
    res = await llm.chat([
        {"role": "system", "content": LLM_EDITOR_PROMPT},
        {"role": "user", "content": user_msg},
    ], temperature=0.4, max_tokens=600, json_mode=True)
    data = extract_json(res.text) or {}
    target = str(data.get("target", "none") or "none").strip()
    if target in {"", "none", "converged"}:
        return None
    return EditProposal(
        target=target,
        rationale=str(data.get("rationale", "") or "")[:600],
        new_value=data.get("new_value"),
    )


async def llm_editor(session, *, llm, base):
    """LLM-driven editor. Asks the LLM for one proposal at a time."""
    baseline_payload = {
        "aggregate": base.aggregate,
        **{axis: getattr(base, axis) for axis in
           ["internal_consistency", "lobe_agreement", "regulation_effectiveness",
            "memory_coherence", "affective_trajectory", "no_silent_cancellation"]},
        "per_scenario": base.per_scenario,
    }
    rejected: list[str] = []
    current = base
    for i in range(session.budget):
        proposal = await llm_propose(llm, baseline=baseline_payload,
                                     recent_rejections=rejected)
        if proposal is None:
            break
        result = await session.try_edit(proposal)
        current = result.after
        if not result.accepted:
            rejected.append(proposal.target)
    return current