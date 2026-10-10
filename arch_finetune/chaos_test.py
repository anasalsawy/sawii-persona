"""Failure-mode hardening: drive the Mind against a chaos_provider and check
that:
  1. The Mind doesn't crash.
  2. Each call records an error in OpenAICompatLLM.call_stats.
  3. The runner still produces a summary.
  4. The runner's elapsed_ms for failed turns is bounded (no hang).

Usage:
    # In one shell:
    python -m arch_finetune.chaos_provider 8802 --mode server_error
    # In another:
    python -m arch_finetune.chaos_test --port 8802 --mode server_error
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + "/src")

from sawii_persona.config import Settings, ModelEndpoint
from sawii_persona.embedder import Embedder
from sawii_persona.identity import create_persona
from sawii_persona.llm import OpenAICompatLLM, LLMError
from sawii_persona.mind import Mind
from sawii_persona.store import Store

from .corpus import ALL_SCENARIOS


def build_endpoint(port: int, model: str, timeout: float = 5.0) -> ModelEndpoint:
    return ModelEndpoint(base_url=f"http://127.0.0.1:{port}/v1",
                         api_key="not-needed", model=model, timeout=timeout)


async def run(port: int, mode: str, *, timeout: float = 5.0, n_scenarios: int = 3):
    """Run the first n_scenarios against the chaos provider. Record what happens."""
    settings = Settings()
    settings.a = build_endpoint(port, "model-a", timeout=timeout)
    settings.b = build_endpoint(port, "model-b", timeout=timeout)
    settings.c = build_endpoint(port, "model-c", timeout=timeout)
    a_llm = OpenAICompatLLM(settings.a, name="A-chaos")
    b_llm = OpenAICompatLLM(settings.b, name="B-chaos")
    c_llm = OpenAICompatLLM(settings.c, name="C-chaos")
    a_llm.reset_stats(); b_llm.reset_stats(); c_llm.reset_stats()

    per_turn = []
    errors = []
    started = time.time()
    with tempfile.TemporaryDirectory() as tmp:
        settings.data_dir = tmp
        settings.db_path = os.path.join(tmp, "t.db")
        store = Store(settings.db_path)
        pid, _key = await create_persona(store, name="Chaos", definition="chaos test.",
                                        timezone="UTC")
        mind = Mind(settings, store=store, llms={"embed": None, "consultants": {},
                                                 "a": a_llm, "b": b_llm, "c": c_llm})
        try:
            for sc in ALL_SCENARIOS[:n_scenarios]:
                if sc.setup is not None:
                    try:
                        await sc.setup(mind, pid)
                    except Exception as exc:
                        errors.append(f"setup {sc.name}: {type(exc).__name__}: {exc}")
                for i, turn in enumerate(sc.turns):
                    t0 = time.monotonic()
                    saw_error = False
                    try:
                        async for ev in mind.respond(pid, turn.user, channel=turn.channel,
                                                     surface=turn.surface):
                            if ev.get("type") == "error":
                                saw_error = True
                    except Exception as exc:
                        errors.append(f"{sc.name}/{i}: {type(exc).__name__}: {str(exc)[:100]}")
                        saw_error = True
                    per_turn.append({
                        "scenario": sc.name, "i": i,
                        "wall_ms": round((time.monotonic() - t0) * 1000, 1),
                        "saw_error_event": saw_error,
                    })
        finally:
            await mind.close()
    return {
        "event": "chaos_run",
        "mode": mode,
        "wall_clock_ms": round((time.time() - started) * 1000, 1),
        "n_turns": len(per_turn),
        "n_turns_with_error": sum(1 for p in per_turn if p.get("saw_error_event")),
        "n_uncaught_errors": len(errors),
        "errors": errors[:5],
        "max_per_turn_ms": max((p["wall_ms"] for p in per_turn), default=0),
        "per_lobe": {
            "A": dict(a_llm.call_stats),
            "B": dict(b_llm.call_stats),
            "C": dict(c_llm.call_stats),
        },
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8802)
    ap.add_argument("--mode", default="server_error")
    ap.add_argument("--timeout", type=float, default=5.0)
    ap.add_argument("--n-scenarios", type=int, default=3)
    args = ap.parse_args()
    out = asyncio.run(run(args.port, args.mode, timeout=args.timeout,
                          n_scenarios=args.n_scenarios))
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()