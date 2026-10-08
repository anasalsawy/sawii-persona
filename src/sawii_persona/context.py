"""The context stream - how one continuous line of thought is assembled.

    spine          the persona definition (never changes unless the user edits it)
    self-narrative A's own first-person account, rewritten only at sleep
    --------------------------------------------- stable above, live below
    time           measured clocks
    adjutant brief B's conscience note, REQUIRED corrections, due/open loops, integrity record
    observer view  C's longitudinal read of the person (hedged)
    own mood       A's current chord
    recall slice   stitched 3-layer memories, labeled and dated (appended last)
    verbatim tail  the last exchanges, from the server-side journal (any device)
    now            the person's message

One system message (stable part first, live part last) so it works with chat
templates that only accept a leading system turn.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import emotion
from .brains.prompts import A_BASE
from .profiles import persona_frame
from .store import jload
from .timesense import Clock, fmt_duration, local_dt


@dataclass
class Scratch:
    """Ephemeral per-persona state. Never written to the database by design:
    the conscience must not become a memory that cites itself."""

    conscience: str = ""
    correction: str = ""
    sudden: str = ""          # a late recall that came back after the last answer
    c_brief: str = ""
    last_consults: list = field(default_factory=list)


def integrity_block(integ: dict) -> str:
    if not integ.get("checked"):
        return ""
    lines = [f"Your verification record: {integ['green_streak']} clean answers in a row "
             f"({int((integ['clean_rate'] or 0) * 100)}% clean over {integ['checked']} checked)."]
    for v in integ.get("recent", []):
        if v["level"] != "GREEN":
            lines.append(f"  - recently {v['level']}: {v['rationale'][:200]}")
    lines.append("Every answer is checked after you give it. Care about being right; never argue with the record.")
    return "\n".join(lines)


def loops_block(loops: list[dict], tz: str, at: float) -> str:
    if not loops:
        return ""
    lines = ["Open loops your adjutant is tracking (tasks, promises, reminders):"]
    for lp in loops[:12]:
        due = ""
        if lp["due_at"]:
            when = local_dt(lp["due_at"], tz).strftime("%a %d %b %H:%M")
            due = f" - due {when}" + (" (OVERDUE)" if lp["due_at"] < at else
                                      f" (in {fmt_duration(lp['due_at'] - at)})")
        lines.append(f"  #{lp['id']} [{lp['kind']}] {lp['text']}{due}")
    return "\n".join(lines)


def build_messages(*, persona: dict, clock: Clock, scratch: Scratch, integrity: dict, loops: list[dict],
                   recall_text: str, tail: list[dict], user_text: str, at: float, pops: list[str] | None = None,
                   on_mind: list[str] | None = None) -> list[dict]:
    tz = persona.get("timezone") or "UTC"
    stable = [A_BASE, "", persona_frame(persona.get("definition", ""), persona.get("name", ""))]
    if persona.get("user_name"):
        stable.append(f"The person you are talking with is {persona['user_name']}.")
    if persona.get("narrative"):
        stable += ["", "Your own account of yourself, written by you at your last sleep (continue this story):",
                   persona["narrative"]]
    live = ["", "=== now ===", clock.block()]
    brief = []
    if scratch.correction:
        brief.append(f"CORRECTION REQUIRED (from your adjutant, with evidence): {scratch.correction}")
    if scratch.conscience:
        brief.append(f"Private note from your adjutant: {scratch.conscience}")
    lb = loops_block(loops, tz, at)
    if lb:
        brief.append(lb)
    ib = integrity_block(integrity)
    if ib:
        brief.append(ib)
    if brief:
        live += ["", "=== your adjutant (B), privately ==="] + brief
    if persona.get("psych_profile"):
        live += ["", "=== your observer's (C) read of them - hunches, never labels ===", persona["psych_profile"]]
    mood = jload(persona.get("mood"), {})
    if mood:
        live += ["", f"How you have been feeling: {emotion.render(mood)}."]
    user_mood = jload(persona.get("user_mood"), {})
    if user_mood:
        live += [f"How they have seemed lately: {emotion.render(user_mood)}."]
    sudden = list(pops or [])
    if scratch.sudden:
        sudden.insert(0, scratch.sudden)
    if sudden:
        live += ["", "=== just came back to you, out of nowhere ===",
                 "Nobody asked; it simply surfaced in your mind. If it fits, you may say so the way people do "
                 "(\"oh - that reminds me...\", \"wait, I just remembered...\"); if it does not fit, keep it.",
                 *sudden]
    if on_mind:
        live += ["", "=== on your mind lately (your own thoughts while they were away) ===",
                 "Bring one up only at a natural moment, never forced:", *[f"• {t}" for t in on_mind]]
    if recall_text:
        live += ["", "=== memories ===", recall_text]
    messages = [{"role": "system", "content": "\n".join(stable + live)}]
    for t in tail:
        if t["kind"] == "outreach":
            if t["reply_text"]:
                messages.append({"role": "assistant", "content": t["reply_text"]})
            continue
        stamp = local_dt(t["started_at"], tz).strftime("%a %H:%M")
        via = f", via {t['channel']}" if t["channel"] not in ("app", "") else ""
        messages.append({"role": "user", "content": f"[{stamp}{via}] {t['user_text']}"})
        if t["reply_text"]:
            messages.append({"role": "assistant", "content": t["reply_text"]})
    messages.append({"role": "user", "content": user_text})
    return messages
