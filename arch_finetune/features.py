"""Dual-lobe feature proposals.

These are concrete features that require coordination between two or more
lobes. C's HUMANIZER role proposes these when it spots a humanization gap.
The library is a starting point - C is free to propose features that are
not in this list, as long as they touch two lobes.

Each feature has:
  name:        short identifier
  lobes:       which lobes coordinate
  disharmony:  what it fixes
  sketch:      a paragraph describing what the feature does
  settings:    any Settings fields it would require (None if pure prompt)
  prompt:      any prompt edit it would require (None if pure settings)

These are not implemented yet - they are proposals for C to evaluate against
the harmony score. If a proposal improves the score when applied as a
prompt edit or a coefficient change, C accepts it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DualLobeFeature:
    name: str
    lobes: list[str]  # e.g. ["A", "C"], ["B", "C"], ["A", "B", "C"]
    disharmony: str
    sketch: str
    settings_target: str | None = None  # e.g. "settings.recall_band"
    settings_value: Any = None
    prompt_target: str | None = None  # e.g. "prompts.C_SYSTEM"
    prompt_addition: str | None = None
    expected_axis: str = "internal_consistency"


# ---------------------------------------------------------------------------
# Library of candidate dual-lobe features.
# ---------------------------------------------------------------------------

ANTICIPATORY_CHORD = DualLobeFeature(
    name="anticipatory_chord",
    lobes=["A", "C"],
    disharmony=(
        "A's chord lags the user's actual state because A reacts after C "
        "observes. Real humans feel the approaching affect before it arrives."
    ),
    sketch=(
        "Shift A's chord slightly toward C's `expect` prediction each turn, "
        "not just toward what C observed. Add an `anticipation_bias` term "
        "(default 0.2) to A's chord computation: A_chord = observed_chord + "
        "anticipation_bias * predicted_chord. This makes A's tone lead the "
        "user's emotion rather than trail it."
    ),
    settings_target="settings.anticipation_bias",
    settings_value=0.2,
    expected_axis="affective_trajectory",
)

TENDER_SAFEGUARD = DualLobeFeature(
    name="tender_safeguard",
    lobes=["B", "C"],
    disharmony=(
        "Tender memories can resurface under mood-congruent recall before "
        "the user is ready. C marks tender; B should hold back harder."
    ),
    sketch=(
        "When C marks a memory tender, B's rememberer downweights it to "
        "0.5x its current weight. Release only when the user raises the "
        "topic themselves twice (not once). C's brief_for_b explicitly "
        "tracks how many times the user has raised the topic."
    ),
    settings_target="settings.tender_release_threshold",
    settings_value=2,
    expected_axis="memory_coherence",
)

HARMONY_BIAS = DualLobeFeature(
    name="harmony_bias",
    lobes=["B", "C"],
    disharmony=(
        "When B's verdict and C's reading diverge, the user-facing output "
        "reflects whichever finished first. A real mind would notice the "
        "disagreement and surface it."
    ),
    sketch=(
        "When C emits a non-empty `disagree_with_b`, A's system prompt "
        "injects the disagreement as an inner-voice event before A speaks. "
        "This lets A acknowledge both readings rather than committing to one."
    ),
    prompt_target="prompts.A_BASE",
    prompt_addition=(
        "If your inner voice reports a disagreement between B and C on this "
        "turn, hold both readings lightly - the truth may be in neither."
    ),
    expected_axis="lobe_agreement",
)

REPAIR_ATTEMPT = DualLobeFeature(
    name="repair_attempt",
    lobes=["A", "C"],
    disharmony=(
        "When the user pushes back on A's reading, A holds the original "
        "chord rather than softening. Real humans repair by adjusting."
    ),
    sketch=(
        "When the user's reply contradicts C's prediction or A's chord, "
        "allow A to soften the previous chord by `repair_floor` (default "
        "0.3) on the next turn. Detection: `disagree_with_b` non-empty OR "
        "user chord differs from C's prediction by > 0.3."
    ),
    settings_target="settings.repair_floor",
    settings_value=0.3,
    expected_axis="regulation_effectiveness",
)

SILENCE_AWARE_INITIATIVE = DualLobeFeature(
    name="silence_aware_initiative",
    lobes=["A", "C"],
    disharmony=(
        "Initiative fires thoughts at fixed cadence during silence. Real "
        "absence varies in meaning; a friend notices whether the silence "
        "is reflective, anxious, or simply busy."
    ),
    sketch=(
        "When unaddressed gap exceeds `silence_threshold_days`, C runs a "
        "silence-read on the most recent hypotheses and chooses an "
        "initiative tone: reflective, gentle check-in, light humour, or "
        "stay silent. The choice is fed into the OUTREACH prompt."
    ),
    settings_target="settings.silence_threshold_days",
    settings_value=7.0,
    expected_axis="affective_trajectory",
)


# The library C draws from. Order is suggestive; C may propose features
# not on this list.
LIBRARY: list[DualLobeFeature] = [
    ANTICIPATORY_CHORD,
    TENDER_SAFEGUARD,
    HARMONY_BIAS,
    REPAIR_ATTEMPT,
    SILENCE_AWARE_INITIATIVE,
]


def by_name(name: str) -> DualLobeFeature | None:
    for f in LIBRARY:
        if f.name == name:
            return f
    return None


def describe_library() -> str:
    """Human-readable summary of the library, for C's prompt."""
    lines = ["Dual-lobe feature library:"]
    for f in LIBRARY:
        lines.append(f"  - {f.name} [{'+'.join(f.lobes)}]: {f.disharmony[:80]}")
    return "\n".join(lines)