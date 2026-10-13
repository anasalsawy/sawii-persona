"""Scripted brains that behave like the real contracts."""
import json

from sawii_persona.brains import prompts
from sawii_persona.llm import ChatResult, ScriptedLLM, ToolCall


def system_of(messages):
    return messages[0]["content"] if messages and messages[0]["role"] == "system" else ""


def last_user(messages):
    for m in reversed(messages):
        if m["role"] == "user":
            return m["content"]
    return ""


def make_a(*, use_tools=False, reply=None, chunk_delay=0.0, continuation="Oh wait - that reminds me of Rex."):
    def respond(messages, opts):
        sys = system_of(messages)
        if "[inner voice" in last_user(messages):
            return continuation + " ⟦feel: joy=0.4⟧"
        if sys.startswith(prompts.A_DIARY[:40]):
            u = last_user(messages)
            said = u.split("\n")[0].replace("USER: ", "")
            return json.dumps({"text": f"They told me: {said[:80]}", "quote": said[:40], "anchor": "chat"})
        if sys.startswith(prompts.NARRATIVE[:40]):
            return "Lately we talked about Maya's exam. I promised to check in."
        if sys.startswith(prompts.DREAM[:40]):
            return json.dumps({"associations": [{"text": "Maya's exam is like Rex stealing the chair", "about_ids": []}]})
        if sys.startswith(prompts.OUTREACH[:40]):
            return "Hey - did Maya's exam go okay?"
        if "rehearsal" in sys or sys.startswith(prompts.REPLAY[:30]):
            return json.dumps({"examples": [{"user": "hi", "assistant": "hey you"}] * 3})
        has_tool_result = any(m["role"] == "tool" for m in messages)
        if use_tools and opts.get("tools") and not has_tool_result:
            return ChatResult(text="Hang on. ", tool_calls=[ToolCall(id="c1", name="fetch_consults", arguments="{}")])
        consult = next((m["content"] for m in messages if m["role"] == "tool"), "")
        text = reply or "Good luck to Maya! I'll remember Friday."
        if consult:
            text = f"Checked: {consult[:60]}. " + text
        return text + " ⟦feel: joy=0.5 tenderness=0.6⟧"
    return ScriptedLLM(respond, name="A", chunk=5, chunk_delay=chunk_delay)


REMEMBER_HEAD = prompts.REMEMBER[:60]


def make_b(correction="", afterthought="", *, pick=None, pick_delay=0.0, live_note="",
           tone="", surprise="", tender=False, level="GREEN", extra=None):
    """``pick(listing_text, situation)`` -> list of {"id", "kind", "connection"} for B's remembering."""
    async def respond(messages, opts):
        sys = system_of(messages)
        body = last_user(messages)
        if sys.startswith(REMEMBER_HEAD):
            if pick_delay:
                import asyncio
                await asyncio.sleep(pick_delay)
            return json.dumps({"memories": pick(body, sys) if pick else []})
        if sys.startswith(prompts.LIVE_CHECK[:40]):
            return json.dumps({"interrupt": bool(live_note), "note": live_note})
        if sys.startswith(prompts.OUTREACH_CHECK[:40]):
            return json.dumps({"ok": True, "why": "grounded"})
        if sys.startswith(prompts.DEEP_CRITIC[:30]):
            return json.dumps({"score": 0.8, "why": "fine"})
        if sys.startswith(prompts.DEEP_PLAN[:30]):
            return json.dumps({"mode": "map", "subtasks": ["part one", "part two"]})
        if sys.startswith(prompts.DEEP_REDUCE[:30]):
            return "merged result"
        # ---- the psyche (B's other half): tone, mirror, profile, the affect read
        if sys.startswith(prompts.GUT_READ[:40]):
            return json.dumps({"tone": tone, "surprise": surprise})
        if sys.startswith(prompts.MIRROR[:40]):
            return json.dumps({"habits": ["opens every reply with 'Oh'"]})
        if sys.startswith(prompts.PSYCH_PROFILE[:40]):
            return "I have noticed they worry about people they love (moderate confidence)."
        if sys.startswith(prompts.PSYCHE_SYSTEM[:40]):
            notes, readings, hyps, ents = [], [], [], []
            if "Maya" in body:
                notes.append({"kind": "detail", "text": "sister Maya's exam Friday",
                              "quote": "my sister Maya has her exam on friday", "anchor": "Maya exam",
                              "salience": 3, "chord": {"fear": 0.5, "tenderness": 0.6}, "tender": tender})
                readings.append({"text": "worry shows as hedging - possibly anxious", "quote": "I'm nervous",
                                 "anchor": "Maya exam"})
                hyps.append({"op": "new", "claim": "worries about family", "quotes": ["I'm nervous"]})
                ents.append({"name": "Maya", "kind": "person", "account": "user's sister, exam Friday",
                             "quote": "my sister Maya"})
            return json.dumps({"user_chord": {"fear": 0.5, "tenderness": 0.5}, "notes": notes,
                               "readings": readings, "hypotheses": hyps, "entities": ents, "reinterpret": [],
                               "brief_for_c": "Maya matters a lot; keep the exam on the radar.",
                               **(extra(body) if extra else {})})
        loops = []
        if "remind me" in body:
            loops.append({"op": "open", "kind": "reminder", "text": "check on Maya's exam",
                          "quote": "remind me", "due": "in 1 seconds"})
        review = {
            "verdict": {"level": level, "rationale": "fine" if level == "GREEN" else "unsupported claim",
                        "findings": [] if level == "GREEN" else [{"claim": "x", "issue": "unsupported", "evidence": "y"}]},
            "correction_required": correction,
            "collateral": [{"text": "nervous for Maya", "quote": "I'm nervous", "anchor": "Maya exam"}],
            "loops": loops, "conscience": "Ask how they are sleeping.", "afterthought": afterthought,
            **(extra(body) if extra else {})}
        return json.dumps(review)
    return ScriptedLLM(respond, name="B")


def make_c(*, level="GREEN", audit=None, extra=None):
    def respond(messages, opts):
        sys = system_of(messages)
        body = last_user(messages)
        if sys.startswith(prompts.AUDIT[:40]):
            return json.dumps({"unsupported": audit(body) if audit else [], "ok": True})
        if sys.startswith(prompts.CALIBRATE[:40]):
            return json.dumps({"careful_about": ["dates of past events"]})
        # Everything else is the verifier's review. The claims ledger is what
        # compute_meter() actually reads: a claim quoted from A's scripted
        # reply, marked unsupported, so a non-GREEN level reaches the record.
        review = {
            "verdict": {"level": level, "rationale": "fine" if level == "GREEN" else "unsupported claim",
                        "findings": [] if level == "GREEN" else [{"claim": "x", "issue": "unsupported", "evidence": "y"}]},
            "correction_required": "", "collateral": [], "loops": [], "conscience": "", "afterthought": "",
            **(extra(body) if extra else {}),
        }
        if level != "GREEN":
            review["claims"] = [{"quote": "I'll remember Friday.", "kind": "COMPLETION",
                                 "support": "UNSUPPORTED", "evidence_quote": ""}]
        return json.dumps(review)
    return ScriptedLLM(respond, name="C")


def make_consultant(text="Exams on Friday: suggest sleep + review."):
    return ScriptedLLM(text, name="consult")
