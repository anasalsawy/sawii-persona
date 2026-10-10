"""Background working lane - a persistent worker A hands multi-step jobs to.

From A's point of view this is her own hands doing her own work on a lane
that keeps running after she stops talking. There is no "B" in the words
A uses with the user; "B" is only the internal name for this lobe. User-
facing strings call it "your background work", "the working lane", or
simply never surface the lobe at all - the user sees tool chips streaming
and knows Maya is doing the thing.

The lane uses the gateway's plugins (shell, python, http, read_file,
write_file, weather, …) through the same webhook A uses when model-driven
tools are enabled. Each call publishes b_tool_start / b_tool_done events
on the Bus so the companion-app client renders a live worklog beside the
chat.

This is NOT the OpenHands workshop ("delegate_task"). OpenHands is a heavy
external sandbox for building whole services; the lane is lighter and more
conversational - A directs, the lane runs, the lane reports back, A
directs again, the user watches the whole thing.

One lane per persona. It outlives any single user turn; it survives A
finishing a reply, and it only pauses when (a) the running directive
completes, (b) A sends stop_background_work, or (c) the process is
restarted - in which case the next startup will not resurrect the queue.
Persisting across restarts is a future problem.
"""
from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from ..llm import LLM, ToolCall, strip_reasoning
from .prompts import PLUGIN_GUIDE

log = logging.getLogger(__name__)


B_SYSTEM = """You are the background working self of a companion. The conversational self (the voice)
has already told the user you are on it. Get it done.

Rules:
- You run tools (shell, python, http, read_file, write_file, list_files, weather, wikipedia, calc,
  qr_code, time_in, word-count, save-note) directly. The voice already decided this is yours; you
  do not stop to ask the user.
- Think concisely between calls. One short sentence per step is plenty - the user watches tool
  chips, not your prose.
- Save artefacts to the workspace (notes/, artifacts/, service/, etc.) as you go. Prefer files over
  giant blobs of returned text.
- You and the voice are ONE self. Never refer to the voice as a separate agent; this prose stays
  inside your head.
- When done, emit a SHORT final summary (no more than a short paragraph): what you did, what files
  you produced, what remains open. The voice uses this to tell the user and decide what is next.
- If a step fails, retry once with a fix. If it fails again, stop and summarise what blocked you so
  the voice can decide the next move.
- Never claim to have done something you did not verify with a tool.
- A note from the voice may arrive mid-work as a [note from the voice] line in a tool result's
  place - treat it as a correction or extra context from your own thinking, incorporate it in the
  next step, do not restart.
- Use at most {max_rounds} rounds of tool calls per directive.

Plugin-authoring guide (verbatim from the voice's original manual; consult it
whenever the directive asks you to build or install a plugin):

""" + PLUGIN_GUIDE + """
"""


MAX_B_ROUNDS = 24         # per directive; a hard upper bound
B_HISTORY_KEEP = 6        # last N finished directives kept in memory for A to glance at
# httpx timeouts for the streaming webhook: snap on connect/write, NO cap on
# read (streaming plugins may be alive for minutes — a phone call, a chat
# monitor). Pool/write caps are short to catch stuck sockets.
TOOL_WEBHOOK_CONNECT = 10
TOOL_WEBHOOK_WRITE = 10
TOOL_WEBHOOK_POOL = 10


@dataclass
class _Directive:
    id: str
    instruction: str
    issued_at: float = field(default_factory=time.monotonic)
    tool_count: int = 0
    summary: str = ""
    error: str = ""
    status: str = "queued"          # queued -> running -> done | failed | cancelled
    pending_notes: list = field(default_factory=list)   # whisper_B nudges to fold in next round


@dataclass
class _State:
    persona_id: int
    worker_task: Optional[asyncio.Task] = None
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    current: Optional[_Directive] = None
    history: list[_Directive] = field(default_factory=list)
    cancel_requested: bool = False
    active_tool_call_id: str = ""     # set while a tool is running on the gateway
    # Questions a running plugin asked the voice. Each: {question_id, question,
    # tool_call_id, asked_at, default_if_quiet, answered}. A reads them in its
    # next-turn brief when A was asleep; mid-turn ones also go into inner_voice.
    pending_questions: list = field(default_factory=list)


class Director:
    """Per-persona registry of B workers. Owned by Mind, shared across turns."""

    def __init__(self, llm: LLM, bus, *, gateway_tool_schemas_getter: Callable[[], list[dict]] | None = None,
                 inner_voice_getter: Callable[[int], Optional["asyncio.Queue"]] | None = None,
                 on_health=None):
        self.llm = llm
        self.bus = bus
        self._states: dict[int, _State] = {}
        # Per-persona webhook / tool list - set by Mind on every chat request so B always has the
        # most recent credentials. B only reads them when it fires a tool call, so a stale set gets
        # refreshed before any damage.
        self._creds: dict[int, dict] = {}
        self._schemas_getter = gateway_tool_schemas_getter
        self._inner_getter = inner_voice_getter
        self.on_health = on_health  # SubsystemHealth or None

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

    def nudge(self, pid: int, note: str) -> dict:
        """Inject a mid-work note into the running directive.

        Two delivery paths, both fire together:
          - Round-level: appended to pending_notes, which the lane folds in
            at the top of the next LLM round (between tool calls).
          - Live-whisper: if a specific tool call is running RIGHT NOW on
            the gateway, the note is POSTed to the gateway's
            /api/internal/tool/notes endpoint so the plugin's ctx.listen()
            receives it immediately. This is the inter-lobular channel -
            A→B whispers mid-call without stopping B.

        If no job is running the note is attached to whatever directive
        runs next; if the queue is empty too, nothing happens.
        """
        note = (note or "").strip()
        if not note:
            return {"ok": False, "reason": "empty note"}
        state = self._states.get(pid)
        if not state:
            return {"ok": False, "reason": "no working lane yet"}
        if state.current is not None:
            state.current.pending_notes.append(note)
            self.bus.publish(pid, {"type": "b_nudge", "job": state.current.id, "note": note})
            # Fire the live-whisper if a tool is currently executing.
            if state.active_tool_call_id:
                asyncio.create_task(self._whisper(pid, state.active_tool_call_id, note),
                                    name=f"whisper-{state.current.id}")
            return {"ok": True, "attached_to": state.current.id,
                    "live": bool(state.active_tool_call_id)}
        if state.queue._queue:  # type: ignore[attr-defined]
            state.queue._queue[-1].pending_notes.append(note)  # type: ignore[attr-defined]
            return {"ok": True, "attached_to_queued": True}
        return {"ok": False, "reason": "nothing is working right now"}

    def deliver_question(self, pid: int, question: dict) -> None:
        """A plugin, mid-run on the gateway, is asking A a question. We stash
        it in pending_questions, publish a lane_question Bus event so the
        inter-lobular feed in the UI shows it, and route it into A's
        inner_voice if A is currently in a turn (otherwise it waits for A's
        next turn, when the brief will surface it)."""
        state = self._state(pid)
        row = {
            "question_id": question.get("question_id") or "",
            "question": question.get("question") or "",
            "tool_call_id": question.get("tool_call_id") or "",
            "default_if_quiet": question.get("default_if_quiet") or "",
            "asked_at": time.time(),
            "answered": False,
        }
        state.pending_questions.append(row)
        # Keep the list bounded; drop oldest answered first, then oldest overall.
        if len(state.pending_questions) > 20:
            unanswered = [q for q in state.pending_questions if not q["answered"]]
            answered = [q for q in state.pending_questions if q["answered"]]
            state.pending_questions = (unanswered + answered)[-20:]
        self.bus.publish(pid, {"type": "lane_question", **row})
        # If a conversation is open, drop into inner voice so A hears it mid-sentence
        if self._inner_getter is not None:
            inner_q = self._inner_getter(pid)
            if inner_q is not None:
                try:
                    inner_q.put_nowait({
                        "from": "B", "kind": "lane_question",
                        "text": ("your background work is asking you: "
                                 f"{row['question']} "
                                 f"[call answer_background_work with question_id={row['question_id']!r} "
                                 "to reply]"),
                    })
                except Exception:  # noqa: BLE001
                    pass

    def pending_questions_brief(self, pid: int) -> str:
        """Short text the next-turn brief surfaces so A sees queued questions."""
        state = self._states.get(pid)
        if not state or not state.pending_questions:
            return ""
        rows = [q for q in state.pending_questions if not q["answered"]][-5:]
        if not rows:
            return ""
        lines = ["Your background work has been asking:"]
        for q in rows:
            age = int(time.time() - q["asked_at"])
            lines.append(f"  - [{age}s ago, id={q['question_id']}] {q['question'][:300]}")
        lines.append("Call answer_background_work(question_id, answer) for each one you want "
                     "to address; others can wait or the default will apply at timeout.")
        return "\n".join(lines)

    async def answer_question(self, pid: int, question_id: str, answer: str) -> dict:
        """A called answer_background_work. POST the answer back to the gateway
        so the plugin's ask_voice unblocks, and mark it answered locally."""
        state = self._states.get(pid)
        if not state:
            return {"ok": False, "reason": "no working lane state"}
        row = next((q for q in state.pending_questions if q["question_id"] == question_id), None)
        if row is None:
            return {"ok": False, "reason": "unknown question_id"}
        if row["answered"]:
            return {"ok": False, "reason": "already answered"}
        creds = self._creds.get(pid) or {}
        webhook = creds.get("webhook") or {}
        answer_url = webhook.get("answer_url")
        secret = webhook.get("secret")
        if not answer_url or not secret:
            return {"ok": False, "reason": "gateway answer endpoint not configured"}
        import httpx
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(answer_url, json={
                    "question_id": question_id, "answer": answer or "",
                }, headers={"X-Internal-Secret": secret})
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "reason": f"delivery failed: {type(exc).__name__}"}
        if resp.status_code != 200:
            return {"ok": False, "reason": f"gateway returned {resp.status_code}",
                    "detail": resp.text[:200]}
        row["answered"] = True
        self.bus.publish(pid, {"type": "voice_answer", "question_id": question_id,
                               "tool_call_id": row.get("tool_call_id", ""),
                               "answer": (answer or "")[:400]})
        return {"ok": True}

    async def _whisper(self, pid: int, tool_call_id: str, note: str) -> None:
        """Push a coaching note to the gateway's running plugin via the
        /api/internal/tool/notes endpoint. Best-effort; failure just means
        the live-whisper did not land - the pending_notes path still folds
        the note in at the next round."""
        creds = self._creds.get(pid) or {}
        webhook = creds.get("webhook") or {}
        url = (webhook.get("url") or "").rstrip("/")
        secret = webhook.get("secret") or ""
        if not url or not secret:
            return
        notes_url = url + "/notes"
        import httpx
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                await client.post(notes_url, json={"tool_call_id": tool_call_id, "note": note},
                                  headers={"X-Internal-Secret": secret})
        except Exception as exc:  # noqa: BLE001
            log.warning("whisper failed: %s", exc)

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
            # Push a report into A's inner_voice if there is a conversation open.
            # Phrasing is deliberate: A hears this as her OWN background work
            # resolving, not as a message from an agent. The user never hears
            # the words "worker" or "lane" from A's mouth.
            if self._inner_getter is not None:
                inner_q = self._inner_getter(state.persona_id)
                if inner_q is not None:
                    body = directive.summary[:400] or directive.error[:200] or "(no result)"
                    verb = {"done": "finished",
                            "failed": "hit a wall",
                            "cancelled": "was stopped"}.get(directive.status, directive.status)
                    note = (f"your background work on \"{directive.instruction[:140]}\" {verb}: "
                            f"{body}")
                    try:
                        inner_q.put_nowait({"from": "B", "kind": "background_work",
                                            "text": note})
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
            # .replace, not .format: PLUGIN_GUIDE contains literal braces
            # (JSON SCHEMA examples) that str.format would eat.
            {"role": "system", "content": B_SYSTEM.replace("{max_rounds}", str(MAX_B_ROUNDS))},
            {"role": "user", "content": directive.instruction},
        ]
        for round_n in range(MAX_B_ROUNDS):
            if state.cancel_requested:
                directive.status = "cancelled"
                directive.error = "cancelled by the voice or by the user"
                return
            # Fold in any mid-work nudges from the voice BEFORE this round plans.
            while directive.pending_notes:
                note = directive.pending_notes.pop(0)
                msgs.append({"role": "user",
                             "content": f"[note from the voice] {note}"})
            # Stream B's output: text pieces publish as b_delta so the UI
            # shows B thinking between tool calls, exactly like A's chat
            # bubble streams. Tool calls accumulate and run after the stream.
            text_parts: list[str] = []
            calls_dict: dict[int, dict] = {}
            try:
                stream = self.llm.stream(msgs, tools=schemas, temperature=0.3, max_tokens=1500)
                try:
                    async for ev in stream:
                        kind = ev.get("type")
                        if kind == "text":
                            piece = str(ev.get("text", ""))
                            if piece:
                                text_parts.append(piece)
                                self.bus.publish(state.persona_id, {
                                    "type": "b_delta", "job": directive.id, "text": piece,
                                })
                        elif kind == "tool_call":
                            tc = ev.get("call")
                            if tc is not None:
                                calls_dict.setdefault(id(tc), tc)
                finally:
                    await stream.aclose()
            except Exception as exc:  # noqa: BLE001
                directive.error = f"working lane model call failed: {type(exc).__name__}"
                directive.status = "failed"
                if self.on_health is not None:
                    self.on_health.fail(f"B stream failed: {type(exc).__name__}: {exc}")
                return
            text = strip_reasoning("".join(text_parts)).strip()
            calls = list(calls_dict.values())
            if not calls:
                directive.summary = text or "(no summary)"
                if self.on_health is not None:
                    self.on_health.ok(extra={"rounds": round_n + 1})
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
                state.active_tool_call_id = call.id  # live-whisper routing target
                try:
                    out = await self._run_tool_via_webhook(state.persona_id, call.name, args_safe,
                                                           job_id=directive.id, tool_id=call.id)
                except Exception as exc:  # noqa: BLE001
                    out = json.dumps({"error": f"{type(exc).__name__}: {exc}"[:200]})
                    ok = False
                finally:
                    state.active_tool_call_id = ""
                ms = int((time.monotonic() - started) * 1000)
                self.bus.publish(state.persona_id, {
                    "type": "b_tool_done", "job": directive.id,
                    "name": call.name, "id": call.id, "ok": ok, "ms": ms,
                })
                msgs.append({"role": "tool", "tool_call_id": call.id, "content": out})
        directive.summary = (f"B hit the {MAX_B_ROUNDS}-round limit before finishing. "
                             "What is already done is in the workspace.")

    async def _run_tool_via_webhook(self, pid: int, name: str, args: dict,
                                     job_id: str, tool_id: str) -> str:
        """POST the tool call, read the SSE response, publish each chunk as a
        b_tool_delta so the chip label can grow live. Returns the final output
        (concatenation of deltas) to feed back into B's LLM as the tool msg.
        """
        creds = self._creds.get(pid) or {}
        webhook = creds.get("webhook") or {}
        url = webhook.get("url")
        secret = webhook.get("secret")
        persona_key = creds.get("persona_key")
        if not url or not secret or not persona_key:
            return json.dumps({"error": "B has no webhook credentials"})
        import httpx  # local import: tests run without the httpx dep
        body = {"persona_key": persona_key, "tool": name, "args": args,
                "tool_call_id": tool_id}
        headers = {"X-Internal-Secret": secret, "Accept": "text/event-stream"}
        chunks: list[str] = []
        final_output: str = ""
        error_text: str = ""
        tx = httpx.Timeout(connect=TOOL_WEBHOOK_CONNECT, read=None,
                           write=TOOL_WEBHOOK_WRITE, pool=TOOL_WEBHOOK_POOL)
        async with httpx.AsyncClient(timeout=tx) as client:
            async with client.stream("POST", url, json=body, headers=headers) as resp:
                if resp.status_code != 200:
                    text = await resp.aread()
                    return json.dumps({"error": f"gateway returned {resp.status_code}",
                                       "detail": text.decode(errors="replace")[:200]})
                ctype = (resp.headers.get("content-type") or "").lower()
                if "text/event-stream" not in ctype:
                    # Legacy JSON response from an older gateway: read and return as-is.
                    text = (await resp.aread()).decode(errors="replace")
                    try:
                        data = json.loads(text)
                        out = data.get("output")
                        return out if isinstance(out, str) else json.dumps(data)[:16000]
                    except Exception:  # noqa: BLE001
                        return text[:16000]
                buffer = ""
                async for raw in resp.aiter_text():
                    buffer += raw
                    while "\n\n" in buffer:
                        frame, buffer = buffer.split("\n\n", 1)
                        line = frame.strip()
                        if not line.startswith("data:"):
                            continue
                        try:
                            event = json.loads(line[5:].strip())
                        except Exception:  # noqa: BLE001
                            continue
                        kind = event.get("type")
                        if kind == "delta":
                            text = str(event.get("text", ""))
                            if not text:
                                continue
                            chunks.append(text)
                            self.bus.publish(pid, {
                                "type": "b_tool_delta", "job": job_id, "id": tool_id,
                                "name": name, "text": text,
                            })
                        elif kind == "done":
                            final_output = str(event.get("output", "") or "")
                            error_text = str(event.get("error", "") or "")
        if error_text:
            return json.dumps({"error": error_text})[:16000]
        if final_output:
            return final_output[:16000]
        return "".join(chunks)[:16000]
