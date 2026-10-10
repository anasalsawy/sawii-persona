"""C - the harmonist.

This is C's architectural-fine-tune role, run during idle hours. C reads the
harmony score across the adversarial corpus, identifies which axes are failing,
and proposes targeted edits to the configuration surface (Settings fields and
prompt strings). C watches the harmony score after each edit and keeps the
change only if it improves the score.

This is NOT a separate agent. It is C with three extra roles layered on top
of the observer role:

  1. HARMONY AMBASSADOR - the only lobe that owns the meta-view of how the
     three lobes are agreeing. Lives in C's standard observation too
     (the harmony_notes field) but this module is the runtime that lets C
     act on it.

  2. HUMANIZER - continuously proposes new features the system is missing.
     These are dual-lobe features (coordination between A+B, B+C, A+C).
     See humanize_proposals.py for the candidate-feature library.

  3. ARCHITECT - in this module, C writes code. It edits prompts, tweaks
     coefficients, proposes new dual-lobe features. It watches the harmony
     score and only accepts changes that improve it.

The runtime loop is:

    idle hours trigger
        -> C awakens in harmonist mode
        -> run arch_finetune baseline (or read last score)
        -> for N edits:
             ask C's LLM for a structured architect_proposal
             apply the proposal
             re-run the corpus
             score
             keep if better, revert if worse
        -> if converged, optionally lock
        -> write a harmonist log entry

C's LLM is whatever model the user assigned to lobe C in Settings.c. In
production that is typically the psych model; in dev it can be anything.

The module is configuration-aware: it takes the live Settings and the live
prompts module, never hard-coded paths or singletons, so it can run inside
a Mind or standalone.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any

# Allow importing arch_finetune from /workspace/sawii-persona.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from ..llm import LLM, extract_json
from .prompts import C_SYSTEM


# ---------------------------------------------------------------------------
# The harmonist prompt. Sent to C's LLM as a fresh chat call when C is in
# architect mode. C is asked for a single structured proposal at a time.
# ---------------------------------------------------------------------------

HARMONIST_PROMPT = """You are C, the observer of a companion mind, running in architect mode during idle hours.
You are not observing the user right now - you are observing the SYSTEM.

You will see:
  - The current configuration surface (Settings fields and prompt strings).
  - The latest harmony score across six axes (0..1 each).
  - Per-scenario scores from the last adversarial run.
  - The failing scenarios and their disharmony class.
  - A list of recently rejected proposals (do not re-propose these).

Your job: propose ONE edit to the configuration surface that would most improve
the harmony score. The edit can target a Settings field (recall_band,
live_checks_max, psyche_half_life_days, juice_floor, ...) or a prompt string
in brains/prompts.py (C_SYSTEM, B_SYSTEM, B_LANE_SYSTEM, LIVE_CHECK, ...).

Return JSON only:
{{
 "target": "settings.recall_band | prompts.C_SYSTEM | prompts.B_LANE_SYSTEM | ...",
 "new_value": <the new value - a number for Settings, a string for prompts>,
 "rationale": "what disharmony this addresses and why this change should help",
 "expected_axis_impact": "which of the six axes you expect to move and how"
}}

Rules:
- One proposal per call. The runtime will apply it, re-score, and tell you if it worked.
- Do not propose edits to weights (we do not edit weights here).
- Do not propose edits that have already been rejected in this session.
- Keep new_value compact. For prompt edits, propose a paragraph or rule addition,
  not a complete rewrite of the prompt.
- If the harmony score is already > 0.9 across all axes, return
  {{"target": "none", "rationale": "converged"}}."""


# ---------------------------------------------------------------------------
# The harmonist record. One per pass.
# ---------------------------------------------------------------------------

@dataclass
class HarmonistPass:
    started_at: float
    finished_at: float = 0.0
    baseline_score: float = 0.0
    final_score: float = 0.0
    proposals: list[dict] = field(default_factory=list)
    accepted: list[dict] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)
    converged: bool = False
    # End-state classification: "improved" | "converged" | "no_change" | "c_unresponsive".
    status: str = ""

    def summary(self) -> str:
        lines = [
            f"Harmonist pass: baseline={self.baseline_score:.3f} "
            f"final={self.final_score:.3f} "
            f"accepted={len(self.accepted)} rejected={len(self.rejected)} "
            f"converged={self.converged}",
        ]
        for p in self.accepted:
            lines.append(f"  + {p['target']}: {p['delta']:+.3f}  ({p['rationale'][:80]})")
        for p in self.rejected:
            lines.append(f"  - {p['target']}: {p['delta']:+.3f}  ({p['rationale'][:80]})")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# C's LLM-driven proposal. Asks C's LLM for one structured edit.
# ---------------------------------------------------------------------------

async def c_propose(llm: LLM, *, baseline: dict, recent_rejections: list[str]) -> dict:
    """Ask C's LLM for one architect-mode proposal.

    `baseline` is the most recent harmony score dict (axis -> score) plus
    per-scenario scores. Returns the parsed proposal dict, or
    {"target": "none", "rationale": "converged"} when C has nothing left,
    or {"target": "_error", "rationale": ...} when C's response is malformed.

    The two non-OK outcomes are deliberately distinguished so the caller can
    decide whether to declare convergence or surface the error. A silent
    "converged" caused by a malformed response (e.g. C returned the observer
    payload instead of the architect payload) used to look like a real
    convergence — now it is reported as an error and counted as a no-op.
    """
    user_msg = (
        "Baseline harmony score:\n" + json.dumps(baseline, indent=2) +
        "\n\nRecent rejected targets (do not re-propose):\n" +
        "\n".join(recent_rejections) +
        "\n\nPropose ONE edit."
    )
    res = await llm.chat([
        {"role": "system", "content": HARMONIST_PROMPT},
        {"role": "user", "content": user_msg},
    ], temperature=0.4, max_tokens=600, json_mode=True)
    raw = res.text or ""
    data = extract_json(raw)
    if not isinstance(data, dict):
        # The LLM returned something that is not parseable JSON. This is a
        # failure, not a convergence — log it.
        return {
            "target": "_error",
            "rationale": f"unparseable response: {raw[:200]!r}",
            "raw": raw[:400],
        }
    target = str(data.get("target", "none") or "none").strip()
    # Heuristic: the architect payload has a small set of fields
    # (target, new_value, rationale, expected_axis_impact). The observer
    # payload has many others (user_chord, notes, readings, ...). If the
    # response has observer-only fields and no architect fields at all, it
    # is almost certainly the observer payload leaking through, not a real
    # "converged" signal.
    observer_only = {"user_chord", "notes", "readings", "hypotheses",
                     "entities", "reinterpret", "brief_for_b", "expect"}
    has_observer_fields = bool(observer_only & set(data.keys()))
    has_architect_fields = any(k in data for k in
                               ("target", "new_value", "expected_axis_impact"))
    if has_observer_fields and not has_architect_fields:
        # C returned the observer payload, not the architect payload.
        return {
            "target": "_error",
            "rationale": "observer payload returned instead of architect payload",
            "raw": raw[:400],
        }
    if target in {"none", "converged"}:
        # C explicitly said converged. Real convergence.
        return {"target": "none", "rationale": "converged"}
    if target == "":
        # Empty target - treat as malformed, not converged.
        return {
            "target": "_error",
            "rationale": "empty target",
            "raw": raw[:400],
        }
    # Sanity check: a proposal must include a target AND either a new_value
    # OR a clear instruction. Missing new_value is suspicious — flag it.
    if "new_value" not in data:
        return {
            "target": "_error",
            "rationale": f"missing new_value: target={target}",
            "raw": raw[:400],
        }
    # Distinguish a real proposal from C returning the observer payload by
    # accident: the architect payload always has a target prefixed with
    # "settings." or "prompts.". If it doesn't, that's a malformed response.
    if not (target.startswith("settings.") or target.startswith("prompts.")):
        return {
            "target": "_error",
            "rationale": f"unrecognized target prefix: target={target}",
            "raw": raw[:400],
        }
    return {
        "target": target,
        "new_value": data.get("new_value"),
        "rationale": str(data.get("rationale", "") or "")[:600],
        "expected_axis_impact": str(data.get("expected_axis_impact", "") or "")[:300],
    }


# ---------------------------------------------------------------------------
# The harmonist runtime. Runs one architectural-fine-tune pass.
# ---------------------------------------------------------------------------

async def harmonist_pass(
    c_llm: LLM,
    *,
    settings,
    prompts_module,
    budget: int = 3,
    lock_key: bytes | None = None,
    log_path: str | None = None,
) -> HarmonistPass:
    """Run one harmonist pass.

    Returns a HarmonistPass record. If `lock_key` is provided and the pass
    improves the harmony score, the new configuration is signed and locked.

    This is a thin wrapper over the arch_finetune pipeline that uses C's LLM
    as the editor. The runtime lives here (not in arch_finetune/) so that the
    Mind can call it directly without going through the CLI.
    """
    from arch_finetune import editor, lock as lock_mod, snapshot as snap_mod
    from arch_finetune.runner import run_corpus
    from arch_finetune.scorer import score_run

    record = HarmonistPass(started_at=time.time())
    # Baseline.
    print("[harmonist] running baseline corpus")
    run = await run_corpus(settings=settings, config_id="harmonist-baseline")
    base_score = score_run(run)
    record.baseline_score = base_score.aggregate
    baseline_payload = {
        "aggregate": base_score.aggregate,
        **{axis: getattr(base_score, axis) for axis in
           ["internal_consistency", "lobe_agreement", "regulation_effectiveness",
            "memory_coherence", "affective_trajectory", "no_silent_cancellation"]},
        "per_scenario": base_score.per_scenario,
    }
    print(f"[harmonist] baseline={base_score.aggregate:.3f}")
    # Apply edits.
    rejected_targets: list[str] = []
    current_score = base_score.aggregate
    best_settings = settings
    best_prompts = prompts_module
    best_score = current_score
    for i in range(budget):
        proposal = await c_propose(c_llm, baseline=baseline_payload,
                                   recent_rejections=rejected_targets)
        if proposal["target"] == "none":
            record.converged = True
            print(f"[harmonist] converged after {i} edits")
            break
        if proposal["target"] == "_error":
            # Malformed response from C. Count as a no-op but do not stop —
            # C may give a good proposal on the next try. Surface in the log.
            # Do NOT add to rejected_targets: the next call is a retry, not
            # a re-propose of a known-bad target.
            err_record = {"i": i, **proposal, "status": "c_response_error",
                          "delta": 0.0, "new_aggregate": current_score}
            record.proposals.append(err_record)
            record.rejected.append(err_record)
            print(f"[harmonist] [{i}] C returned malformed response: "
                  f"{proposal.get('rationale', '')[:120]}")
            continue
        # Apply.
        snap_before = snap_mod.Snapshot.capture(settings, prompts_module,
                                                config_id=f"harmonist-before-{i}")
        if proposal["target"].startswith("settings."):
            field_name = proposal["target"][len("settings."):]
            if hasattr(settings, field_name):
                setattr(settings, field_name, proposal["new_value"])
            else:
                record.proposals.append({"i": i, **proposal, "status": "invalid_target"})
                rejected_targets.append(proposal["target"])
                continue
        elif proposal["target"].startswith("prompts."):
            prompt_name = proposal["target"][len("prompts."):]
            if hasattr(prompts_module, prompt_name):
                setattr(prompts_module, prompt_name, proposal["new_value"])
            else:
                record.proposals.append({"i": i, **proposal, "status": "invalid_target"})
                rejected_targets.append(proposal["target"])
                continue
        # Re-score.
        run2 = await run_corpus(settings=settings, config_id=f"harmonist-after-{i}")
        new_score = score_run(run2)
        delta = new_score.aggregate - current_score
        proposal_record = {"i": i, **proposal, "delta": delta, "new_aggregate": new_score.aggregate}
        record.proposals.append(proposal_record)
        if delta >= 0:
            record.accepted.append(proposal_record)
            if new_score.aggregate > best_score:
                best_score = new_score.aggregate
                # Capture the winning configuration.
                best_settings = settings
                best_prompts = prompts_module
            current_score = new_score.aggregate
            print(f"[harmonist] [{i}] accepted {proposal['target']}: {delta:+.3f} ({current_score:.3f})")
        else:
            # Revert.
            snap_before.apply(settings, prompts_module)
            record.rejected.append(proposal_record)
            rejected_targets.append(proposal["target"])
            print(f"[harmonist] [{i}] rejected {proposal['target']}: {delta:+.3f}")
    record.final_score = best_score
    record.finished_at = time.time()
    # If every proposal was an error (C kept returning the wrong shape),
    # report it as an error pass, not a converged pass.
    if (record.proposals and not record.accepted
            and all(p.get("target") == "_error" for p in record.proposals)):
        record.status = "c_unresponsive"
    elif record.converged:
        record.status = "converged"
    elif record.accepted:
        record.status = "improved"
    else:
        record.status = "no_change"
    # Optional lock.
    if lock_key is not None and best_score > base_score.aggregate:
        snap = snap_mod.Snapshot.capture(best_settings, best_prompts,
                                          config_id=f"harmonist-best-{int(time.time())}")
        lock_path = lock_mod.lock(snap, key=lock_key, harmony_score=best_score,
                                   locked_by="harmonist-v1",
                                   notes=f"pass improved {base_score.aggregate:.3f} -> {best_score:.3f}")
        print(f"[harmonist] locked at {lock_path}")
    if log_path:
        _write_log(log_path, record)
    return record


def _write_log(path: str, record: HarmonistPass) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    # Rotation: if the existing log is over 1 MB, rotate it to .1 (keeping
    # the last 3 files). Cheap to do once-per-write because most writes are
    # O(1) bytes — the rotation only fires after a long run.
    _rotate_if_needed(path, max_bytes=1_000_000, keep=3)
    # Full record: every field except raw prompts, to keep log lines short.
    with open(path, "a") as f:
        f.write(json.dumps({
            "event": "harmonist_pass",
            "started_at": record.started_at,
            "finished_at": record.finished_at,
            "wall_clock_ms": round((record.finished_at - record.started_at) * 1000, 1)
                              if record.finished_at else 0,
            "baseline_score": record.baseline_score,
            "final_score": record.final_score,
            "delta": round(record.final_score - record.baseline_score, 4),
            "status": record.status,
            "converged": record.converged,
            "n_proposals": len(record.proposals),
            "n_accepted": len(record.accepted),
            "n_rejected": len(record.rejected),
            "accepted": [
                {"target": p["target"], "delta": p.get("delta", 0.0),
                 "rationale": (p.get("rationale") or "")[:200]}
                for p in record.accepted
            ],
            "rejected": [
                {"target": p["target"], "delta": p.get("delta", 0.0),
                 "rationale": (p.get("rationale") or "")[:200],
                 "status": p.get("status", "rejected_score")}
                for p in record.rejected
            ],
        }) + "\n")


def _rotate_if_needed(path: str, *, max_bytes: int, keep: int) -> None:
    """If ``path`` exists and is over ``max_bytes``, shift it to ``.1``,
    ``.2`` ... up to ``.keep-1``. Old ``.keep`` is deleted.

    No-op if the file does not exist or is small enough.
    """
    if not os.path.exists(path):
        return
    try:
        size = os.path.getsize(path)
    except OSError:
        return
    if size < max_bytes:
        return
    # Shift from the end backwards: .keep -> delete, .(keep-1) -> .keep, etc.
    for i in range(keep, 0, -1):
        src = f"{path}.{i}" if i > 0 else path
        dst = f"{path}.{i + 1}"
        if os.path.exists(src):
            if i == keep:
                try:
                    os.remove(src)
                except OSError:
                    pass
            else:
                try:
                    os.replace(src, dst)
                except OSError:
                    pass
    # Move the current file to .1
    try:
        os.replace(path, f"{path}.1")
    except OSError:
        # If we can't rotate, truncate to half size so the next write fits.
        try:
            with open(path, "r") as f:
                lines = f.readlines()
            with open(path, "w") as f:
                f.writelines(lines[len(lines) // 2:])
        except OSError:
            pass


# ---------------------------------------------------------------------------
# The idle-hour trigger. The Mind's main loop calls this periodically; when
# the system has been idle for N minutes, it runs one harmonist pass.
# ---------------------------------------------------------------------------

@dataclass
class IdleTrigger:
    """Watches for idle hours and runs the harmonist when triggered.

    Wire into the Mind loop by calling `tick(now, last_activity_at)` after
    each turn. When `now - last_activity_at` exceeds `idle_minutes`, run one
    pass and update `last_pass_at`.
    """
    idle_minutes: float = 30.0
    cooldown_minutes: float = 60.0
    budget_per_pass: int = 3
    lock_key: bytes | None = None
    log_path: str | None = "./data/harmonist.log"
    last_pass_at: float = 0.0
    pass_count: int = 0

    async def tick(self, now: float, last_activity_at: float, *,
                   c_llm: LLM, settings, prompts_module) -> HarmonistPass | None:
        """Return a HarmonistPass if a pass ran this tick; else None."""
        idle_seconds = now - last_activity_at
        cooldown_seconds = now - self.last_pass_at
        if idle_seconds < self.idle_minutes * 60:
            return None
        if cooldown_seconds < self.cooldown_minutes * 60:
            return None
        return await self._run(c_llm, settings, prompts_module)

    async def _run(self, c_llm, settings, prompts_module) -> HarmonistPass:
        record = await harmonist_pass(
            c_llm, settings=settings, prompts_module=prompts_module,
            budget=self.budget_per_pass, lock_key=self.lock_key,
            log_path=self.log_path,
        )
        self.last_pass_at = time.time()
        self.pass_count += 1
        return record