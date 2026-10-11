"""LiveVerifier: streaming verifier running concurrent with A's stream.

Reads each token A emits and incrementally builds a streaming-context
verdict. Emits verdict_partial events as confidence accumulates, and a
final verdict event when A's stream ends. By the time done fires in
mind.py:respond, the verdict is already buffered on the bus, so
chat() returns without polling.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

log = logging.getLogger(__name__)


VERIFIER_SYSTEM = """You are A's live reviewer. A is the voice of the companion; your job
is to catch lies, fabrication, or anything the recall does not support as
A speaks. You see A's tokens stream in real time. After each finished
sentence, emit a partial verdict - GREEN if it looks fine, YELLOW if you
spot a hedge that needs cleanup, RED if you spot a fabrication.

Output JSON only - one object per emit:
  partial: {"type":"verdict_partial","level":"GREEN|YELLOW|RED","reason":"<short>"}
  final:   {"type":"verdict","level":"GREEN|YELLOW|RED","reason":"<short>",
            "findings":[{"claim":"<exact text A wrote>","issue":"<why>","evidence":"<source or 'not in recall'>"}]}

Do NOT invent. If A says something that is not supported by the recall
or the user's exact words, mark it RED and explain why.
"""


class LiveVerifier:
    def __init__(self, llm, *, user_text, recall_text, rules, bus, persona_id,
                 timeout_s=60.0, on_health=None):
        self.llm = llm
        self.user_text = user_text
        self.recall_text = recall_text
        self.rules = rules
        self.bus = bus
        self.persona_id = persona_id
        self.on_health = on_health
        self.timeout_s = timeout_s
        self.queue: asyncio.Queue = asyncio.Queue()
        self._task = None
        self._final_level = None
        self._final_reason = ""
        self._final_findings = []
        self._started_at = 0.0
        self._finished_at = 0.0

    def start(self):
        self._started_at = asyncio.get_event_loop().time()
        self._task = asyncio.create_task(self._run(), name=f"live-verifier-{self.persona_id}")

    def observe_token(self, text):
        if self._task is None or self._task.done():
            return
        self._task = asyncio.create_task(self._feed(text), name=f"live-v-feed-{self.persona_id}")

    async def _feed(self, text):
        try:
            msgs = [
                {"role": "system", "content": VERIFIER_SYSTEM},
                {"role": "user", "content":
                    "User just said: " + self.user_text[:400] + "\n\n"
                    "A's accumulated transcript so far:\n" + text[:16000] + "\n\n"
                    "Recall summary:\n" + self.recall_text[:1500] + "\n\n"
                    "Rules to enforce:\n" + ("\n".join(self.rules[:5]) if self.rules else "(none)")},
                {"role": "user", "content":
                    "If you spot a fabrication, hedge, or unsupported claim in the "
                    "transcript above, emit a verdict_partial or verdict JSON now. "
                    "Otherwise wait for the next chunk."},
            ]
            stream = await self.llm.stream(msgs, temperature=0.2, max_tokens=200)
            async for ev in stream:
                if ev.get("type") == "text":
                    await self._emit(ev["text"])
        except Exception as exc:
            log.warning("live verifier feed failed: %s", exc)

    async def _emit(self, raw):
        raw = raw.strip()
        for line in raw.splitlines():
            line = line.strip()
            if not line or line[0] != "{":
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = obj.get("type")
            if kind == "verdict":
                self._final_level = obj.get("level", self._final_level or "GREEN")
                self._final_reason = str(obj.get("reason", ""))[:400]
                self._final_findings = obj.get("findings") or []
                await self.queue.put({
                    "from": "B", "kind": "verdict",
                    "level": self._final_level, "reason": self._final_reason,
                    "findings": self._final_findings,
                })
            elif kind == "verdict_partial":
                await self.queue.put({
                    "from": "B", "kind": "verdict_partial",
                    "level": obj.get("level", "GREEN"),
                    "reason": str(obj.get("reason", ""))[:200],
                })

    async def finalize(self):
        if self._task is None:
            return
        try:
            msgs = [
                {"role": "system", "content": VERIFIER_SYSTEM},
                {"role": "user", "content":
                    "User just said: " + self.user_text[:400] + "\n\n"
                    "A's full transcript is in your context above. Now emit the "
                    "FINAL verdict JSON. One object only."},
            ]
            stream = await self.llm.stream(msgs, temperature=0.1, max_tokens=300)
            async for ev in stream:
                if ev.get("type") == "text":
                    await self._emit(ev["text"])
        except Exception as exc:
            log.warning("live verifier finalize failed: %s", exc)
        self._finished_at = asyncio.get_event_loop().time()

    async def drain(self, max_wait_s=0.5):
        verdict = None
        deadline = asyncio.get_event_loop().time() + max_wait_s
        while True:
            try:
                ev = self.queue.get_nowait()
                if ev.get("kind") == "verdict":
                    verdict = ev
                elif ev.get("kind") == "verdict_partial" and verdict is None:
                    verdict = ev
            except asyncio.QueueEmpty:
                break
            if asyncio.get_event_loop().time() > deadline:
                break
        return verdict

    def elapsed(self):
        if self._started_at == 0:
            return 0.0
        if self._finished_at:
            return self._finished_at - self._started_at
        return asyncio.get_event_loop().time() - self._started_at
