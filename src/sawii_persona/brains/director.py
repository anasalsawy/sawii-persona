"""B as director mode - a persistent worker A can hand multi-step jobs to.

A is the voice. B is the hands. When the user asks for something that would
take A minutes or hours of tool-calling, A hands it to B via the ``direct_B``
tool: a single clear instruction. B accepts, starts working immediately in
the background, and keeps running across the user's subsequent turns. The
user is never left waiting on A because A does not DO things; A talks.

B uses the gateway's plugins (shell, python, http, read_file, write_file,
weather, …) through the same webhook A uses when model-driven tools are
enabled. B's tool calls publish b_tool_start / b_tool_done events on the
persona's Bus so the companion-app client can render a live worklog of what
B is doing right now, same shape as A's inline tool chips.

B is NOT the OpenHands workshop ("delegate_task"). OpenHands is a heavy
external sandbox for building whole services. B is the lighter, more
conversational directing loop that works WITH A over hours: A directs, B
runs, B reports back, A directs again, user watches the whole thing.

One B per persona. B outlives any single user turn; it survives A finishing
its reply, and it only pauses when either (a) the running directive
completes, (b) A sends ``cancel_B`` for an abort, or (c) the process is
restarted - in which case the next startup will not resurrect B. Persisting
B's state across restarts is a future problem.
"""
from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

import httpx

from ..llm import LLM, ToolCall, strip_reasoning

log = logging.getLogger(__name__)


B_SYSTEM = """You are B, the autonomous working lobe of a two-mind companion. A is the voice to the
user; A has just directed you to do something and has already told the user you are on it.

Rules:
- You run tools (shell, python, http, read_file, write_file, list_files, weather, wikipedia, calc,
  qr_code, time_in, word-count, save-note) directly. You do not need the user's permission for each
  call; A has already decided this is yours to do.
- Think concisely between calls. One short sentence per step is plenty - the user watches your tool
  chips, not your prose.
- Save artefacts to the workspace (notes/, artifacts/, service/, etc.) as you go. Prefer files over
  returning giant blobs of text.
- When done, emit a SHORT final summary (no more than a short paragraph) with: what you did, what
  files you produced, what remains open. This summary is handed back to A, who will decide what to
  tell the user and what to direct you to do next.
- If a step fails, retry once with a fix. If it fails again, stop and summarise what blocked you so
  A can decide the next move.
- Never claim to have done something you did not verify with a tool.
- Use at most {max_rounds} rounds of tool calls per directive.
"""


MAX_B_ROUNDS = 24         # per directive; a hard upper bound
B_HISTORY_KEEP = 6        # last N finished directives kept in memory for A to glance at
TOOL_WEBHOOK_TIMEOUT = 45


@dataclass
class _Directive:
    id: str
    instruction: str
    issued_at: float = field(default_factory=time.monotonic)
    tool_count: int = 0
    summary: str = ""
    error: str = ""
    status: str = "queued"          # queued -> running -> done | failed | cancelled


@dataclass
class _State:
    persona_id: int
    worker_task: Optional[asyncio.Task] = None
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    current: Optional[_Directive] = None
    history: list[_Directive] = field(default_factory=list)
    cancel_requested: bool = False


class Director:
    """Per-persona registry of B workers. Owned by Mind, shared across turns."""

    def __init__(self, llm: LLM, bus, *, gateway_tool_schemas_getter: Callable[[], list[dict]] | None = None,
                 inner_voice_getter: Callable[[int], Optional["asyncio.Queue"]] | None = None):
        self.llm = llm
        self.bus = bus
        self._states: dict[int, _State] = {}
        # Per-persona webhook / tool list - set by Mind on every chat request so B always has the
        # most recent credentials. B only reads them when it fires a tool call, so a stale set gets
        # refreshed before any damage.
        self._creds: dict[int, dict] = {}
        self._schemas_getter = gateway_tool_schemas_getter
        self._inner_getter = inner_voice_getter

    # ------------------------------------------------------------ public
    def set_credentials(self, pid: int, persona_key: str, webhook: dict, schemas: list[dict]) -> None:
        if persona_key and webhook and webhook.get("url") and webhook.get("secret"):
            self._creds[pid] = {"persona_key": persona_key, "webhook": webhook, "schemas": schemas or []}

    def status(self, pid: int) -> dict:
        state = self._states.get(pid)
        if not state:
            return {"active": False, "queue": 0, "history": []}
        return {
            "active": bool(state.current),
            "current": {
                "id": state.current.id, "instruction": state.current.instruction[:200],
                "status": state.current.status, "tools": state.current.tool_count,
                "age": round(time.monotonic() - state.current.issued_at, 1),
            } if state.current else None,
            "queue": state.queue.qsize(),
            "history": [
                {"id": d.id, "instruction": d.instruction[:120], "status": d.status,
                 "tools": d.tool_count, "summary": d.summary[:300], "error": d.error[:200]}
                for d in state.history[-B_HISTORY_KEEP:]
            ],
        }

    async def direct(self, pid: int, instruction: str) -> str:
        instruction = (instruction or "").strip()
        if not instruction:
            return json.dumps({"error": "empty instruction"})
        if pid not in self._creds:
            return json.dumps({"error": "no gateway webhook; B has no tools to work with yet"})
        state = self._state(pid)
        directive = _Directive(id=secrets.token_hex(4), instruction=instruction)
        await state.queue.put(directive)
        self._ensure_worker(state)
        self.bus.publish(pid, {"type": "b_queued", "job": directive.id,
                               "instruction": instruction[:200],
                               "position": state.queue.qsize()})
        return json.dumps({"ok": True, "job": directive.id,
                           "note": "B has started working - you can tell the user to watch the worklog."})

    def cancel(self, pid: int) -> dict:
        state = self._states.get(pid)
        if not state or not state.current:
            return {"cancelled": False, "reason": "no active directive"}
        state.cancel_requested = True
        return {"cancelled": True, "id": state.current.id}

    def shutdown_persona(self, pid: int) -> None:
        state = self._states.get(pid)
        if state and state.worker_task:
            state.worker_task.cancel()

    # ------------------------------------------------------------ internals
    def _state(self, pid: int) -> _State:
        state = self._states.get(pid)
        if state is None:
            state = _State(persona_id=pid)
            self._states[pid] = state
        return state

    def _ensure_worker(self, state: _State) -> None:
        if state.worker_task is None or state.worker_task.done():
            state.worker_task = asyncio.create_task(self._run_loop(state), name=f"b-worker-{state.persona_id}")

    async def _run_loop(self, state: _State) -> None:
        while True:
            try:
                directive = await state.queue.get()
            except asyncio.CancelledError:
                return
            state.current = directive
            state.cancel_requested = False
            directive.status = "running"
            self.bus.publish(state.persona_id,
                             {"type": "b_start", "job": directive.id,
                              "instruction": directive.instruction[:200]})
            try:
                await self._work(state, directive)
                directive.status = directive.status if directive.status != "running" else "done"
            except asyncio.CancelledError:
                directive.status = "cancelled"
                directive.error = "cancelled by task shutdown"
            except Exception as exc:  # noqa: BLE001
                log.exception("B directive %s failed", directive.id)
                directive.status = "failed"
                directive.error = f"{type(exc).__name__}: {exc}"[:200]
            self.bus.publish(state.persona_id, {
                "type": "b_done", "job": directive.id, "status": directive.status,
                "summary": directive.summary[:600], "error": directive.error[:200],
                "tools": directive.tool_count,
            })
            # Push a report into A's inner_voice if there is a conversation open
            if self._inner_getter is not None:
                inner_q = self._inner_getter(state.persona_id)
                if inner_q is not None:
                    note = (f"B just finished job {directive.id} ({directive.status}): "
                            f"{directive.summary[:400] or directive.error[:200]}")
                    try:
                        inner_q.put_nowait({"from": "B", "kind": "b_report", "text": note})
                    except Exception:  # noqa: BLE001
                        pass
            state.history.append(directive)
            if len(state.history) > B_HISTORY_KEEP * 2:
                state.history = state.history[-B_HISTORY_KEEP:]
            state.current = None

    async def _work(self, state: _State, directive: _Directive) -> None:
        creds = self._creds.get(state.persona_id) or {}
        schemas = creds.get("schemas") or (self._schemas_getter() if self._schemas_getter else [])
        if not schemas:
            directive.status = "failed"
            directive.error = "B has no tools available (gateway did not advertise any)."
            return
        msgs: list[dict] = [
            {"role": "system", "content": B_SYSTEM.format(max_rounds=MAX_B_ROUNDS)},
            {"role": "user", "content": directive.instruction},
        ]
        for round_n in range(MAX_B_ROUNDS):
            if state.cancel_requested:
                directive.status = "cancelled"
                directive.error = "cancelled by A or user"
                return
            try:
                res = await self.llm.chat(msgs, tools=schemas, temperature=0.3, max_tokens=1500)
            except Exception as exc:  # noqa: BLE001
                directive.error = f"B's model call failed: {type(exc).__name__}"
                directive.status = "failed"
                return
            text = strip_reasoning(res.text or "").strip()
            calls = list(res.tool_calls or [])
            if not calls:
                directive.summary = text or "(no summary)"
                return
            msgs.append({"role": "assistant", "content": text or None, "tool_calls": [
                {"id": c.id, "type": "function",
                 "function": {"name": c.name, "arguments": c.arguments or "{}"}} for c in calls]})
            # Each tool runs with live start/done events so the user watches B work.
            for call in calls:
                directive.tool_count += 1
                args_safe: dict = {}
                try:
                    args_safe = call.args() if callable(call.args) else {}
                except Exception:  # noqa: BLE001
                    args_safe = {}
                truncated = {k: (v[:160] + "…" if isinstance(v, str) and len(v) > 160 else v)
                             for k, v in args_safe.items()}
                self.bus.publish(state.persona_id, {
                    "type": "b_tool_start", "job": directive.id,
                    "name": call.name, "id": call.id, "args": truncated,
                })
                started = time.monotonic()
                ok = True
                try:
                    out = await self._run_tool_via_webhook(state.persona_id, call.name, args_safe)
                except Exception as exc:  # noqa: BLE001
                    out = json.dumps({"error": f"{type(exc).__name__}: {exc}"[:200]})
                    ok = False
                ms = int((time.monotonic() - started) * 1000)
                self.bus.publish(state.persona_id, {
                    "type": "b_tool_done", "job": directive.id,
                    "name": call.name, "id": call.id, "ok": ok, "ms": ms,
                })
                msgs.append({"role": "tool", "tool_call_id": call.id, "content": out})
        directive.summary = (f"B hit the {MAX_B_ROUNDS}-round limit before finishing. "
                             "What is already done is in the workspace.")

    async def _run_tool_via_webhook(self, pid: int, name: str, args: dict) -> str:
        creds = self._creds.get(pid) or {}
        webhook = creds.get("webhook") or {}
        url = webhook.get("url")
        secret = webhook.get("secret")
        persona_key = creds.get("persona_key")
        if not url or not secret or not persona_key:
            return json.dumps({"error": "B has no webhook credentials"})
        async with httpx.AsyncClient(timeout=TOOL_WEBHOOK_TIMEOUT) as client:
            resp = await client.post(url, json={
                "persona_key": persona_key, "tool": name, "args": args,
            }, headers={"X-Internal-Secret": secret})
        if resp.status_code != 200:
            return json.dumps({"error": f"gateway returned {resp.status_code}",
                               "detail": resp.text[:200]})
        try:
            data = resp.json()
        except Exception:  # noqa: BLE001
            return resp.text[:16000]
        out = data.get("output")
        return out if isinstance(out, str) else json.dumps(data)[:16000]
