"""The Mind: one turn of the wheel, end to end.

    ┌────────────── turn start ──────────────┐
    │ load: narrative, loops, integrity,      │   consultants start thinking
    │ C's profile, recall slice, tail, clock  │── in parallel the moment the
    └───────────────┬────────────────────────┘   message lands
                    ▼
            A speaks (streams)  ── fetch_consults / check_clock / delegate_task
                    ▼
     person has the answer  ── nothing below blocks them
                    ▼
    B reviews (verdict → A's integrity record, collateral, loops,
               conscience note, REQUIRED correction, afterthought)
                    ▼
    C observes B + the exchange (notes, readings, chord, hypotheses,
               entities, reinterpretations, brief for B next turn)
                    ▼
    A writes the fact layer · juice filter · mood drift

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
from .brains import adjutant, observer
from .brains.consults import ConsultBoard
from .brains.prompts import A_DIARY
from .brains.voice_a import A_TOOLS, VoiceResult, speak
from .config import ModelEndpoint, Settings
from .context import Scratch, build_messages
from .embedder import Embedder
from .identity import get_persona
from .life.subconscious import Subconscious
from .llm import LLM, OpenAICompatLLM, ToolCall, extract_json
from .memory.recall import Recall
from .memory.spontaneous import Spontaneous, render_pop
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
        self.a: LLM = llms.get("a") or OpenAICompatLLM(settings.a, name="A")
        self.b: LLM = llms.get("b") or OpenAICompatLLM(settings.b, name="B")
        self.c: LLM = llms.get("c") or OpenAICompatLLM(settings.c, name="C")
        embed_llm = llms["embed"] if "embed" in llms else (
            OpenAICompatLLM(settings.embed, name="embed") if settings.embed.api_key else None)
        self.embedder = Embedder(embed_llm)
        if "consultants" in llms:
            self.consultants: dict[str, LLM] = llms["consultants"]
        else:
            self.consultants = {m.split("/")[-1]: OpenAICompatLLM(
                ModelEndpoint(settings.consult_base_url, settings.consult_api_key, m, 60), name=m)
                for m in settings.consult_models}
        self.memory = Memory(self.store, self.embedder)
        self.recall = Recall(self.store, self.memory, self.embedder, band=settings.recall_band,
                             intrusion_p=settings.intrusion_probability, unaddressed_days=settings.unaddressed_days,
                             half_life_days=settings.psyche_half_life_days)
        self.spontaneous = Spontaneous(self.store, self.embedder, half_life_min=settings.activation_half_life_min,
                                       band=settings.recall_band)
        self.subconscious = Subconscious(self.store, self.embedder, floor=settings.juice_floor,
                                         buffer_rows=settings.buffer_rows, bowl_ratio=settings.bowl_ratio)
        self.bus = Bus()
        self.scratch: dict[int, Scratch] = {}
        self._turn_locks: dict[int, asyncio.Lock] = {}
        self._post_locks: dict[int, asyncio.Lock] = {}
        self._bg: set[asyncio.Task] = set()
        self.deep = None  # set by lanes.deep.attach()
        self.outbox = None  # set by life.initiative.attach()

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
                      tools: bool | None = None) -> AsyncIterator[dict]:
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
            tail_rows = await self.memory.tail(pid, self.s.tail_turns)
            tail_rows = [t for t in tail_rows if t["id"] != turn_id]
            recall_res, loops, integ = await asyncio.gather(
                self.recall.recall(persona, user_text, budget=self.s.recall_budget_chars,
                                   exclude_turn_ids={turn_id}, mood=_blend_moods(persona)),
                self.memory.open_loops(pid),
                self.memory.integrity(pid))
            recall_text = recall_res.text
            if self.deep is not None:
                done_jobs = await self.deep.unseen_results(pid)
                if done_jobs:
                    recall_text += "\n\nYour workshop finished (tell them if it matters):\n" + "\n".join(
                        f"• job {j['id']}: {j['task'][:160]} -> {j['result'][:1200]}" for j in done_jobs)
            # memories that were primed earlier and now pop up unbidden
            fmt = lambda ts: local_dt(ts, persona["timezone"]).strftime("%d %b")  # noqa: E731
            popped = await self.spontaneous.pops(persona, limit=1, at=started)
            pops_text = [render_pop(r, fmt) for r in popped]
            # thoughts it had while they were away (wander / dream), offered once
            mind_rows = await self.store.all(
                "SELECT id, text FROM thoughts WHERE persona_id = ? AND status = 'pending' AND source IN "
                "('wander','dream') ORDER BY score DESC, id DESC LIMIT 2", pid)
            for r in mind_rows:
                await self.store.update("thoughts", r["id"], status="offered")
            last_chat = next((t for t in reversed(tail_rows) if t["kind"] == "chat" and t["ended_at"]), None)
            if last_chat:
                clock.last_exchange_seconds = max(0.0, last_chat["ended_at"] - last_chat["started_at"])
            messages = build_messages(persona=persona, clock=clock, scratch=scratch, integrity=integ, loops=loops,
                                      recall_text=recall_text, tail=tail_rows, user_text=user_text, at=started,
                                      pops=pops_text, on_mind=[r["text"] for r in mind_rows])
            scratch.sudden = ""
            surfaced_ids = [*recall_res.layer_ids, *[r["id"] for r in popped]]

            async def run_tool(call: ToolCall) -> str:
                yield_tool.append(call.name)
                if call.name == "fetch_consults":
                    data = await board.fetch(self.s.consult_wait_ms)
                    return board.render(data)
                if call.name == "check_clock":
                    return json.dumps(clock.snapshot())
                if call.name == "delegate_task":
                    task = str(call.args().get("task", "")).strip()
                    if not task:
                        return json.dumps({"error": "empty task"})
                    if self.deep is None:
                        return json.dumps({"error": "workshop not available"})
                    job = await self.deep.submit(pid, task)
                    return json.dumps({"job": job, "status": "started - results will come back to you later"})
                return json.dumps({"error": f"unknown tool {call.name}"})

            yield_tool: list[str] = []
            use_tools = self.s.a_tools if tools is None else tools
            offered = [t for t in A_TOOLS if (t["function"]["name"] != "fetch_consults" or self.consultants)
                       and (t["function"]["name"] != "delegate_task" or self.deep is not None)]
            result = VoiceResult()
            failed = False
            try:
                async for piece in speak(self.a, messages, tools=offered if use_tools else None, run_tool=run_tool,
                                         result=result, model=self.a_model_for(persona)):
                    while yield_tool:
                        yield {"type": "tool", "name": yield_tool.pop(0)}
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
            await self.memory.finish_turn(turn_id, reply)
            await self.store.update("personas", pid, last_reply_at=now())
            if scratch.correction and not failed:
                scratch.correction = ""  # delivered (B re-checks next review)
            scratch.conscience = ""

            post = self.spawn(self._post_turn(persona, turn_id, user_text, reply, tail_rows, recall_res, board,
                                              result.chord, surface, failed, surfaced_ids), name=f"post-{turn_id}")
            done = {"type": "done", "turn_id": turn_id, "text": reply, "chord": result.chord,
                    "tools": result.tools_used, "spent_seconds": round(clock.spent, 2)}
            if self.s.show_verdict_to_user and not failed:
                try:
                    verdict = await asyncio.wait_for(asyncio.shield(post), timeout=60)
                    if verdict:
                        done["verdict"] = verdict
                except asyncio.TimeoutError:
                    pass
            yield done

    async def chat(self, pid: int, text: str, **kw) -> dict:
        """Non-streaming convenience."""
        out = {}
        async for ev in self.respond(pid, text, **kw):
            if ev["type"] == "done":
                out = ev
        return out

    # ------------------------------------------------------------- after the turn
    async def _post_turn(self, persona, turn_id, user_text, reply, tail, recall_res, board, a_chord, surface,
                         failed, surfaced_ids=None) -> dict | None:
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

            # ---- B: adjutant
            b_rev: dict = {}
            if not failed:
                try:
                    b_rev = await adjutant.review(self.b, persona=persona, user_text=user_text, reply=reply, tail=tail,
                                                  recall_text=recall_res.text, consults=consults, loops=loops,
                                                  c_brief=scratch.c_brief, at=now())
                except Exception as exc:  # noqa: BLE001
                    log.warning("B review failed: %s", exc)
            if b_rev:
                if b_rev["parsed"]:
                    await self.memory.record_verdict(pid, turn_id, b_rev["level"], b_rev["rationale"],
                                                     b_rev["findings"])
                    verdict_out = {"level": b_rev["level"], "rationale": b_rev["rationale"],
                                   "findings": b_rev["findings"]}
                    await self._revisit(pid, event_id, turn_id, user_text, b_rev, sources)
                scratch.conscience = b_rev["conscience"]
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
            if not failed:
                try:
                    recalled = [r for r in await self._layers(pid, recall_res.layer_ids)]
                    c_obs = await observer.observe(
                        self.c, persona=persona, user_text=user_text, reply=reply, tail=tail, b_review=b_rev,
                        hypotheses=await self.memory.hypotheses(pid), recalled=recalled,
                        effective_conf=lambda h: self.memory.effective_confidence(h, self.s.psyche_half_life_days))
                except Exception as exc:  # noqa: BLE001
                    log.warning("C observe failed: %s", exc)
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
                await self.memory.add_layers(pid, event_id, items, sources=sources)
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

            # ---- spontaneous recall: prime near-misses + chains, then the slow late search
            if not failed:
                try:
                    fresh = await get_persona(self.store, pid)
                    cue = f"{user_text}\n{reply}"
                    await self.spontaneous.prime(fresh, cue, surfaced=surfaced_ids or recall_res.layer_ids,
                                                 mood=_blend_moods(fresh))
                    late = await self.spontaneous.late_recall(fresh, cue, already=surfaced_ids or recall_res.layer_ids,
                                                              current_turn=turn_id)
                    if late:
                        when = local_dt(late["created_at"], fresh["timezone"]).strftime("%d %b")
                        said = f' - they said: "{late["quote"]}"' if late["quote"] else ""
                        memory_line = f"{late['text']}{said} (from {when})"
                        scratch.sudden = "• " + memory_line
                        await self.store.insert("thoughts", persona_id=pid, source="sudden", score=0.8,
                                                text=f"It suddenly came back to you, right after you answered: "
                                                     f"{memory_line}. Say it like 'oh wait, I just remembered' if it "
                                                     f"adds something to what you were just talking about.",
                                                ref=str(late["id"]), created_at=now())
                        if self.outbox is not None:
                            self.spawn(self._delayed_consider(pid), name=f"sudden-{turn_id}")
                except Exception as exc:  # noqa: BLE001
                    log.warning("spontaneous recall failed: %s", exc)

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

    async def _delayed_consider(self, pid: int) -> None:
        await asyncio.sleep(self.s.sudden_delay_seconds)
        await self.outbox.consider(pid)

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
