"""Concurrency / load test: run N Mind instances in parallel against the same
provider. Measures throughput, contention, and per-mind latency variance.

Usage:
    # Start provider
    PYTHONPATH=src python tests/fake_provider.py 8801 &
    # Then:
    python -m arch_finetune.concurrency_test --port 8801 --n-minds 4 --scenarios 3
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + "/src")

from sawii_persona.config import Settings, ModelEndpoint
from sawii_persona.embedder import Embedder
from sawii_persona.identity import create_persona
from sawii_persona.llm import OpenAICompatLLM
from sawii_persona.mind import Mind
from sawii_persona.store import Store

from .corpus import ALL_SCENARIOS


def build_endpoint(port: int, model: str) -> ModelEndpoint:
    return ModelEndpoint(base_url=f"http://127.0.0.1:{port}/v1",
                         api_key="not-needed", model=model)


async def one_mind_run(mind_id: int, port: int, scenarios: list) -> dict:
    """Run a single Mind through the scenarios. Returns per-mind stats."""
    settings = Settings()
    settings.a = build_endpoint(port, "model-a")
    settings.b = build_endpoint(port, "model-b")
    settings.c = build_endpoint(port, "model-c")
    a_llm = OpenAICompatLLM(settings.a, name=f"A-{mind_id}")
    b_llm = OpenAICompatLLM(settings.b, name=f"B-{mind_id}")
    c_llm = OpenAICompatLLM(settings.c, name=f"C-{mind_id}")
    a_llm.reset_stats(); b_llm.reset_stats(); c_llm.reset_stats()
    started = time.time()
    per_turn_ms = []
    errors = []
    with tempfile.TemporaryDirectory() as tmp:
        settings.data_dir = tmp
        settings.db_path = os.path.join(tmp, f"t-{mind_id}.db")
        store = Store(settings.db_path)
        pid, _key = await create_persona(store, name=f"M{mind_id}",
                                        definition="parallel test.", timezone="UTC")
        mind = Mind(settings, store=store, llms={"embed": None, "consultants": {},
                                                 "a": a_llm, "b": b_llm, "c": c_llm})
        try:
            for sc in scenarios:
                if sc.setup is not None:
                    try:
                        await sc.setup(mind, pid)
                    except Exception as exc:
                        errors.append(f"setup: {type(exc).__name__}: {exc}")
                for turn in sc.turns:
                    t0 = time.monotonic()
                    try:
                        async for _ev in mind.respond(pid, turn.user,
                                                     channel=turn.channel,
                                                     surface=turn.surface):
                            pass
                    except Exception as exc:
                        errors.append(f"{type(exc).__name__}: {str(exc)[:100]}")
                    per_turn_ms.append((time.monotonic() - t0) * 1000)
        finally:
            await mind.close()
    return {
        "mind_id": mind_id,
        "wall_clock_ms": round((time.time() - started) * 1000, 1),
        "n_turns": len(per_turn_ms),
        "n_errors": len(errors),
        "per_turn_p50_ms": round(statistics.median(per_turn_ms), 1) if per_turn_ms else 0,
        "per_turn_p95_ms": round(sorted(per_turn_ms)[int(len(per_turn_ms) * 0.95)],
                                 1) if per_turn_ms else 0,
        "per_turn_max_ms": round(max(per_turn_ms), 1) if per_turn_ms else 0,
        "per_lobe_calls": {"A": a_llm.call_stats["n_calls"],
                           "B": b_llm.call_stats["n_calls"],
                           "C": c_llm.call_stats["n_calls"]},
    }


async def main_async(port: int, n_minds: int, n_scenarios: int):
    scenarios = ALL_SCENARIOS[:n_scenarios]
    started = time.time()
    results = await asyncio.gather(*(one_mind_run(i, port, scenarios)
                                     for i in range(n_minds)))
    wall = round((time.time() - started) * 1000, 1)
    n_turns_total = sum(r["n_turns"] for r in results)
    throughput = round(n_turns_total / (wall / 1000), 2) if wall > 0 else 0
    return {
        "event": "concurrency_run",
        "n_minds": n_minds,
        "n_scenarios": n_scenarios,
        "wall_clock_ms": wall,
        "n_turns_total": n_turns_total,
        "throughput_turns_per_sec": throughput,
        "minds": results,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8801)
    ap.add_argument("--n-minds", type=int, default=4)
    ap.add_argument("--scenarios", type=int, default=3)
    args = ap.parse_args()
    out = asyncio.run(main_async(args.port, args.n_minds, args.scenarios))
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()