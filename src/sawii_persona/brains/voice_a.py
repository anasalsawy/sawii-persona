"""A - the voice. Streams to the person, uses its tools mid-sentence, and
declares its own feeling (the chord) in a private suffix that is removed
before anyone sees it. A is the one who *experiences* the emotional spectrum:
its declared chord becomes the key that pulls mood-congruent memories.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import AsyncIterator, Awaitable, Callable

from .. import emotion
from ..llm import LLM, ToolCall
from .prompts import FEEL_CLOSE, FEEL_OPEN

_MARKERS = (FEEL_OPEN, "[[feel:", "[feel:")
_FEEL_RE = re.compile(r"(?:⟦|\[\[|\[)feel:(.*?)(?:⟧|\]\]|\]|$)", re.S | re.I)

A_TOOLS = [
    {"type": "function", "function": {
        "name": "fetch_consults",
        "description": "See what your parallel minds have worked out about the current question so far. "
                       "Returns whatever is ready (waits at most a moment) and who is still working.",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {
        "name": "check_clock",
        "description": "Look at the running clock: current time, seconds since your last reply, seconds this "
                       "request has been running.",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {
        "name": "delegate_task",
        "description": "Hand heavy multi-step work (research, reading, coding, files) to your workshop. It runs "
                       "in the background; the result comes back to you later.",
        "parameters": {"type": "object", "properties": {
            "task": {"type": "string", "description": "What to do, fully specified."}},
            "required": ["task"]}}},
]


class FeelFilter:
    """Streaming filter: passes text through, swallows the private feel suffix."""

    def __init__(self):
        self.buf = ""
        self.captured = ""
        self.hidden = False

    def feed(self, piece: str) -> str:
        if self.hidden:
            self.captured += piece
            return ""
        self.buf += piece
        for m in _MARKERS:
            pos = self.buf.find(m)
            if pos != -1:
                out, self.captured, self.hidden = self.buf[:pos], self.buf[pos:], True
                self.buf = ""
                return out
        # hold back a tail that could be the start of a marker
        hold = 0
        for m in _MARKERS:
            for k in range(min(len(m) - 1, len(self.buf)), 0, -1):
                if self.buf.endswith(m[:k]):
                    hold = max(hold, k)
                    break
        out = self.buf[:len(self.buf) - hold] if hold else self.buf
        self.buf = self.buf[len(out):]
        return out

    def flush(self) -> str:
        out, self.buf = self.buf, ""
        return out

    def chord(self) -> dict:
        m = _FEEL_RE.search(self.captured)
        return emotion.parse_declaration(m.group(1)) if m else {}


def strip_feel(text: str) -> tuple[str, dict]:
    f = FeelFilter()
    out = f.feed(text) + f.flush()
    return out.rstrip(), f.chord()


ToolExec = Callable[[ToolCall], Awaitable[str]]


@dataclass
class VoiceResult:
    text: str = ""
    chord: dict = field(default_factory=dict)
    tools_used: list[str] = field(default_factory=list)


async def speak(llm: LLM, messages: list[dict], *, tools: list[dict] | None, run_tool: ToolExec,
                result: VoiceResult, max_rounds: int = 4, model: str | None = None,
                temperature: float = 0.8) -> AsyncIterator[str]:
    """Stream A's answer, executing tool calls between rounds. Yields visible text."""
    msgs = list(messages)
    feel = FeelFilter()
    for round_no in range(max_rounds):
        round_text = ""
        calls: list[ToolCall] = []
        use_tools = tools if round_no < max_rounds - 1 else None
        kwargs = {"tools": use_tools, "temperature": temperature, "max_tokens": 1500}
        if model:
            kwargs["model"] = model
        async for ev in llm.stream(msgs, **kwargs):
            if ev["type"] == "text":
                round_text += ev["text"]
                visible = feel.feed(ev["text"])
                if visible:
                    result.text += visible
                    yield visible
            elif ev["type"] == "tool_call":
                calls.append(ev["call"])
        if not calls:
            break
        tail = feel.flush()
        if tail:
            result.text += tail
            yield tail
        feel = FeelFilter() if not feel.hidden else feel
        msgs.append({"role": "assistant", "content": round_text or None, "tool_calls": [
            {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments or "{}"}}
            for c in calls]})
        for c in calls:
            result.tools_used.append(c.name)
            try:
                out = await run_tool(c)
            except Exception as exc:  # noqa: BLE001
                out = json.dumps({"error": str(exc)[:200]})
            msgs.append({"role": "tool", "tool_call_id": c.id, "content": out})
        if result.text and not result.text.endswith((" ", "\n")):
            result.text += " "
            yield " "
    tail = feel.flush()
    if tail:
        result.text += tail
        yield tail
    result.text = result.text.strip()
    result.chord = feel.chord()
