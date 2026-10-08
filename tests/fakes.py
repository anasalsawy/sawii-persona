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


def make_a(*, use_tools=False, reply=None):
    def respond(messages, opts):
        sys = system_of(messages)
        if sys.startswith(prompts.A_DIARY[:40]):
            u = last_user(messages)
            said = u.split("\n")[0].replace("USER: ", "")
            return json.dumps({"text": f"They told me: {said[:80]}", "quote": said[:40], "anchor": "chat"})
        if sys.startswith(prompts.NARRATIVE[:40]):
            return "Lately we talked about Maya's exam. I promised to check in."
        if sys.startswith(prompts.WANDER[:40]):
            return json.dumps({"thought": "I wonder how Maya's exam went.", "about_ids": [], "worth_saying": 0.9,
                               "why": "unresolved"})
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
    return ScriptedLLM(respond, name="A", chunk=5)


def make_b(level="GREEN", correction="", afterthought=""):
    def respond(messages, opts):
        sys = system_of(messages)
        if sys.startswith(prompts.OUTREACH_CHECK[:40]):
            return json.dumps({"ok": True, "why": "grounded"})
        if sys.startswith(prompts.DEEP_CRITIC[:30]):
            return json.dumps({"score": 0.8, "why": "fine"})
        if sys.startswith(prompts.DEEP_PLAN[:30]):
            return json.dumps({"mode": "map", "subtasks": ["part one", "part two"]})
        if sys.startswith(prompts.DEEP_REDUCE[:30]):
            return "merged result"
        body = last_user(messages)
        loops = []
        if "remind me" in body:
            loops.append({"op": "open", "kind": "reminder", "text": "check on Maya's exam",
                          "quote": "remind me", "due": "in 1 seconds"})
        return json.dumps({
            "verdict": {"level": level, "rationale": "fine" if level == "GREEN" else "unsupported claim",
                        "findings": [] if level == "GREEN" else [{"claim": "x", "issue": "unsupported", "evidence": "y"}]},
            "correction_required": correction,
            "collateral": [{"text": "nervous for Maya", "quote": "I'm nervous", "anchor": "Maya exam"}],
            "loops": loops, "conscience": "Ask how they are sleeping.", "afterthought": afterthought})
    return ScriptedLLM(respond, name="B")


def make_c():
    def respond(messages, opts):
        sys = system_of(messages)
        if sys.startswith(prompts.PSYCH_PROFILE[:40]):
            return "I have noticed they worry about people they love (moderate confidence)."
        body = last_user(messages)
        notes, readings, hyps, ents = [], [], [], []
        if "Maya" in body:
            notes.append({"kind": "detail", "text": "sister Maya's exam Friday",
                          "quote": "my sister Maya has her exam on friday", "anchor": "Maya exam", "salience": 3,
                          "chord": {"fear": 0.5, "tenderness": 0.6}})
            readings.append({"text": "worry shows as hedging - possibly anxious", "quote": "I'm nervous",
                             "anchor": "Maya exam"})
            hyps.append({"op": "new", "claim": "worries about family", "quotes": ["I'm nervous"]})
            ents.append({"name": "Maya", "kind": "person", "account": "user's sister, exam Friday",
                         "quote": "my sister Maya"})
        return json.dumps({"user_chord": {"fear": 0.5, "tenderness": 0.5}, "notes": notes, "readings": readings,
                           "hypotheses": hyps, "entities": ents, "reinterpret": [],
                           "brief_for_b": "Maya matters a lot; keep the exam on the radar."})
    return ScriptedLLM(respond, name="C")


def make_consultant(text="Exams on Friday: suggest sleep + review."):
    return ScriptedLLM(text, name="consult")
