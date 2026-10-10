"""Live-runner: run the corpus (and optionally a harmonist pass) against the
fake_provider HTTP server. Real httpx, real SSE streaming, real network latency
- just scripted brains behind the wire.

Usage:
    PYTHONPATH=src python -m arch_finetune.live_runner [--port 8801] [--budget 3]

The provider URL is hard-coded to http://127.0.0.1:8801. If you don't have the
fake provider running, start it first:
    PYTHONPATH=src python tests/fake_provider.py 8801 &
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
import time
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + "/src")

from sawii_persona.config import Settings, ModelEndpoint
from sawii_persona.embedder import Embedder
from sawii_persona.identity import create_persona
from sawii_persona.llm import OpenAICompatLLM
from sawii_persona.mind import Mind
from sawii_persona.store import Store


PROVIDER_URL = os.environ.get("FAKE_PROVIDER_URL", "http://127.0.0.1:8801/v1")


@dataclass
class LobeTimings:
    """Per-lobe wall-clock ms across the run."""
    a_ms: float = 0.0
    b_ms: float = 0.0
    c_ms: float = 0.0
    consultant_ms: float = 0.0
    n_calls: dict = field(default_factory=dict)


@dataclass
class ScenarioTiming:
    scenario: str
    turns: int
    wall_clock_ms: float
    per_turn_ms: list[float]
    errors: list[str] = field(default_factory=list)


def build_endpoint(model: str = "model-a", timeout: float = 60.0) -> ModelEndpoint:
    return ModelEndpoint(base_url=PROVIDER_URL, api_key="not-needed-for-fake",
                         model=model, timeout=timeout)


async def run_live_scenario(mind: Mind, pid: int, scenario, timings: LobeTimings) -> ScenarioTiming:
    """Run a scenario against the live Mind, measuring per-turn wall-clock."""
    per_turn_ms = []
    errors = []
    if scenario.setup is not None:
        try:
            await scenario.setup(mind, pid)
        except Exception as exc:
            errors.append(f"setup: {type(exc).__name__}: {exc}")
    for i, turn in enumerate(scenario.turns):
        t0 = time.monotonic()
        try:
            async for _ev in mind.respond(pid, turn.user, channel=turn.channel, surface=turn.surface):
                pass
        except Exception as exc:
            errors.append(f"turn {i}: {type(exc).__name__}: {exc}")
        per_turn_ms.append((time.monotonic() - t0) * 1000)
    return ScenarioTiming(scenario=scenario.name, turns=len(scenario.turns),
                          wall_clock_ms=sum(per_turn_ms), per_turn_ms=per_turn_ms,
                          errors=errors)


def summarize(scenario_timings: list[ScenarioTiming], started: float) -> dict:
    finished = time.time()
    total_wall_ms = (finished - started) * 1000
    total_per_turn = sum(t.wall_clock_ms for t in scenario_timings)
    return {
        "started_at": started,
        "finished_at": finished,
        "total_wall_ms": total_wall_ms,
        "total_per_turn_sum_ms": total_per_turn,
        "n_scenarios": len(scenario_timings),
        "scenarios": [
            {
                "name": t.scenario,
                "turns": t.turns,
                "wall_clock_ms": round(t.wall_clock_ms, 1),
                "avg_per_turn_ms": round(t.wall_clock_ms / max(t.turns, 1), 1),
                "errors": t.errors,
            }
            for t in scenario_timings
        ],
        "slowest_scenarios": sorted(
            [{"name": t.scenario, "ms": round(t.wall_clock_ms, 1)} for t in scenario_timings],
            key=lambda x: x["ms"], reverse=True,
        )[:5],
    }


async def run_live_corpus(log_path: str, *, port: int = 8801, scenarios: list | None = None,
                          budget: int | None = None) -> dict:
    """Run the full corpus against the live fake provider.

    Writes one JSON line per scenario to log_path, plus a summary line.
    """
    from .corpus import ALL_SCENARIOS
    scens = scenarios or ALL_SCENARIOS
    settings = Settings()
    settings.a = build_endpoint("model-a")
    settings.b = build_endpoint("model-b")
    settings.c = build_endpoint("model-c")
    # Build our own LLM instances so we can read per-lobe call_stats after.
    a_llm = OpenAICompatLLM(settings.a, name="A-live")
    b_llm = OpenAICompatLLM(settings.b, name="B-live")
    c_llm = OpenAICompatLLM(settings.c, name="C-live")
    a_llm.reset_stats(); b_llm.reset_stats(); c_llm.reset_stats()
    timings = LobeTimings()
    started = time.time()
    scenario_timings: list[ScenarioTiming] = []
    with tempfile.TemporaryDirectory() as tmp:
        settings.data_dir = tmp
        settings.db_path = os.path.join(tmp, "t.db")
        store = Store(settings.db_path)
        pid, _key = await create_persona(store, name="Live", definition="You are live.",
                                        timezone="UTC")
        mind = Mind(settings, store=store, llms={"embed": None, "consultants": {},
                                                 "a": a_llm, "b": b_llm, "c": c_llm})
        try:
            for sc in scens:
                t = await run_live_scenario(mind, pid, sc, timings)
                scenario_timings.append(t)
                # Live log line.
                _log(line=json.dumps({
                    "event": "scenario_done",
                    "scenario": t.scenario,
                    "wall_clock_ms": round(t.wall_clock_ms, 1),
                    "turns": t.turns,
                    "errors": t.errors,
                }), path=log_path)
        finally:
            await mind.close()
    summary = summarize(scenario_timings, started)
    summary["per_lobe"] = {
        "A": a_llm.call_stats,
        "B": b_llm.call_stats,
        "C": c_llm.call_stats,
    }
    _log(line=json.dumps({"event": "summary", **summary}), path=log_path)
    return summary


async def run_live_harmonist(log_path: str, *, budget: int = 3) -> dict:
    """Run the harmonist against the live fake provider.

    C uses model-c (the observer's scripted brain) plus the harmonist prompt.
    Note: the fakes.py make_c returns the same observer payload regardless of
    system prompt, so the LLM-editor will keep proposing the same target.
    This is fine - the test verifies the loop runs end-to-end.
    """
    from sawii_persona.brains.harmonist import harmonist_pass
    from sawii_persona.brains import prompts as prompts_module
    settings = Settings()
    settings.a = build_endpoint("model-a")
    settings.b = build_endpoint("model-b")
    settings.c = build_endpoint("model-c")
    c_llm = OpenAICompatLLM(settings.c, name="C-live")
    started = time.time()
    record = await harmonist_pass(
        c_llm, settings=settings, prompts_module=prompts_module,
        budget=budget, log_path=log_path,
    )
    finished = time.time()
    out = {
        "event": "harmonist_pass",
        "wall_clock_ms": (finished - started) * 1000,
        "baseline_score": record.baseline_score,
        "final_score": record.final_score,
        "delta": record.final_score - record.baseline_score,
        "n_proposals": len(record.proposals),
        "n_accepted": len(record.accepted),
        "n_rejected": len(record.rejected),
        "converged": record.converged,
        "status": record.status,
        "proposals": [
            {"target": p["target"], "delta": p["delta"], "rationale": p["rationale"]}
            for p in record.proposals
        ],
    }
    _log(line=json.dumps(out), path=log_path)
    return out


def _log(*, line: str, path: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a") as f:
        f.write(line + "\n")
        f.flush()
    print(line, flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8801)
    ap.add_argument("--budget", type=int, default=3)
    ap.add_argument("--mode", choices=["corpus", "harmonist", "both"], default="both")
    ap.add_argument("--log-dir", default="./data/live")
    args = ap.parse_args()
    global PROVIDER_URL
    PROVIDER_URL = f"http://127.0.0.1:{args.port}/v1"
    log_dir = args.log_dir
    os.makedirs(log_dir, exist_ok=True)
    corpus_log = os.path.join(log_dir, "corpus.jsonl")
    harmonist_log = os.path.join(log_dir, "harmonist.jsonl")
    if args.mode in ("corpus", "both"):
        print(f"[live] running corpus against {PROVIDER_URL}", flush=True)
        summary = asyncio.run(run_live_corpus(corpus_log, port=args.port))
        print(f"[live] corpus done: {summary['total_wall_ms']:.0f}ms wall, "
              f"{summary['n_scenarios']} scenarios", flush=True)
    if args.mode in ("harmonist", "both"):
        print(f"[live] running harmonist against {PROVIDER_URL}", flush=True)
        h = asyncio.run(run_live_harmonist(harmonist_log, budget=args.budget))
        print(f"[live] harmonist done: baseline={h['baseline_score']:.3f} "
              f"final={h['final_score']:.3f} delta={h['delta']:+.3f}", flush=True)


if __name__ == "__main__":
    main()