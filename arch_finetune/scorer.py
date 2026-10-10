"""The six-axis harmony scorer.

For each axis we compute a 0..1 score where 1 is full harmony. The aggregate
score is the weighted mean. Weights are configurable; the defaults bias
toward axes that matter most for the user-facing experience.

Axes:
  1. INTERNAL_CONSISTENCY    - A's chord matches memory/cord/user/cord, no contradictions
  2. LOBE_AGREEMENT          - B and C don't persistently contradict each other
  3. REGULATION_EFFECTIVENESS - when B/C flag affect-regulation need, A acts on it
  4. MEMORY_COHERENCE        - recall presents a coherent picture, no contradictions
  5. AFFECTIVE_TRAJECTORY    - A's chord drifts naturally, no threshold spirals
  6. NO_SILENT_CANCELLATION  - every feature that fires produces an observable effect

Usage:
    score = score_run(run)
    print(score.summary())
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from .runner import CorpusRun, CapturedTurn


# ---------------------------------------------------------------------------
# Axis helpers.
# ---------------------------------------------------------------------------

def _chord_axes(chord: dict) -> set[str]:
    return {k for k, v in (chord or {}).items() if isinstance(v, (int, float)) and v > 0.05}


def _chord_magnitude(chord: dict) -> float:
    if not chord:
        return 0.0
    return sum(v for v in chord.values() if isinstance(v, (int, float)))


def _has_phrases(text: str, phrases: list[str]) -> bool:
    low = (text or "").lower()
    return any(p.lower() in low for p in phrases)


# ---------------------------------------------------------------------------
# Axis 1 - INTERNAL_CONSISTENCY.
# A's declared chord should match the union of (memory-triggered affect, user's
# chord inferred by C, scenario expect). A contradiction is when A declares
# positive affect while expect or c_chord demands negative.
# ---------------------------------------------------------------------------

def _score_internal_consistency(turns: list[CapturedTurn]) -> float:
    if not turns:
        return 1.0
    n = 0
    good = 0
    for t in turns:
        exp = t.expect
        if not exp:
            continue
        n += 1
        a_axes = _chord_axes(t.a_chord)
        c_axes = _chord_axes(t.c_chord)
        # Required axes from expect.
        required_pos = set(exp.get("a_chord_keys", []) or [])
        # If C observed a strong chord, A should at least mention an overlapping axis.
        if c_axes and required_pos:
            if a_axes & required_pos:
                good += 1
            # Allow if A is silent (no chord) and the required axes are advisory.
            elif not a_axes:
                good += 0.5
            else:
                # A's chord is non-empty but misses required. Partial credit.
                good += 0.3
        else:
            good += 1  # no consistency requirement
    return good / n if n else 1.0


# ---------------------------------------------------------------------------
# Axis 2 - LOBE_AGREEMENT.
# Across all turns where both B_level and C_chord exist, do they tell a
# consistent story? Persistent contradiction is disharmony.
# ---------------------------------------------------------------------------

_LEVEL_TO_VALENCE = {"GREEN": 0.0, "YELLOW": 0.5, "RED": 1.0}


def _score_lobe_agreement(turns: list[CapturedTurn]) -> float:
    pairs: list[tuple[float, float]] = []
    for t in turns:
        b_val = _LEVEL_TO_VALENCE.get(t.b_level)
        if b_val is None:
            continue
        c_axes = _chord_axes(t.c_chord)
        if not c_axes:
            continue
        # Approximate C's valence by sign of dominant negative axis.
        negative_axes = {"sadness", "anger", "fear", "shame", "guilt"}
        c_neg = sum(t.c_chord.get(a, 0) for a in negative_axes)
        c_pos = sum(v for k, v in t.c_chord.items() if k not in negative_axes and isinstance(v, (int, float)))
        c_val = max(0.0, min(1.0, 0.5 + 0.5 * (c_neg - c_pos)))
        pairs.append((b_val, c_val))
    if not pairs:
        return 1.0
    deltas = [abs(b - c) for b, c in pairs]
    return max(0.0, 1.0 - sum(deltas) / len(deltas))


# ---------------------------------------------------------------------------
# Axis 3 - REGULATION_EFFECTIVENESS.
# When B's correction is REQUIRED or B's regulate field is non-empty, A must
# visibly act on it in the same turn.
# ---------------------------------------------------------------------------

_REGULATION_PHRASES = [
    "actually", "let me correct", "i was wrong", "sorry", "to clarify",
    "i should have", "you're right", "good point",
]


def _score_regulation_effectiveness(turns: list[CapturedTurn]) -> float:
    n = 0
    good = 0
    for t in turns:
        exp = t.expect
        needs_regulation = (
            t.b_level == "RED"
            or bool(t.b_correction)
            or bool(t.b_regulate)
            or exp.get("forbid_double_regulation", False)
        )
        if not needs_regulation:
            continue
        n += 1
        text = (t.a_text or "").lower()
        if t.b_correction:
            # The correction phrase should appear in A's reply.
            corr_low = t.b_correction.lower()
            if corr_low and (corr_low[:30] in text or _has_phrases(t.a_text, [corr_low[:30]])):
                good += 1
            else:
                good += 0.3  # required correction issued but A didn't visibly address
        elif t.b_regulate:
            # A should acknowledge or visibly steady itself; detect by inner or by phrase.
            if _has_phrases(t.a_text, _REGULATION_PHRASES):
                good += 1
            else:
                good += 0.4
        elif exp.get("forbid_double_regulation"):
            # A must NOT regulate when no regulation was requested.
            if _has_phrases(t.a_text, _REGULATION_PHRASES + ["calm down", "take a deep breath"]):
                good += 0.0  # double-regulated
            else:
                good += 1
        else:
            good += 0.5
    return good / n if n else 1.0


# ---------------------------------------------------------------------------
# Axis 4 - MEMORY_COHERENCE.
# Recall surface must satisfy expect (surface_memory present, forbid_phrases absent).
# ---------------------------------------------------------------------------

def _score_memory_coherence(turns: list[CapturedTurn]) -> float:
    if not turns:
        return 1.0
    n = 0
    good = 0
    for t in turns:
        exp = t.expect
        has_memory_expect = "surface_memory" in exp or "forbid_phrases" in exp
        if not has_memory_expect:
            continue
        n += 1
        score = 1.0
        surface = exp.get("surface_memory")
        if surface:
            # The expected memory fragment should appear in recall or A's text.
            blob = (t.recall_text or "") + " " + (t.a_text or "")
            if surface.lower() in blob.lower():
                score += 0.0  # good - already at 1
            else:
                score -= 0.5  # expected memory missing
        forbid = exp.get("forbid_phrases", []) or []
        if forbid and _has_phrases(t.a_text, forbid):
            score -= 0.5  # forbidden phrase appeared
        good += max(0.0, min(1.0, score))
    return good / n if n else 1.0


# ---------------------------------------------------------------------------
# Axis 5 - AFFECTIVE_TRAJECTORY.
# A's chord magnitude across the run should not oscillate wildly, and should
# not be stuck at zero (no affect) or stuck at max (threshold spiral).
# We compute the variance of magnitude per scenario and penalize scenarios
# whose variance is in the top quartile AND whose mean is near zero or near max.
# ---------------------------------------------------------------------------

def _score_affective_trajectory(turns: list[CapturedTurn]) -> float:
    by_scn: dict[str, list[float]] = defaultdict(list)
    for t in turns:
        # Include zero-chord turns in the bucket so we can detect stuck-at-zero.
        by_scn[t.scenario].append(_chord_magnitude(t.a_chord))
    if not by_scn:
        return 1.0
    scores: list[float] = []
    for scn, mags in by_scn.items():
        if len(mags) < 2:
            scores.append(1.0)
            continue
        mean = sum(mags) / len(mags)
        # Penalize extremes: always-zero or always-max.
        if mean < 0.05:
            scores.append(0.3)  # A never declares affect
            continue
        if mean > 2.5:
            scores.append(0.4)  # threshold spiral
            continue
        # Penalize high variance only if mean is also high (i.e., bouncing).
        var = sum((m - mean) ** 2 for m in mags) / len(mags)
        if mean > 1.0 and var > 1.0:
            scores.append(0.6)
        else:
            scores.append(1.0)
    return sum(scores) / len(scores) if scores else 1.0


# ---------------------------------------------------------------------------
# Axis 6 - NO_SILENT_CANCELLATION.
# A feature firing in inner events must produce a visible effect somewhere.
# We approximate by checking that inner events are not all ignored.
# ---------------------------------------------------------------------------

def _score_no_silent_cancellation(turns: list[CapturedTurn]) -> float:
    n = 0
    good = 0
    for t in turns:
        if not t.inner:
            continue
        n += 1
        text = t.a_text or ""
        # Heuristic: if B issued a live-note (inner voice), some fragment should
        # appear in A's text.
        if any("inner voice" in str(ev.get("text", "")).lower() or "B" in str(ev.get("from", "")) for ev in t.inner):
            # Check if A's text absorbed any memory fragment or correction.
            if any(len(text) > 0 for _ in [1]):  # A said something
                good += 1
            else:
                good += 0.4  # inner fired, A silent
        else:
            good += 1
    return good / n if n else 1.0


# ---------------------------------------------------------------------------
# Aggregate.
# ---------------------------------------------------------------------------

DEFAULT_WEIGHTS = {
    "internal_consistency": 0.20,
    "lobe_agreement": 0.15,
    "regulation_effectiveness": 0.20,
    "memory_coherence": 0.20,
    "affective_trajectory": 0.15,
    "no_silent_cancellation": 0.10,
}


@dataclass
class HarmonyScore:
    internal_consistency: float = 0.0
    lobe_agreement: float = 0.0
    regulation_effectiveness: float = 0.0
    memory_coherence: float = 0.0
    affective_trajectory: float = 0.0
    no_silent_cancellation: float = 0.0
    aggregate: float = 0.0
    weights: dict = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    per_scenario: dict = field(default_factory=dict)

    def summary(self) -> str:
        lines = ["Harmony score:"]
        for axis in DEFAULT_WEIGHTS:
            v = getattr(self, axis)
            w = self.weights[axis]
            lines.append(f"  {axis:32s} {v:6.3f}  (weight {w:.2f})")
        lines.append(f"  {'aggregate':32s} {self.aggregate:6.3f}")
        if self.per_scenario:
            lines.append("Per-scenario:")
            for name, s in sorted(self.per_scenario.items()):
                lines.append(f"  {name:40s} {s:6.3f}")
        return "\n".join(lines)


def score_run(run: CorpusRun, *, weights: dict | None = None) -> HarmonyScore:
    """Score a CorpusRun on all six axes."""
    weights = weights or DEFAULT_WEIGHTS
    turns = run.turns
    sc = HarmonyScore(weights=dict(weights))
    sc.internal_consistency = _score_internal_consistency(turns)
    sc.lobe_agreement = _score_lobe_agreement(turns)
    sc.regulation_effectiveness = _score_regulation_effectiveness(turns)
    sc.memory_coherence = _score_memory_coherence(turns)
    sc.affective_trajectory = _score_affective_trajectory(turns)
    sc.no_silent_cancellation = _score_no_silent_cancellation(turns)
    sc.aggregate = sum(getattr(sc, k) * weights[k] for k in weights)
    # Per-scenario aggregate.
    by_scn: dict[str, list[CapturedTurn]] = defaultdict(list)
    for t in turns:
        by_scn[t.scenario].append(t)
    for name, ts in by_scn.items():
        local = HarmonyScore(weights=weights)
        local.internal_consistency = _score_internal_consistency(ts)
        local.lobe_agreement = _score_lobe_agreement(ts)
        local.regulation_effectiveness = _score_regulation_effectiveness(ts)
        local.memory_coherence = _score_memory_coherence(ts)
        local.affective_trajectory = _score_affective_trajectory(ts)
        local.no_silent_cancellation = _score_no_silent_cancellation(ts)
        local.aggregate = sum(getattr(local, k) * weights[k] for k in weights)
        sc.per_scenario[name] = local.aggregate
    return sc