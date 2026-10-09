"""HTTP / WebSocket surface.

    /v1/chat/completions   OpenAI-compatible (any chat client works; streaming supported).
                           The persona owns the conversation: only the newest user message is
                           taken from the request - the history lives server-side, across devices.
    /v1/voice              realtime voice WebSocket
    /v1/events             live stream (SSE): running clock, pop-up messages, workshop results
    /v1/messages           outbox long-poll for clients without SSE
    /v1/persona/...        definition (preset / clone), clock, emotion, memory audit + wipe, loops, sleep
    /hooks/...             inbound SMS / WhatsApp (Twilio), Telegram, email
    /admin/personas        create personas (each gets its key = its identity)
    /app                   built-in web app (chat, voice, live timer, pop-ups)
"""
from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import logging
import os
import time
import uuid
from collections import defaultdict, deque

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from .. import emotion, identity
from ..channels.adapters import CHANNELS, Channels, twilio_signature_ok
from ..config import Settings, load_settings
from ..lanes.deep import attach as attach_deep
from ..life.finetune import FineTuner
from ..life.initiative import Initiative
from ..life.sleep import Sleep
from ..mind import Mind
from ..profiles import PRESETS, build_clone, preset_list
from ..store import jdump, jload, now
from ..timesense import Clock
from ..voice.realtime import SpeechIO, VoiceSession, parse_frame

log = logging.getLogger(__name__)
WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "web")
MAX_BODY = 256_000


class RateLimiter:
    def __init__(self, per_minute: int = 40):
        self.per_minute = per_minute
        self.hits: dict[str, deque] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        t = time.monotonic()
        q = self.hits[key]
        while q and t - q[0] > 60:
            q.popleft()
        if len(q) >= self.per_minute:
            return False
        q.append(t)
        return True


def _bearer(request_or_ws) -> str:
    auth = request_or_ws.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request_or_ws.query_params.get("key", "")


def _err(status: int, msg: str) -> JSONResponse:
    return JSONResponse({"error": {"message": msg}}, status_code=status)


class Runtime:
    """Everything the API needs, built once."""

    def __init__(self, settings: Settings, mind: Mind | None = None, channels: Channels | None = None):
        self.s = settings
        self.mind = mind or Mind(settings)
        self.channels = channels or Channels(settings)
        self.finetuner = FineTuner(settings, self.mind.store, self.mind.subconscious, self.mind.a)
        self.sleep = Sleep(self.mind, self.finetuner)
        self.initiative = Initiative(self.mind, self.channels, self.sleep)
        self.mind.outbox = self.initiative
        attach_deep(self.mind, settings)
        self.speech = SpeechIO(settings.asr, settings.tts, settings.tts_voice)
        self.limiter = RateLimiter()


def create_app(settings: Settings | None = None, runtime: Runtime | None = None) -> Starlette:
    settings = settings or (runtime.s if runtime else load_settings())
    rt = runtime or Runtime(settings)
    mind = rt.mind

    async def persona_or_401(request) -> dict | None:
        return await identity.persona_by_key(mind.store, _bearer(request))

    async def body_json(request: Request) -> dict:
        raw = await request.body()
        if len(raw) > MAX_BODY:
            raise ValueError("body too large")
        if not raw:
            return {}
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("expected a JSON object")
        return data

    # ------------------------------------------------------------------ basics
    async def health(request):
        return JSONResponse({"ok": True, "time": now(), "channels": rt.channels.available(),
                             "voice": {"asr": rt.speech.has_asr, "tts": rt.speech.has_tts},
                             "workshop": "openhands" if getattr(mind.deep.worker_factory(), "name", "") == "openhands"
                             else "model"})

    async def web_app(request):
        with open(os.path.join(WEB_DIR, "index.html"), encoding="utf-8") as fh:
            return HTMLResponse(fh.read())

    async def models(request):
        return JSONResponse({"object": "list", "data": [{"id": "sawii-persona", "object": "model",
                                                         "owned_by": "sawii"}]})

    # ------------------------------------------------------------------ admin
    def admin_ok(request) -> bool:
        tok = settings.admin_token
        return bool(tok) and hmac.compare_digest(_bearer(request), tok)

    async def admin_personas(request: Request):
        if not admin_ok(request):
            return _err(401, "admin token required")
        if request.method == "GET":
            rows = await mind.store.all("SELECT * FROM personas ORDER BY id")
            return JSONResponse({"personas": [identity.public_view(r) for r in rows]})
        try:
            data = await body_json(request)
        except ValueError as exc:
            return _err(400, str(exc))
        definition, preset = _definition_from(data)
        pid, key = await identity.create_persona(
            mind.store, name=str(data.get("name", ""))[:80], definition=definition, preset=preset,
            user_name=str(data.get("user_name", ""))[:80], timezone=str(data.get("timezone", "UTC"))[:64],
            contact=_clean_contact(data.get("contact")), preferred_channel=_channel(data.get("preferred_channel")))
        return JSONResponse({"id": pid, "key": key, "note": "Store this key: it IS the persona's identity. "
                                                            "Only its hash is kept."}, status_code=201)

    # ------------------------------------------------------------------ chat
    async def chat_completions(request: Request):
        persona = await persona_or_401(request)
        if not persona:
            return _err(401, "invalid persona key")
        if not rt.limiter.allow(f"chat:{persona['id']}"):
            return _err(429, "slow down")
        try:
            data = await body_json(request)
        except (ValueError, json.JSONDecodeError) as exc:
            return _err(400, str(exc))
        msgs = data.get("messages") or []
        text = ""
        for m in reversed(msgs):
            if isinstance(m, dict) and m.get("role") == "user":
                c = m.get("content")
                text = c if isinstance(c, str) else " ".join(
                    p.get("text", "") for p in (c or []) if isinstance(p, dict) and p.get("type") == "text")
                break
        if not text.strip():
            return _err(400, "no user message")
        surface = _surface(request)
        cid = "chatcmpl-" + uuid.uuid4().hex[:24]
        created = int(now())
        gateway_tools = data.get("gateway_tools")
        if not isinstance(gateway_tools, list):
            gateway_tools = None
        gateway_webhook = data.get("gateway_webhook")
        if not isinstance(gateway_webhook, dict):
            gateway_webhook = None
        if data.get("stream"):
            async def gen():
                async for ev in mind.respond(persona["id"], text, surface=surface,
                                             gateway_tools=gateway_tools,
                                             gateway_webhook=gateway_webhook):
                    if ev["type"] == "delta":
                        chunk = {"id": cid, "object": "chat.completion.chunk", "created": created,
                                 "model": "sawii-persona",
                                 "choices": [{"index": 0, "delta": {"content": ev["text"]}, "finish_reason": None}]}
                        yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
                    elif ev["type"] in ("tool_start", "tool_done"):
                        # Live tool chip events (sawii-only extension; OpenAI clients
                        # ignore unknown chunk fields).
                        chunk = {"id": cid, "object": "chat.completion.chunk", "created": created,
                                 "model": "sawii-persona", "sawii_tool": ev,
                                 "choices": [{"index": 0, "delta": {}, "finish_reason": None}]}
                        yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
                    elif ev["type"] == "done":
                        extra = {}
                        if "verdict" in ev:
                            extra["sawii_verdict"] = ev["verdict"]
                        if ev.get("inner"):
                            extra["sawii_inner"] = ev["inner"]  # who spoke to A mid-reply (B/C), kinds only
                        chunk = {"id": cid, "object": "chat.completion.chunk", "created": created,
                                 "model": "sawii-persona", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                                 **extra}
                        yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"
            return StreamingResponse(gen(), media_type="text/event-stream",
                                     headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
        out = await mind.chat(persona["id"], text, surface=surface)
        body = {"id": cid, "object": "chat.completion", "created": created, "model": "sawii-persona",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": out.get("text", "")},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}
        if "verdict" in out:
            body["sawii_verdict"] = out["verdict"]
        return JSONResponse(body)

    # ------------------------------------------------------------------ persona
    async def persona_get_put(request: Request):
        persona = await persona_or_401(request)
        if not persona:
            return _err(401, "invalid persona key")
        if request.method == "GET":
            return JSONResponse(identity.public_view(persona))
        try:
            data = await body_json(request)
        except (ValueError, json.JSONDecodeError) as exc:
            return _err(400, str(exc))
        updates = {}
        if any(k in data for k in ("definition", "preset", "clone")):
            definition, preset = _definition_from(data)
            updates.update(definition=definition, preset=preset)
        for k, n in (("name", 80), ("user_name", 80), ("timezone", 64)):
            if k in data:
                updates[k] = str(data[k])[:n]
        if "contact" in data:
            updates["contact"] = jdump(_clean_contact(data["contact"]))
        if "preferred_channel" in data:
            updates["preferred_channel"] = _channel(data["preferred_channel"])
        if "a_model" in data:
            updates["a_model_override"] = str(data["a_model"] or "")[:200]
        if updates:
            await mind.store.update("personas", persona["id"], **updates)
        return JSONResponse(identity.public_view(await identity.get_persona(mind.store, persona["id"])))

    async def presets(request):
        return JSONResponse({"presets": preset_list()})

    async def clock(request):
        persona = await persona_or_401(request)
        if not persona:
            return _err(401, "invalid persona key")
        c = Clock(started_at=now(), last_reply_at=persona["last_reply_at"], last_user_at=persona["last_user_at"],
                  tz=persona["timezone"])
        return JSONResponse(c.snapshot())

    async def emotion_view(request):
        persona = await persona_or_401(request)
        if not persona:
            return _err(401, "invalid persona key")
        own, user = jload(persona["mood"], {}), jload(persona["user_mood"], {})
        timeline = await mind.store.all("SELECT created_at, chord FROM events WHERE persona_id = ? AND chord != '{}' "
                                        "ORDER BY id DESC LIMIT 200", persona["id"])
        return JSONResponse({
            "persona": {"chord": own, "words": emotion.describe(own), "color": emotion.color(own)},
            "user": {"chord": user, "words": emotion.describe(user), "color": emotion.color(user)},
            "timeline": [{"at": t["created_at"], "color": emotion.color(jload(t["chord"], {})),
                          "words": emotion.describe(jload(t["chord"], {}))} for t in reversed(timeline)]})

    async def memory_view(request):
        persona = await persona_or_401(request)
        if not persona:
            return _err(401, "invalid persona key")
        return JSONResponse(await mind.memory.inspect(persona["id"]))

    async def memory_wipe(request):
        persona = await persona_or_401(request)
        if not persona:
            return _err(401, "invalid persona key")
        try:
            lid = int(request.path_params["layer_id"])
        except ValueError:
            return _err(400, "bad id")
        ok = await mind.memory.wipe(persona["id"], lid)
        return JSONResponse({"wiped": ok}, status_code=200 if ok else 404)

    async def loops_view(request):
        persona = await persona_or_401(request)
        if not persona:
            return _err(401, "invalid persona key")
        return JSONResponse({"open": await mind.memory.open_loops(persona["id"])})

    async def sleep_now(request):
        persona = await persona_or_401(request)
        if not persona:
            return _err(401, "invalid persona key")
        return JSONResponse(await rt.sleep.run(persona))

    async def subconscious_view(request):
        persona = await persona_or_401(request)
        if not persona:
            return _err(401, "invalid persona key")
        jobs = await mind.store.all("SELECT id, provider, status, output_model, detail, created_at FROM finetune_jobs "
                                    "WHERE persona_id = ? ORDER BY id DESC LIMIT 10", persona["id"])
        return JSONResponse({"buffer": await mind.subconscious.occupancy(persona["id"]), "jobs": jobs,
                             "a_model": persona["a_model_override"] or None})

    async def deep_submit(request):
        persona = await persona_or_401(request)
        if not persona:
            return _err(401, "invalid persona key")
        try:
            data = await body_json(request)
        except (ValueError, json.JSONDecodeError) as exc:
            return _err(400, str(exc))
        task = str(data.get("task", "")).strip()
        if not task:
            return _err(400, "task required")
        mode = data.get("mode") if data.get("mode") in ("auto", "single", "map", "speculative") else "auto"
        job = await mind.deep.submit(persona["id"], task, mode)
        return JSONResponse({"job": job}, status_code=202)

    async def deep_get(request):
        persona = await persona_or_401(request)
        if not persona:
            return _err(401, "invalid persona key")
        row = await mind.store.one("SELECT * FROM deep_jobs WHERE id = ? AND persona_id = ?",
                                   int(request.path_params["job_id"]), persona["id"])
        if not row:
            return _err(404, "no such job")
        row["trace"] = jload(row["trace"], {})
        return JSONResponse(row)

    # ------------------------------------------------------------------ live
    async def messages_poll(request):
        persona = await persona_or_401(request)
        if not persona:
            return _err(401, "invalid persona key")
        wait = min(30.0, max(0.0, float(request.query_params.get("wait", "0") or 0)))
        deadline = time.monotonic() + wait
        while True:
            rows = await mind.store.all("SELECT id, text, reason, channel, created_at FROM outbox WHERE persona_id = ? "
                                        "AND status IN ('queued','sent') AND channel = 'app' ORDER BY id", persona["id"])
            if rows or time.monotonic() >= deadline:
                break
            await asyncio.sleep(0.5)
        for r in rows:
            await mind.store.update("outbox", r["id"], status="read")
        return JSONResponse({"messages": rows})

    async def events_sse(request):
        persona = await persona_or_401(request)
        if not persona:
            return _err(401, "invalid persona key")
        pid = persona["id"]
        q = mind.bus.subscribe(pid)

        async def gen():
            try:
                yield "retry: 3000\n\n"
                while True:
                    try:
                        ev = await asyncio.wait_for(q.get(), timeout=1.0)
                        if ev.get("type") == "message":
                            await mind.store.update("outbox", ev["id"], status="read")
                        yield f"event: {ev['type']}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n"
                    except asyncio.TimeoutError:
                        p = await identity.get_persona(mind.store, pid)
                        c = Clock(started_at=now(), last_reply_at=p["last_reply_at"], last_user_at=p["last_user_at"],
                                  tz=p["timezone"])
                        yield f"event: clock\ndata: {json.dumps(c.snapshot())}\n\n"
                    if await request.is_disconnected():
                        break
            finally:
                mind.bus.unsubscribe(pid, q)
        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    async def voice_ws(ws: WebSocket):
        await ws.accept()
        key = _bearer(ws)
        if not key:
            try:
                hello = parse_frame(await asyncio.wait_for(ws.receive_text(), timeout=10))
                key = str(hello.get("key", ""))
            except Exception:  # noqa: BLE001
                await ws.close(code=4401)
                return
        persona = await identity.persona_by_key(mind.store, key)
        if not persona:
            await ws.send_json({"type": "error", "detail": "invalid persona key"})
            await ws.close(code=4401)
            return
        session = VoiceSession(mind, persona, rt.speech, ws.send_json, ws.send_bytes)
        q = mind.bus.subscribe(persona["id"])

        async def popups():
            while True:
                ev = await q.get()
                if ev.get("type") == "message":
                    await mind.store.update("outbox", ev["id"], status="read")
                    await session.speak_text(ev["text"])

        pop = asyncio.create_task(popups())
        await ws.send_json({"type": "ready", "persona": persona["name"], "tts": "server" if rt.speech.has_tts else
                            "client", "asr": "server" if rt.speech.has_asr else "client"})
        try:
            while True:
                msg = await ws.receive()
                if msg["type"] == "websocket.disconnect":
                    break
                if msg.get("bytes") is not None:
                    await session.on_audio(msg["bytes"])
                elif msg.get("text") is not None:
                    await session.handle_frame(parse_frame(msg["text"]))
        except WebSocketDisconnect:
            pass
        finally:
            pop.cancel()
            mind.bus.unsubscribe(persona["id"], q)
            await session.finish()

    # ------------------------------------------------------------------ inbound channels
    async def _inbound(persona: dict, text: str, channel: str):
        out = await mind.chat(persona["id"], text, channel=channel)
        reply = out.get("text", "")
        if reply:
            try:
                await rt.channels.deliver(persona, channel, reply)
            except Exception as exc:  # noqa: BLE001
                log.warning("reply delivery failed on %s: %s", channel, exc)

    async def hook_twilio(request: Request):
        form = dict(await request.form())
        url = settings.public_base_url.rstrip("/") + request.url.path
        if not twilio_signature_ok(settings.twilio_token, url, form, request.headers.get("x-twilio-signature", "")):
            return _err(403, "bad signature")
        sender = str(form.get("From", ""))
        channel = "whatsapp" if sender.startswith("whatsapp:") else "sms"
        persona = await identity.find_by_contact(mind.store, channel, sender.replace("whatsapp:", ""))
        if persona is None and channel == "whatsapp":
            persona = await identity.find_by_contact(mind.store, channel, sender)
        if persona and str(form.get("Body", "")).strip():
            mind.spawn(_inbound(persona, str(form["Body"]), channel), name="inbound-twilio")
        return Response("<Response></Response>", media_type="application/xml")

    async def hook_telegram(request: Request):
        if not settings.telegram_secret or not hmac.compare_digest(
                request.headers.get("x-telegram-bot-api-secret-token", ""), settings.telegram_secret):
            return _err(403, "bad secret")
        data = await body_json(request)
        msg = data.get("message") or {}
        chat_id = str((msg.get("chat") or {}).get("id", ""))
        persona = await identity.find_by_contact(mind.store, "telegram", chat_id)
        if persona and msg.get("text"):
            mind.spawn(_inbound(persona, str(msg["text"]), "telegram"), name="inbound-telegram")
        return JSONResponse({"ok": True})

    async def lane_question(request: Request):
        """A plugin on the gateway, mid-run, is asking A something. Routed to
        this user's Director, which stashes it in pending_questions and either
        pushes to A's inner_voice (if a turn is live) or surfaces in the brief
        on A's next turn."""
        secret = os.environ.get("GATEWAY_INTERNAL_SECRET", "").strip()
        if not secret or not hmac.compare_digest(
                request.headers.get("x-internal-secret", "") or "", secret):
            return _err(401, "bad secret")
        try:
            data = await body_json(request)
        except Exception:  # noqa: BLE001
            return _err(400, "bad body")
        key = str(data.get("persona_key") or "").strip()
        if not key:
            return _err(400, "missing persona_key")
        persona = await identity.persona_by_key(mind.store, key)
        if persona is None:
            return _err(404, "unknown persona")
        mind.director.deliver_question(persona["id"], {
            "question_id": str(data.get("question_id") or ""),
            "tool_call_id": str(data.get("tool_call_id") or ""),
            "question": str(data.get("question") or ""),
            "default_if_quiet": str(data.get("default_if_quiet") or ""),
        })
        return JSONResponse({"ok": True})

    async def hook_email(request: Request):
        if not settings.email_inbound_secret or not hmac.compare_digest(
                request.query_params.get("secret", ""), settings.email_inbound_secret):
            return _err(403, "bad secret")
        data = await body_json(request)
        sender = str(data.get("from", "")).strip()
        if "<" in sender:
            sender = sender.split("<", 1)[1].rstrip(">")
        persona = await identity.find_by_contact(mind.store, "email", sender)
        if persona and str(data.get("text", "")).strip():
            mind.spawn(_inbound(persona, str(data["text"])[:20000], "email"), name="inbound-email")
        return JSONResponse({"ok": True})

    @contextlib.asynccontextmanager
    async def lifespan(app):
        if settings.background_enabled:
            rt.initiative.start()
        yield
        await rt.initiative.stop()
        await mind.drain(timeout=10)

    routes = [
        Route("/", lambda r: RedirectResponse("/app")),
        Route("/health", health),
        Route("/app", web_app),
        Route("/admin/personas", admin_personas, methods=["GET", "POST"]),
        Route("/v1/models", models),
        Route("/v1/chat/completions", chat_completions, methods=["POST"]),
        Route("/v1/persona", persona_get_put, methods=["GET", "PUT"]),
        Route("/v1/persona/presets", presets),
        Route("/v1/persona/clock", clock),
        Route("/v1/persona/emotion", emotion_view),
        Route("/v1/persona/memory", memory_view),
        Route("/v1/persona/memory/{layer_id}", memory_wipe, methods=["DELETE"]),
        Route("/v1/persona/loops", loops_view),
        Route("/v1/persona/sleep", sleep_now, methods=["POST"]),
        Route("/v1/persona/subconscious", subconscious_view),
        Route("/v1/deep", deep_submit, methods=["POST"]),
        Route("/v1/deep/{job_id:int}", deep_get),
        Route("/v1/messages", messages_poll),
        Route("/v1/events", events_sse),
        WebSocketRoute("/v1/voice", voice_ws),
        Route("/hooks/twilio", hook_twilio, methods=["POST"]),
        Route("/hooks/telegram", hook_telegram, methods=["POST"]),
        Route("/hooks/email", hook_email, methods=["POST"]),
        Route("/v1/lane/questions", lane_question, methods=["POST"]),
    ]
    app = Starlette(routes=routes, lifespan=lifespan)
    app.state.runtime = rt
    return app


def _definition_from(data: dict) -> tuple[str, str]:
    """preset id | clone fields | free text - always the user's words."""
    if isinstance(data.get("clone"), dict):
        c = data["clone"]
        samples = c.get("sample_messages") or []
        if isinstance(samples, str):
            samples = [s for s in samples.splitlines() if s.strip()]
        return build_clone(name=str(c.get("name", "someone"))[:80], relationship=str(c.get("relationship", ""))[:200],
                           how_they_talk=str(c.get("how_they_talk", ""))[:3000],
                           sample_messages=[str(s)[:500] for s in samples][:40], traits=str(c.get("traits", ""))[:3000],
                           shared_memories=str(c.get("shared_memories", ""))[:5000],
                           things_they_would_never_say=str(c.get("never", ""))[:1000]), "clone"
    preset = str(data.get("preset", "") or "")
    if preset in PRESETS:
        extra = str(data.get("definition", "") or "").strip()
        return (PRESETS[preset]["definition"] + ("\n" + extra if extra else ""))[:12000], preset
    return str(data.get("definition", "") or "")[:12000], ""


def _surface(request) -> str:
    """Where they are talking from (device/app). Provenance only - never memory content."""
    import re as _re
    parts = [request.headers.get(h, "") for h in ("x-dl-surface-device", "x-dl-surface-app")]
    raw = " / ".join(p for p in parts if p) or request.headers.get("x-surface", "")
    return _re.sub(r"[^\w .:/@+-]", "", raw)[:120]


def _clean_contact(raw) -> dict:
    if not isinstance(raw, dict):
        return {}
    return {k: str(v)[:200] for k, v in raw.items() if k in ("sms", "whatsapp", "email", "telegram") and v}


def _channel(v) -> str:
    return v if v in CHANNELS else "app"


def main_app() -> Starlette:  # uvicorn sawii_persona.api.app:main_app --factory
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return create_app()
