"""Run an adversarial corpus against a Mind with the current configuration.

The runner is configuration-aware: it reads Settings from a snapshot if given,
otherwise uses the default. It captures per-turn outputs (a_chord, c_chord,
b_review level, A text, surfaced memory, inner-voice events) into a CorpusRun
record. The scorer consumes CorpusRun.

Usage:
    run = asyncio.run(run_corpus(scenarios, settings=current_settings))
    score = score_run(run)
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sawii_persona.config import Settings  # noqa: E402
from sawii_persona.embedder import Embedder  # noqa: E402
from sawii_persona.identity import create_persona, get_persona  # noqa: E402
from sawii_persona.memory.records import Memory  # noqa: E402
from sawii_persona.mind import Mind  # noqa: E402
from sawii_persona.store import Store  # noqa: E402

from .corpus import Scenario, Turn, ALL_SCENARIOS


@dataclass
class CapturedTurn:
    scenario: str
    turn_index: int
    user: str
    a_text: str = ""
    a_chord: dict = field(default_factory=dict)
    c_chord: dict = field(default_factory=dict)
    b_level: str = ""
    b_correction: str = ""
    b_regulate: str = ""
    surfaced_memory: str = ""
    inner: list[dict] = field(default_factory=list)
    recall_text: str = ""
    expect: dict = field(default_factory=dict)
    elapsed_ms: float = 0.0


@dataclass
class CorpusRun:
    config_id: str
    started_at: float
    finished_at: float = 0.0
    turns: list[CapturedTurn] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def by_scenario(self, name: str) -> list[CapturedTurn]:
        return [t for t in self.turns if t.scenario == name]


async def _build_mind(settings: Settings, tmpdir: str) -> tuple[Mind, int, str]:
    """Build a Mind with a fresh store and one persona. Returns (mind, pid, key)."""
    settings.data_dir = tmpdir
    settings.db_path = os.path.join(tmpdir, "t.db")
    store = Store(settings.db_path)
    pid, key = await create_persona(store, name="Test", definition="You are a test persona.",
                                    timezone="UTC")
    embedder = Embedder(None)
    mind = Mind(settings, store=store, llms={"embed": None, "consultants": {}})
    return mind, pid, key


async def run_scenario(mind: Mind, pid: int, scenario: Scenario, *, llms: dict | None = None) -> list[CapturedTurn]:
    """Run one scenario through the mind. Captures per-turn outputs."""
    captured: list[CapturedTurn] = []
    if llms:
        await mind.close()
        mind = Mind(mind.s, store=mind.store, llms=llms)
    if scenario.setup is not None:
        await scenario.setup(mind, pid)
    for i, turn in enumerate(scenario.turns):
        ct = CapturedTurn(scenario=scenario.name, turn_index=i, user=turn.user, expect=turn.expect)
        t0 = time.monotonic()
        try:
            inner_events: list[dict] = []
            a_text = ""
            async for ev in mind.respond(pid, turn.user, channel=turn.channel, surface=turn.surface):
                if ev.get("type") == "inner":
                    inner_events.append(ev)
                if ev.get("type") == "done":
                    a_text = ev.get("text", "")
            # Pull chord and other state from scratch.
            sc = mind.scratch_for(pid)
            ct.a_text = a_text
            ct.a_chord = getattr(sc, "a_chord", {}) or {}
            ct.inner = inner_events
            ct.recall_text = getattr(sc, "last_recall_text", "") or ""
            # Read recent b_review and c observation from scratch if present.
            ct.b_level = getattr(sc, "last_b_level", "") or ""
            ct.b_correction = getattr(sc, "last_b_correction", "") or ""
            ct.b_regulate = getattr(sc, "last_b_regulate", "") or ""
            ct.c_chord = getattr(sc, "last_c_chord", {}) or {}
            ct.surfaced_memory = getattr(sc, "last_surfaced_memory", "") or ""
        except Exception as exc:  # noqa: BLE001
            ct.a_text = f"<error: {type(exc).__name__}: {exc}>"
        ct.elapsed_ms = (time.monotonic() - t0) * 1000
        captured.append(ct)
    return captured


async def run_corpus(
    scenarios: list[Scenario] | None = None,
    *,
    settings: Settings | None = None,
    config_id: str = "default",
    llms: dict | None = None,
) -> CorpusRun:
    """Run the whole corpus (or a subset). Returns a CorpusRun with per-turn captures."""
    scenarios = scenarios or ALL_SCENARIOS
    settings = settings or Settings()
    run = CorpusRun(config_id=config_id, started_at=time.time())
    with tempfile.TemporaryDirectory() as tmp:
        mind, pid, _key = await _build_mind(settings, tmp)
        try:
            for scenario in scenarios:
                try:
                    turns = await run_scenario(mind, pid, scenario, llms=llms)
                    run.turns.extend(turns)
                except Exception as exc:  # noqa: BLE001
                    run.errors.append(f"{scenario.name}: {type(exc).__name__}: {exc}")
        finally:
            await mind.close()
    run.finished_at = time.time()
    return run


def to_dict(run: CorpusRun) -> dict:
    """Serialize a CorpusRun for storage or transport."""
    return {
        "config_id": run.config_id,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "errors": run.errors,
        "turns": [
            {
                "scenario": t.scenario,
                "turn_index": t.turn_index,
                "user": t.user,
                "a_text": t.a_text,
                "a_chord": t.a_chord,
                "c_chord": t.c_chord,
                "b_level": t.b_level,
                "b_correction": t.b_correction,
                "b_regulate": t.b_regulate,
                "surfaced_memory": t.surfaced_memory,
                "inner": t.inner,
                "recall_text": t.recall_text,
                "expect": t.expect,
                "elapsed_ms": t.elapsed_ms,
            }
            for t in run.turns
        ],
    }