"""C - the observer (psych model). Reads the exchange plus B's review and
writes the affect line: notes, behavioural readings, the user's chord,
hypotheses about the person, entity cards, reinterpretations of recalled
memories, and a brief for B (what B needs to hear, not what B wants to hear).
"""
from __future__ import annotations

from .. import emotion
from ..llm import LLM, extract_json
from ..store import jload
from .adjutant import transcript
from .prompts import C_SYSTEM


async def observe(llm: LLM, *, persona: dict, user_text: str, reply: str, tail: list[dict], b_review: dict,
                  hypotheses: list[dict], recalled: list[dict], effective_conf) -> dict:
    parts = []
    if hypotheses:
        parts.append("Your current hypotheses about them:\n" + "\n".join(
            f"#{h['id']} ({effective_conf(h):.2f}) {h['claim']}" for h in hypotheses[:15]))
    if recalled:
        parts.append("Memories that came up this turn (you may reinterpret by id; quotes never change):\n" + "\n".join(
            f"#{r['id']} [{r['layer']}] {r['text']}" + (f' - "{r["quote"]}"' if r["quote"] else "")
            for r in recalled[:12]))
    if b_review:
        parts.append("B's private review of A's answer: " + f"{b_review.get('level')} - {b_review.get('rationale', '')}"
                     + (f"\nB's note to A: {b_review['conscience']}" if b_review.get("conscience") else ""))
    prev = jload(persona.get("user_mood"), {})
    if prev:
        parts.append(f"How they seemed before this turn: {emotion.render(prev)}")
    parts.append("Transcript:\n" + transcript(tail, user_text, reply))
    res = await llm.chat([{"role": "system", "content": C_SYSTEM},
                          {"role": "user", "content": "\n\n".join(parts)}],
                         temperature=0.3, max_tokens=1600, json_mode=True)
    data = extract_json(res.text) or {}

    def lst(key, n):
        return [x for x in (data.get(key) or []) if isinstance(x, dict)][:n]

    return {
        "user_chord": emotion.normalize(data.get("user_chord")),
        "notes": lst("notes", 8),
        "readings": lst("readings", 4),
        "hypotheses": lst("hypotheses", 4),
        "entities": lst("entities", 6),
        "reinterpret": lst("reinterpret", 4),
        "brief_for_b": str(data.get("brief_for_b", "") or "").strip()[:800],
        "expect": str(data.get("expect", "") or "").strip()[:300],
        "disagree_with_b": str(data.get("disagree_with_b", "") or "").strip()[:400],
        "parsed": bool(data),
    }
