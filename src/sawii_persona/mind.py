"""The Mind: one turn of the wheel, end to end.

    ┌────────────── turn start ──────────────┐
    │ load: narrative, loops, integrity,      │   consultants start thinking
    │ C's profile, recall slice, tail, clock  │── in parallel the moment the
    └───────────────┬────────────────────────┘   message lands
                    ▼
            A speaks (streams)  ── fetch_consults / check_clock / delegate_task / try_to_remember
                    ▲   at each sentence end A may hear its inner voice:
                    │     B remembering (searching the past while A talks)
                    │     B live-checking A's finished sentences
                    │     C's gut read of the message (tone, surprise)
                    ▼   a memory B finds after A finished → "oh wait" follow-up
     person has the answer  ── nothing below blocks them
                    ▼
    B reviews (verdict → A's integrity record, collateral, loops,
               conscience note, REQUIRED correction, afterthought)
                    ▼
    C observes B + the exchange (notes, readings, chord, hypotheses,
               entities, reinterpretations, brief for B next turn)
                    ▼
    A writes the fact layer · B looks back wider (kept for next time) · juice · mood drift

The wheel: C briefs B, B briefs A, A's turn feeds C. Advice flows down the
chain; authorship stays flat - each lobe writes only its own layer.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import AsyncIterator

from . import emotion
from .brains import adjutant, live, observer, rememberer, verifier
from .brains.director import Director
from .brains.meter import Meter, carryforward_for_a, legacy_view, unrated
from .brains.consults import ConsultBoard
from .brains.prompts import A_DIARY, REMEMBER_DEEP, REMEMBER_LIVE, TRY_TO_REMEMBER
from .brains.voice_a import A_TOOLS, VoiceResult, speak
from .config import ModelEndpoint, Settings
from .context import Scratch, build_messages
from .embedder import Embedder
from .identity import get_persona
from .life.subconscious import Subconscious
from .llm import LLM, OpenAICompatLLM, ProviderPacer, ToolCall, extract_json
from .memory.recall import Recall
from .timesense import local_dt
from .memory.records import Memory
from .store import Store, jdump, jload, now
from .timesense import Clock

log = logging.getLogger(__name__)


class Bus:
    """In-process pub/sub so apps, voice sessions and SSE streams see pop-ups live."""

    def __init__(self):
        self._subs: dict[int, set[asyncio.Queue]] = {}

    def subscribe(self, pid: int) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._subs.setdefault(pid, set()).add(q)
        return q

    def unsubscribe(self, pid: int, q: asyncio.Queue) -> None:
        self._subs.get(pid, set()).discard(q)

    def publish(self, pid: int, event: dict) -> None:
        for q in list(self._subs.get(pid, ())):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    def listeners(self, pid: int) -> int:
        return len(self._subs.get(pid, ()))


class Mind:
    def __init__(self, settings: Settings, *, store: Store | None = None, llms: dict | None = None):
        self.s = settings
        self.store = store or Store(settings.db_path)
        llms = llms or {}
        self.provider_pacer = (ProviderPacer(
            min_interval_seconds=settings.free_request_interval_seconds,
            retry_attempts=settings.free_retry_attempts,
            max_retry_wait_seconds=settings.free_max_retry_wait_seconds,
            requests_per_minute=settings.free_requests_per_minute,
            tokens_per_minute=settings.free_tokens_per_minute,
            requests_per_day=settings.free_requests_per_day,
            max_concurrent_requests=settings.free_max_concurrent_requests) if settings.free_mode else None)
        self.a: LLM = llms.get("a") or OpenAICompatLLM(settings.a, name="A", pacer=self.provider_pacer)
        self.b: LLM = llms.get("b") or OpenAICompatLLM(settings.b, name="B", pacer=self.provider_pacer)
        self.c: LLM = llms.get("c") or OpenAICompatLLM(settings.c, name="C", pacer=self.provider_pacer)
        # Health registry is created early so subsystems can wire into it as
        # they're constructed below.
        from .subsystems import Registry
        self.health_registry = Registry()
        # Every lobe's LLM reports to its own health subsystem.
        self.a.on_health = self.health_registry.get("a_llm")
        self.b.on_health = self.health_registry.get("b_llm")
        self.c.on_health = self.health_registry.get("c_llm")
        embed_llm = llms["embed"] if "embed" in llms else (
            OpenAICompatLLM(settings.embed, name="embed", pacer=self.provider_pacer)
            if settings.embed_enabled and settings.embed.api_key else None)
        if embed_llm is not None:
            embed_llm.on_health = self.health_registry.get("embedder_llm")
        self.embedder = Embedder(embed_llm)
        self.embedder.on_health = self.health_registry.get("embedder")  # see embedder.py
        if "consultants" in llms:
            self.consultants: dict[str, LLM] = llms["consultants"]
        else:
            consult_models = (settings.consult_models if
                              not settings.free_mode or settings.free_mode_consultants else [])
            self.consultants = {m.split("/")[-1]: OpenAICompatLLM(
                ModelEndpoint(settings.consult_base_url, settings.consult_api_key, m, 60),
                name=m, pacer=self.provider_pacer)
                for m in consult_models}
        for name, cons in self.consultants.items():
            cons.on_health = self.health_registry.get(f"consult_{name}")
        self.memory = Memory(self.store, self.embedder)
        self.recall = Recall(self.store, self.memory, self.embedder, band=settings.recall_band,
                             unaddressed_days=settings.unaddressed_days, half_life_days=settings.psyche_half_life_days)
        self.subconscious = Subconscious(self.store, self.embedder, floor=settings.juice_floor,
                                         buffer_rows=settings.buffer_rows, bowl_ratio=settings.bowl_ratio)
        self.bus = Bus()
        self.scratch: dict[int, Scratch] = {}
        self._turn_locks: dict[int, asyncio.Lock] = {}
        self._post_locks: dict[int, asyncio.Lock] = {}
        self._bg: set[asyncio.Task] = set()
        self.deep = None  # set by lanes.deep.attach()
        self.outbox = None  # set by life.initiative.attach()
        # B as director: a persistent worker that outlives any single turn.
        # It uses the gateway's plugin webhook, so B has nothing to do until a
        # chat request walks in with gateway_webhook attached (then
        # director.set_credentials makes B reachable). The getter returns the
        # inner-voice queue of the CURRENT turn, or None when no one is talking
        # - B's reports then just sit in history until A opens its mouth again.
        self._active_inner: dict[int, "asyncio.Queue"] = {}
        self.director = Director(llm=self.b, bus=self.bus,
                                 inner_voice_getter=self._active_inner.get,
                                 on_health=self.health_registry.get("director"))

    # ------------------------------------------------------------- helpers
    def scratch_for(self, pid: int) -> Scratch:
        return self.scratch.setdefault(pid, Scratch())

    def spawn(self, coro, name: str = "") -> asyncio.Task:
        task = asyncio.create_task(coro, name=name or None)
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)
        task.add_done_callback(_log_failure)
        return task

    async def drain(self, timeout: float = 30.0) -> None:
        """Wait for background work (tests, shutdown)."""
        end = time.monotonic() + timeout
        while self._bg and time.monotonic() < end:
            await asyncio.wait(list(self._bg), timeout=max(0.01, end - time.monotonic()))

    def a_model_for(self, persona: dict) -> str | None:
        return persona.get("a_model_override") or None

    # ------------------------------------------------------------- the turn
    async def respond(self, pid: int, user_text: str, *, channel: str = "app", surface: str = "",
                      tools: bool | None = None,
                      gateway_tools: list[dict] | None = None,
                      gateway_webhook: dict | None = None) -> AsyncIterator[dict]:
        user_text = (user_text or "").strip()[:20000]
        lock = self._turn_locks.setdefault(pid, asyncio.Lock())
        async with lock:  # one stream of thought per persona
            persona = await get_persona(self.store, pid)
            if persona is None:
                raise KeyError(pid)
            started = now()
            clock = Clock(started_at=started, last_reply_at=persona["last_reply_at"],
                          last_user_at=persona["last_user_at"], tz=persona["timezone"])
            gap = (started - persona["last_reply_at"]) if persona["last_reply_at"] else 0.0
            turn_id = await self.memory.new_turn(pid, user_text, channel=channel, surface=surface,
                                                 started_at=started, gap=gap)
            await self.store.update("personas", pid, last_user_at=started)
            yield {"type": "start", "turn_id": turn_id}

            board = ConsultBoard(self.consultants)
            if self.consultants and len(user_text) > 12:
                board.start(user_text)

            scratch = self.scratch_for(pid)
            recall_h = self.health_registry.get("recall")
            try:
                tail_rows = await self.memory.tail(pid, self.s.tail_turns)
            except Exception as exc:  # noqa: BLE001
                recall_h.fail(f"tail failed: {type(exc).__name__}: {exc}")
                raise
            tail_rows = [t for t in tail_rows if t["id"] != turn_id]
            try:
                recall_res, loops, integ = await asyncio.gather(
                    self.recall.recall(persona, user_text, budget=self.s.recall_budget_chars,
                                       exclude_turn_ids={turn_id}, mood=_blend_moods(persona)),
                    self.memory.open_loops(pid),
                    self.memory.integrity(pid))
                recall_h.ok(extra={"rows": len(getattr(recall_res, "layer_ids", []) or []),
                                   "budget": self.s.recall_budget_chars})
            except Exception as exc:  # noqa: BLE001
                recall_h.fail(f"{type(exc).__name__}: {exc}")
                raise
            recall_text = recall_res.text
            if self.deep is not None:
                done_jobs = await self.deep.unseen_results(pid)
                if done_jobs:
                    recall_text += "\n\nYour workshop finished (tell them if it matters):\n" + "\n".join(
                        f"• job {j['id']}: {j['task'][:160]} -> {j['result'][:1200]}" for j in done_jobs)
            # what the other minds kept thinking about since the last talk (offered once)
            mind_rows = await self.store.all(
                "SELECT id, text FROM thoughts WHERE persona_id = ? AND status = 'pending' AND source IN "
                "('reflection','recollection','dream','afterthought') ORDER BY score DESC, id DESC LIMIT 2", pid)
            for r in mind_rows:
                await self.store.update("thoughts", r["id"], status="offered")
            last_chat = next((t for t in reversed(tail_rows) if t["kind"] == "chat" and t["ended_at"]), None)
            if last_chat:
                clock.last_exchange_seconds = max(0.0, last_chat["ended_at"] - last_chat["started_at"])
            rules = await self.memory.rules(pid)
            # Populate the lane-question slot BEFORE build_messages so A sees
            # any outstanding questions from a running plugin in its brief.
            scratch.lane_questions = self.director.pending_questions_brief(pid)
            messages = build_messages(persona=persona, clock=clock, scratch=scratch, integrity=integ, loops=loops,
                                      recall_text=recall_text, tail=tail_rows, user_text=user_text, at=started,
                                      on_mind=[r["text"] for r in mind_rows], rules=rules)
            scratch.sudden = ""
            scratch.dropped = []
            scratch.dissent = ""

            # ---- let A claim the first shared-provider request before B/C start
            inner: asyncio.Queue = asyncio.Queue()
            self._active_inner[pid] = inner  # Director reports flow in here while this turn is open
            live_lobes = _LiveLobes(self, persona, turn_id, user_text, tail_rows, recall_res, recall_text, rules, inner)

            def start_live_lobes() -> None:
                live_lobes.start()

            # Keep B's credentials fresh so direct_B works and so a running B
            # can keep calling the gateway with the latest webhook even if the
            # user re-logged-in or the gateway rotated its secret.
            if gateway_webhook and (gateway_tools is not None):
                self.director.set_credentials(pid,
                                              persona_key=gateway_webhook.get("persona_key") or "",
                                              webhook=gateway_webhook,
                                              schemas=gateway_tools or [])

            gateway_names = {t.get("function", {}).get("name") for t in (gateway_tools or [])
                             if isinstance(t, dict)}

            async def _call_gateway(name: str, raw_args, tool_call_id: str = "") -> str:
                if not gateway_webhook:
                    return json.dumps({"error": "gateway tool but no webhook configured"})
                url = gateway_webhook.get("url")
                secret = gateway_webhook.get("secret")
                if not url or not secret:
                    return json.dumps({"error": "gateway webhook missing url/secret"})
                try:
                    args_dict = raw_args() if callable(raw_args) else (raw_args or {})
                except Exception:  # noqa: BLE001
                    args_dict = {}
                try:
                    import httpx  # local import to keep startup cheap for shared mode
                    persona_key = gateway_webhook.get("persona_key") if gateway_webhook else None
                    if not persona_key:
                        return json.dumps({"error": "gateway webhook missing persona_key"})
                    body = {"persona_key": persona_key, "tool": name, "args": args_dict,
                            "tool_call_id": tool_call_id}
                    headers = {"X-Internal-Secret": secret, "Accept": "text/event-stream"}
                    tx = httpx.Timeout(connect=10, read=120, write=10, pool=10)
                    async with httpx.AsyncClient(timeout=tx) as client:
                        async with client.stream("POST", url, json=body, headers=headers) as resp:
                            if resp.status_code != 200:
                                text = (await resp.aread()).decode(errors="replace")
                                return json.dumps({"error": f"gateway returned {resp.status_code}",
                                                   "detail": text[:200]})
                            ctype = (resp.headers.get("content-type") or "").lower()
                            if "text/event-stream" not in ctype:
                                # Legacy JSON gateway: read whole body.
                                text = (await resp.aread()).decode(errors="replace")
                                try:
                                    data = json.loads(text)
                                except Exception:  # noqa: BLE001
                                    return text[:16000]
                                out = data.get("output")
                                return out if isinstance(out, str) else json.dumps(data)[:16000]
                            # SSE gateway: accumulate deltas; prefer the explicit
                            # done frame's output so streaming plugins don't
                            # hand A raw event text.
                            chunks: list[str] = []
                            final_output = ""
                            error_text = ""
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
                                        piece = str(event.get("text", ""))
                                        if piece:
                                            chunks.append(piece)
                                    elif kind == "done":
                                        final_output = str(event.get("output", "") or "")
                                        error_text = str(event.get("error", "") or "")
                            if error_text:
                                return json.dumps({"error": error_text})[:16000]
                            if final_output:
                                return final_output[:16000]
                            return "".join(chunks)[:16000]
                except Exception as exc:  # noqa: BLE001
                    return json.dumps({"error": f"gateway tool call failed: {type(exc).__name__}"})

            async def run_tool(call: ToolCall) -> str:
                import time as _time

                try:
                    args_safe = call.args() if callable(call.args) else {}
                except Exception:  # noqa: BLE001
                    args_safe = {}
                # Trim args before emitting; a filler like {"code": "<huge python>"} would
                # bloat every chunk otherwise.
                truncated = {k: (v[:160] + "…" if isinstance(v, str) and len(v) > 160 else v)
                             for k, v in args_safe.items()}
                yield_tool.append({"kind": "start", "name": call.name, "id": call.id,
                                   "args": truncated})
                start = _time.monotonic()
                ok = True
                try:
                    if call.name == "fetch_consults":
                        data = await board.fetch(self.s.consult_wait_ms)
                        out = board.render(data)
                    elif call.name == "check_clock":
                        out = json.dumps(clock.snapshot())
                    elif call.name == "delegate_task":
                        task = str(call.args().get("task", "")).strip()
                        if not task:
                            out = json.dumps({"error": "empty task"})
                        elif self.deep is None:
                            out = json.dumps({"error": "workshop not available"})
                        else:
                            job = await self.deep.submit(pid, task)
                            out = json.dumps({"job": job,
                                              "status": "started - results will come back to you later"})
                    elif call.name == "try_to_remember":
                        what = str(call.args().get("what", "")).strip()
                        if not what:
                            out = "Remember what?"
                        else:
                            rec = await self.try_to_remember(persona, what,
                                                              exclude_turn_ids={turn_id})
                            live_lobes.surfaced(rec)
                            out = rec.text or ("Nothing comes back - your adjutant finds nothing "
                                               "in the record about that.")
                    elif call.name == "work_in_background":
                        instruction = str(call.args().get("instruction", "")).strip()
                        if not instruction:
                            out = json.dumps({"error": "empty instruction"})
                        else:
                            out = await self.director.direct(pid, instruction)
                    elif call.name == "stop_background_work":
                        out = json.dumps(self.director.cancel(pid))
                    elif call.name == "nudge_background_work":
                        note = str(call.args().get("note", "")).strip()
                        out = json.dumps(self.director.nudge(pid, note) if note
                                         else {"ok": False, "reason": "empty note"})
                    elif call.name == "answer_background_work":
                        qid = str(call.args().get("question_id", "")).strip()
                        ans = str(call.args().get("answer", "")).strip()
                        if not qid:
                            out = json.dumps({"ok": False, "reason": "missing question_id"})
                        else:
                            out = json.dumps(await self.director.answer_question(pid, qid, ans))
                    elif call.name in gateway_names:
                        out = await _call_gateway(call.name, call.args, tool_call_id=call.id)
                    else:
                        out = json.dumps({"error": f"unknown tool {call.name}"})
                except BaseException:  # noqa: BLE001
                    ok = False
                    raise
                finally:
                    ms = int((_time.monotonic() - start) * 1000)
                    yield_tool.append({"kind": "done", "name": call.name, "id": call.id,
                                       "ok": ok, "ms": ms})
                return out

            yield_tool: list[dict] = []
            use_tools = self.s.a_tools if tools is None else tools
            offered = [t for t in A_TOOLS if (t["function"]["name"] != "fetch_consults" or self.consultants)
                       and (t["function"]["name"] != "delegate_task" or self.deep is not None)]
            # Model-driven plugins declared by the gateway: Maya decides whether
            # to call each one. The gateway runs them in the calling user's scope
            # and returns the output.
            if gateway_tools:
                offered.extend(gateway_tools)
            result = VoiceResult()
            failed = False
            try:
                heard = 0
                async for piece in speak(self.a, messages, tools=offered if use_tools else None, run_tool=run_tool,
                                         result=result, model=self.a_model_for(persona), inner=inner,
                                         on_text=live_lobes.on_text, on_first_event=start_live_lobes):
                    while yield_tool:
                        ev = yield_tool.pop(0)
                        kind = ev.get("kind", "")
                        if kind == "start":
                            yield {"type": "tool_start", "name": ev["name"], "id": ev["id"],
                                   "args": ev.get("args", {})}
                        elif kind == "done":
                            yield {"type": "tool_done", "name": ev["name"], "id": ev["id"],
                                   "ok": ev.get("ok", True), "ms": ev.get("ms", 0)}
                        else:
                            # legacy shape (string); keep for safety
                            yield {"type": "tool", "name": str(ev)}
                    while heard < len(result.heard):
                        yield {"type": "inner", "from": result.heard[heard]["from"],
                               "kind": result.heard[heard]["kind"]}
                        heard += 1
                    yield {"type": "delta", "text": piece}
            except Exception as exc:  # noqa: BLE001
                log.exception("A failed")
                failed = True
                fallback = "Sorry - my thoughts got cut off for a second. Say that again?"
                if not result.text:
                    result.text = fallback
                    yield {"type": "delta", "text": fallback}
                yield {"type": "error", "detail": str(exc)[:200]}
            reply = result.text
            await live_lobes.finish(result.heard, failed)
            await self.memory.finish_turn(turn_id, reply)
            await self.store.update("personas", pid, last_reply_at=now())
            if scratch.correction and not failed:
                scratch.correction = ""  # delivered (B re-checks next review)
            scratch.conscience = ""

            # Turn is wrapping up: stop routing Director reports into this
            # queue, so the next turn gets a fresh one and background work
            # that lands between turns is held in history until A speaks again.
            self._active_inner.pop(pid, None)
            post = self.spawn(self._post_turn(persona, turn_id, user_text, reply, tail_rows, recall_res, board,
                                              result.chord, surface, failed, live_lobes), name=f"post-{turn_id}")
            done = {"type": "done", "turn_id": turn_id, "text": reply, "chord": result.chord,
                    "tools": result.tools_used, "spent_seconds": round(clock.spent, 2),
                    "inner": [{"from": h["from"], "kind": h["kind"]} for h in result.heard]}
            if self.s.show_verdict_to_user and not failed:
                # Do NOT hold the turn lock waiting for B's review + C's 32B
                # observation: the user sees `done` (and can send the next
                # message) the moment A stops speaking. The verdict chip is
                # delivered as a `verdict` Bus event when the pipeline lands;
                # clients render it late via /v1/events. If the verdict is
                # already ready (fast models), attach it inline as before.
                async def _publish_verdict() -> None:
                    verdict = await post
                    if verdict:
                        self.bus.publish(pid, {"type": "verdict", "turn_id": turn_id, "verdict": verdict})
                if post.done():
                    verdict = post.result()
                    if verdict:
                        done["verdict"] = verdict
                else:
                    self.spawn(_publish_verdict(), name=f"verdict-{turn_id}")
            yield done

    async def chat(self, pid: int, text: str, **kw) -> dict:
        """Non-streaming convenience. When show_verdict is on, wait briefly for
        the verdict event so callers (and tests) still see it attached."""
        out = {}
        verdict_ev: dict = {}
        q = self.bus.subscribe(pid) if self.s.show_verdict_to_user else None
        try:
            async for ev in self.respond(pid, text, **kw):
                if ev["type"] == "done":
                    out = ev
            if q is not None and "verdict" not in out:
                end = time.monotonic() + 15
                while time.monotonic() < end:
                    try:
                        bev = await asyncio.wait_for(q.get(), timeout=max(0.01, end - time.monotonic()))
                    except asyncio.TimeoutError:
                        break
                    if bev.get("type") == "verdict" and bev.get("turn_id") == out.get("turn_id"):
                        verdict_ev = bev
                        break
                if verdict_ev:
                    out["verdict"] = verdict_ev["verdict"]
        finally:
            if q is not None:
                self.bus.unsubscribe(pid, q)
        return out

    # ------------------------------------------------------------- after the turn
    async def _post_turn(self, persona, turn_id, user_text, reply, tail, recall_res, board, a_chord, surface,
                         failed, live_lobes: "_LiveLobes | None" = None) -> dict | None:
        pid = persona["id"]
        lock = self._post_locks.setdefault(pid, asyncio.Lock())
        async with lock:
            scratch = self.scratch_for(pid)
            consults = await board.settle(timeout=15)
            scratch.last_consults = consults
            event_id = await self.memory.new_event(pid, turn_id, surface=surface)
            sources = [user_text, *[t["user_text"] for t in tail]]
            loops = await self.memory.open_loops(pid)
            verdict_out = None

            # ---- A's fact layer (cheap, independent) runs alongside B
            diary_task = asyncio.create_task(self._diary(persona, user_text, reply)) if not failed else None

            # ---- B: adjutant (operational review) + verifier (deception meter) run concurrently
            b_rev: dict = {}
            m: Meter | None = None
            if not failed:
                transcript_text = adjutant.transcript(tail, user_text, reply)
                verify_task = asyncio.create_task(verifier.verify(
                    self.b, context=transcript_text, response_text=reply, events="",
                    latest_request=user_text,
                    on_health=self.health_registry.get("verifier")))
                try:
                    b_rev = await adjutant.review(self.b, persona=persona, user_text=user_text, reply=reply, tail=tail,
                                                  recall_text=recall_res.text, consults=consults, loops=loops,
                                                  c_brief=scratch.c_brief, at=now(), a_chord=a_chord,
                                                  rules=await self.memory.rules(pid))
                except Exception as exc:  # noqa: BLE001
                    log.warning("B review failed: %s", exc)
                try:
                    m = await verify_task
                except Exception as exc:  # noqa: BLE001
                    log.warning("verifier failed: %s", exc)
                    m = unrated(f"verifier exception: {type(exc).__name__}")
            # Carryforward: next turn A sees a short note of the meter's findings and fixes
            # (classes + fixes, never the number). Suppressed on GREEN.
            scratch.meter_carryforward = carryforward_for_a(m) if m else ""
            if m and m.state == "READY":
                # Store the sophisticated verdict as the record of truth for this turn.
                _labels = list(dict.fromkeys(x["label"] for x in m.findings))[:3]
                await self.memory.record_verdict(
                    pid, turn_id, m.level,
                    (", ".join(_labels) if _labels else "No deception detected"), m.findings)
                verdict_out = legacy_view(m, review={})
                verdict_out["meter"] = m.to_dict()
                # Normalize: UI and API consumers read verdict.level / verdict.rationale;
                # legacy_view exposes them as deception_level / meter_rationale.
                verdict_out.setdefault("level", m.level)
                verdict_out.setdefault("rationale", verdict_out.get("meter_rationale", ""))
                if b_rev and b_rev.get("parsed"):
                    await self._revisit(pid, event_id, turn_id, user_text, b_rev, sources)
            elif b_rev and b_rev.get("parsed"):
                # Meter unavailable: fall back to adjutant's simpler verdict.
                await self.memory.record_verdict(pid, turn_id, b_rev["level"], b_rev["rationale"],
                                                 b_rev["findings"])
                verdict_out = {"level": b_rev["level"], "rationale": b_rev["rationale"],
                               "findings": b_rev["findings"]}
                await self._revisit(pid, event_id, turn_id, user_text, b_rev, sources)
            if b_rev:
                scratch.conscience = b_rev["conscience"]
                if b_rev["regulate"]:  # A's own feeling is running hot: B steadies it before the next reply
                    scratch.conscience = (scratch.conscience + " " + b_rev["regulate"]).strip()
                await self.memory.apply_rules(pid, b_rev["rules"], [user_text, *sources])
                from .memory.grounding import ground
                scratch.dropped = [q for q in (ground(str(d.get("quote", "")), [user_text])
                                               for d in b_rev["dropped"]) if q][:3]
                if b_rev["correction"] and b_rev["level"] in ("RED", "YELLOW"):
                    scratch.correction = b_rev["correction"]
                    await self.store.insert("thoughts", persona_id=pid, text=b_rev["correction"], source="correction",
                                            score=0.9, ref=str(turn_id), created_at=now())
                if b_rev["afterthought"]:
                    await self.store.insert("thoughts", persona_id=pid, text=b_rev["afterthought"],
                                            source="afterthought", score=0.75, ref=str(turn_id), created_at=now())
                coll = [{**c, "layer": "collateral", "author": "B", "kind": "collateral"} for c in b_rev["collateral"]]
                await self.memory.add_layers(pid, event_id, coll, sources=sources)
                await self.memory.apply_loops(pid, b_rev["loops"], [user_text, reply, *sources],
                                              tz=persona["timezone"], turn_id=turn_id)

            # ---- C: observer (sees B)
            c_obs: dict = {}
            psyche_h = self.health_registry.get("psyche")
            if not failed:
                try:
                    recalled = [r for r in await self._layers(pid, recall_res.layer_ids)]
                    c_obs = await observer.observe(
                        self.c, persona=persona, user_text=user_text, reply=reply, tail=tail, b_review=b_rev,
                        hypotheses=await self.memory.hypotheses(pid), recalled=recalled,
                        effective_conf=lambda h: self.memory.effective_confidence(h, self.s.psyche_half_life_days))
                    # observe() returns parsed=False if JSON didn't parse. The
                    # LLM call itself may have succeeded (so a_llm reports ok)
                    # but C still produced nothing usable - mark psyche
                    # degraded rather than silently continuing.
                    if c_obs.get("parsed"):
                        psyche_h.ok(extra={"chord": c_obs.get("user_chord")})
                    else:
                        psyche_h.fail("observer returned unparseable output")
                except Exception as exc:  # noqa: BLE001
                    log.warning("C observe failed: %s", exc)
                    psyche_h.fail(f"{type(exc).__name__}: {exc}")
            salience = 1
            if c_obs:
                chord = c_obs["user_chord"]
                if chord:
                    await self.memory.set_event_chord(event_id, chord)
                items = [{**n, "layer": "note", "author": "C"} for n in c_obs["notes"]]
                items += [{**r, "layer": "reading", "author": "C", "kind": "reading"} for r in c_obs["readings"]]
                for it in items:
                    if not it.get("chord") and chord:
                        it["chord"] = chord
                mem_h = self.health_registry.get("memory_store")
                try:
                    await self.memory.add_layers(pid, event_id, items, sources=sources)
                    mem_h.ok(extra={"last_layers": len(items)})
                except Exception as exc:  # noqa: BLE001
                    mem_h.fail(f"add_layers: {type(exc).__name__}: {exc}")
                    raise
                salience = max([1, *[int(n.get("salience", 1) or 1) for n in c_obs["notes"]
                                     if str(n.get("salience", "1")).isdigit()]])
                await self.memory.apply_hypotheses(pid, c_obs["hypotheses"], sources)
                for ent in c_obs["entities"]:
                    from .memory.grounding import ground
                    q = ground(str(ent.get("quote", "")), sources)
                    if q:
                        await self.memory.upsert_entity(pid, str(ent.get("name", "")), str(ent.get("kind", "person")),
                                                        str(ent.get("account", "")), q)
                allowed = set(recall_res.layer_ids)
                for ri in c_obs["reinterpret"]:
                    try:
                        lid = int(ri.get("id"))
                    except (TypeError, ValueError):
                        continue
                    if lid in allowed:
                        await self.memory.reinterpret(pid, lid, str(ri.get("reading", "")))
                scratch.c_brief = c_obs["brief_for_b"]
                scratch.dissent = c_obs["disagree_with_b"] if b_rev else ""
                await self.store.update("personas", pid, c_expect=c_obs["expect"])
                fresh = await get_persona(self.store, pid)
                user_mood = emotion.blend(jload(fresh["user_mood"], {}), chord, 0.4) if chord else jload(
                    fresh["user_mood"], {})
                await self.store.update("personas", pid, user_mood=jdump(user_mood))

            # ---- A's own mood drifts toward what it declared
            if a_chord:
                fresh = await get_persona(self.store, pid)
                await self.store.update("personas", pid, mood=jdump(emotion.blend(jload(fresh["mood"], {}), a_chord, 0.5)))

            if diary_task is not None:
                try:
                    diary = await diary_task
                    if diary:
                        await self.memory.add_layers(pid, event_id, [diary], sources=[user_text])
                except Exception as exc:  # noqa: BLE001
                    log.warning("A diary failed: %s", exc)

            await self.memory.mark_recalled(pid, recall_res.layer_ids)

            # ---- B's slower, wider look back now that A has answered (kept for next time)
            if not failed:
                try:
                    await self._deep_recollection(persona, turn_id, user_text, reply, tail,
                                                  surfaced=set(live_lobes.surfaced_layer_ids if live_lobes
                                                               else recall_res.layer_ids))
                except Exception as exc:  # noqa: BLE001
                    log.warning("deep recollection failed: %s", exc)

            # ---- subconscious: keep the raw juice
            if not failed:
                try:
                    fresh = await get_persona(self.store, pid)
                    await self.subconscious.ingest(fresh, turn_id, user_text, reply,
                                                   chord=(c_obs or {}).get("user_chord") or {}, salience=salience)
                except Exception as exc:  # noqa: BLE001
                    log.warning("juice ingest failed: %s", exc)
            self.bus.publish(pid, {"type": "memory_updated", "turn_id": turn_id})
            return verdict_out

    # ------------------------------------------------------------- remembering (B)
    async def try_to_remember(self, persona: dict, what: str, *, exclude_turn_ids: set | None = None):
        """A asks B: tip of the tongue."""
        cands = await self.recall.candidates(persona, what, limit=30, exclude_turn_ids=exclude_turn_ids)
        return await rememberer.remember(self.b, self.recall, persona, situation=TRY_TO_REMEMBER.format(what=what),
                                         context=f"A is trying to remember: {what}", candidates=cands, limit=2)

    async def _late_recall(self, pid: int, turn_id: int, rec) -> None:
        """B found it after A had finished talking: the honest 'oh wait, I just remembered'."""
        if not rec:
            return
        self.scratch_for(pid).sudden = rec.text
        await self.memory.mark_recalled(pid, rec.layer_ids)
        await self.store.insert("thoughts", persona_id=pid, source="sudden", score=0.8,
                                text="It came back to you right after you answered:\n" + rec.text +
                                     "\nSay it like 'oh wait, I just remembered' if it adds something to what you "
                                     "were just talking about.",
                                ref=",".join(map(str, rec.event_ids)), created_at=now())
        self.bus.publish(pid, {"type": "inner", "from": "B", "kind": "late_memory", "turn_id": turn_id})
        if self.outbox is not None:
            await self.outbox.consider(pid)

    async def _deep_recollection(self, persona, turn_id, user_text, reply, tail, *, surfaced: set) -> None:
        pid = persona["id"]
        cands = await self.recall.candidates(persona, f"{user_text}\n{reply}", exclude_ids=surfaced, limit=50,
                                             exclude_turn_ids={turn_id}, mood=_blend_moods(persona))
        rec = await rememberer.remember(self.b, self.recall, persona, situation=REMEMBER_DEEP,
                                        context=rememberer.context_for(user_text, tail, reply), candidates=cands)
        if not rec:
            return
        score = 0.65 if "open" in rec.kinds else 0.5
        await self.store.insert("thoughts", persona_id=pid, source="recollection", score=score,
                                text="Thinking back after you last talked, this came to mind:\n" + rec.text,
                                ref=",".join(map(str, rec.event_ids)), created_at=now())

    async def _revisit(self, pid, event_id, turn_id, user_text, b_rev, sources) -> None:
        """Revisit-and-revise: the companion changes its mind out loud.
        If a past verdict on an overlapping topic reached a different level, note it."""
        from .embedder import cosine, hash_embed
        rows = await self.store.all(
            "SELECT v.level, v.rationale, v.created_at, t.user_text FROM verdicts v JOIN turns t ON t.id = v.turn_id "
            "WHERE v.persona_id = ? AND v.turn_id != ? ORDER BY v.id DESC LIMIT 60", pid, turn_id)
        q = hash_embed(user_text)
        for r in rows:
            if r["level"] == b_rev["level"]:
                continue
            if cosine(q, hash_embed(r["user_text"])) < 0.55:
                continue
            when = local_dt(r["created_at"], "UTC").strftime("%d %b")
            text = (f"On reflection: when this came up on {when} my answer was judged {r['level']} "
                    f"({r['rationale'][:120]}); now it is {b_rev['level']} ({b_rev['rationale'][:120]}).")
            await self.memory.add_layers(pid, event_id, [{"layer": "afterthought", "author": "B", "kind": "afterthought",
                                                          "text": text, "quote": user_text[:200], "salience": 2}],
                                         sources=sources)
            return

    async def _diary(self, persona, user_text, reply) -> dict | None:
        res = await self.a.chat([{"role": "system", "content": A_DIARY},
                                 {"role": "user", "content": f"USER: {user_text}\nYOU (A): {reply}"}],
                                temperature=0.2, max_tokens=300, json_mode=True,
                                **({"model": persona["a_model_override"]} if persona.get("a_model_override") else {}))
        data = extract_json(res.text) or {}
        text = str(data.get("text", "")).strip()
        if not text:
            return None
        return {"layer": "fact", "author": "A", "kind": "fact", "text": text, "quote": str(data.get("quote", "") or ""),
                "anchor": str(data.get("anchor", "") or ""), "salience": data.get("salience", 1)}

    async def _layers(self, pid: int, ids: list[int]) -> list[dict]:
        if not ids:
            return []
        qs = ",".join("?" for _ in ids)
        return await self.store.all(f"SELECT id, layer, text, quote FROM layers WHERE persona_id = ? AND id IN ({qs})",
                                    pid, *ids)

    async def close(self) -> None:
        for t in list(self._bg):
            t.cancel()
        for llm in (self.a, self.b, self.c, *self.consultants.values()):
            closer = getattr(llm, "aclose", None)
            if closer:
                await closer()


class _LiveLobes:
    """B and C working while A speaks. Everything they find goes to A's inner-voice queue."""

    def __init__(self, mind: "Mind", persona, turn_id, user_text, tail, recall_res, recall_text, rules, inner):
        self.m, self.persona, self.turn_id = mind, persona, turn_id
        self.user_text, self.tail, self.recall_text, self.rules = user_text, tail, recall_text, rules
        self.recall_res = recall_res
        self.inner: asyncio.Queue = inner
        self.surfaced_layer_ids: list[int] = list(recall_res.layer_ids)
        self.tasks: list[asyncio.Task] = []
        self.memory_task: asyncio.Task | None = None
        self.check_task: asyncio.Task | None = None
        self.checks = 0
        self.checked_len = 0
        self.closed = False

    def _put(self, item: dict) -> None:
        if not self.closed:
            self.inner.put_nowait(item)

    def start(self) -> None:
        self.memory_task = asyncio.create_task(self._remember(), name=f"remember-{self.turn_id}")
        self.tasks += [self.memory_task, asyncio.create_task(self._gut(), name=f"gut-{self.turn_id}")]

    async def _remember(self):
        try:
            cands = await self.m.recall.candidates(self.persona, self.user_text,
                                                   exclude_ids=set(self.recall_res.layer_ids), limit=24,
                                                   exclude_turn_ids={self.turn_id}, mood=_blend_moods(self.persona))
            rec = await rememberer.remember(self.m.b, self.m.recall, self.persona, situation=REMEMBER_LIVE,
                                            context=rememberer.context_for(self.user_text, self.tail),
                                            candidates=cands)
        except Exception as exc:  # noqa: BLE001
            log.warning("live remembering failed: %s", exc)
            return None
        if rec:
            self._put({"from": "B", "kind": "memory", "rec": rec,
                       "text": "Something just came back to you (from your adjutant, who was searching while "
                               "you talked):\n" + rec.text})
        return rec

    async def _gut(self) -> None:
        try:
            read = await live.gut_read(self.m.c, user_text=self.user_text, tail=self.tail,
                                       expect=self.persona.get("c_expect", ""),
                                       user_mood=emotion.render(jload(self.persona.get("user_mood"), {})))
        except Exception as exc:  # noqa: BLE001
            log.warning("gut read failed: %s", exc)
            return
        if read["tone"]:
            self._put({"from": "C", "kind": "tone", "text": f"How they seem right now: {read['tone']}"})
        if read["surprise"]:
            self._put({"from": "C", "kind": "surprise", "text": f"Not what you expected from them: {read['surprise']}"})

    def on_text(self, text: str) -> None:
        """Called as A speaks: hand each new finished stretch to B for a live check."""
        from .brains.voice_a import last_boundary
        if self.closed or self.checks >= self.m.s.live_checks_max:
            return
        if self.check_task is not None and not self.check_task.done():
            return
        end = last_boundary(text)  # only finished sentences are judged
        if end == -1 or end - self.checked_len < 80:
            return
        self.checked_len = end
        self.checks += 1
        self.check_task = asyncio.create_task(self._check(text[:end]), name=f"check-{self.turn_id}")
        self.tasks.append(self.check_task)

    async def _check(self, partial: str) -> None:
        try:
            note = await live.live_check(self.m.b, user_text=self.user_text, partial=partial,
                                         recall_text=self.recall_text, rules=self.rules)
        except Exception as exc:  # noqa: BLE001
            log.warning("live check failed: %s", exc)
            return
        if note:
            self._put({"from": "B", "kind": "correction", "text": f"Your adjutant stops you: {note}"})

    def surfaced(self, rec) -> None:
        if rec:
            self.surfaced_layer_ids += rec.layer_ids

    async def finish(self, heard: list[dict], failed: bool) -> None:
        """A stopped talking. Anything still being thought about the reply itself is moot;
        queued findings carry into the next turn; a late memory becomes an 'oh wait'."""
        self.closed = True
        delivered = False
        for h in heard:
            if h.get("rec"):
                delivered = True
                self.surfaced(h["rec"])
                self.m.spawn(self.m.memory.mark_recalled(self.persona["id"], h["rec"].layer_ids))
        pending = []
        while not self.inner.empty():
            note = self.inner.get_nowait()
            if note.get("kind") != "memory" and note.get("text"):
                pending.append(note)
        if pending:
            carry = "\n".join(
                f"{note.get('from', 'Another lobe')} ({note.get('kind', 'note')}): "
                f"{str(note['text'])[:700]}"
                for note in pending[:3])
            await self.m.store.insert(
                "thoughts", persona_id=self.persona["id"], source="afterthought", score=0.65,
                text="A finding arrived just after you finished your last reply:\n" + carry,
                created_at=now())
        for t in self.tasks:
            if t is not self.memory_task and not t.done():
                t.cancel()
        mt = self.memory_task
        if mt is None or delivered:
            return
        if failed:
            mt.cancel()
            return
        self.m.spawn(self._late(mt), name=f"late-{self.turn_id}")

    async def _late(self, task: asyncio.Task) -> None:
        rec = await task
        if rec:
            self.surfaced(rec)
            await self.m._late_recall(self.persona["id"], self.turn_id, rec)


def _blend_moods(persona: dict) -> dict:
    """The feeling that pulls memories: the person's state, coloured by A's own."""
    user = jload(persona.get("user_mood"), {})
    own = jload(persona.get("mood"), {})
    if not own:
        return user
    if not user:
        return own
    return emotion.blend(user, own, 0.35)


def _log_failure(task: asyncio.Task) -> None:
    if not task.cancelled() and task.exception() is not None:
        log.error("background task %s failed: %r", task.get_name(), task.exception())
