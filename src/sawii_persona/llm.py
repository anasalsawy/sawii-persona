"""OpenAI-compatible model clients (chat, streaming with tool calls, embeddings).

Every lobe talks through :class:`LLM`. Tests swap in :class:`ScriptedLLM`.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import random
import re
import time
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, AsyncIterator, Awaitable, Callable
from urllib.parse import urlsplit

try:  # production dependency
    import httpx  # type: ignore
except ImportError:  # pragma: no cover - sandbox fallback (same API)
    import httpx2 as httpx  # type: ignore

from .config import ModelEndpoint


class LLMError(RuntimeError):
    pass


@dataclass
class _ProviderState:
    semaphore: asyncio.Semaphore
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    next_start: float = 0.0
    blocked_until: float = 0.0
    minute_requests: deque = field(default_factory=deque)
    minute_tokens: deque = field(default_factory=deque)
    daily_date: date | None = None
    daily_requests: int = 0


class ProviderPacer:
    """Process-local request pacing shared by models using the same provider key."""

    RETRYABLE_STATUSES = {429, 502, 503, 504}

    def __init__(self, *, min_interval_seconds: float = 2.1, retry_attempts: int = 2,
                 max_retry_wait_seconds: float = 30.0, requests_per_minute: int = 28,
                 tokens_per_minute: int = 0, requests_per_day: int = 0,
                 max_concurrent_requests: int = 3):
        if min_interval_seconds < 0 or retry_attempts < 0 or max_retry_wait_seconds < 0:
            raise ValueError("Pacing and retry settings must be non-negative")
        if requests_per_minute < 0 or tokens_per_minute < 0 or requests_per_day < 0:
            raise ValueError("Free-mode request and token limits must be non-negative")
        if max_concurrent_requests < 1:
            raise ValueError("Maximum concurrent provider requests must be at least one")
        self.min_interval_seconds = min_interval_seconds
        self.retry_attempts = retry_attempts
        self.max_retry_wait_seconds = max_retry_wait_seconds
        self.requests_per_minute = requests_per_minute
        self.tokens_per_minute = tokens_per_minute
        self.requests_per_day = requests_per_day
        self.max_concurrent_requests = max_concurrent_requests
        self._states: dict[str, _ProviderState] = {}

    @staticmethod
    def provider_key(endpoint: ModelEndpoint) -> str:
        identity = f"key:{endpoint.api_key}" if endpoint.api_key else f"url:{endpoint.base_url.lower()}"
        return hashlib.sha256(identity.encode()).hexdigest()

    def _state(self, key: str) -> _ProviderState:
        state = self._states.get(key)
        if state is None:
            state = self._states[key] = _ProviderState(
                semaphore=asyncio.Semaphore(self.max_concurrent_requests))
        return state

    @asynccontextmanager
    async def request(self, key: str, estimated_tokens: int = 0):
        if estimated_tokens < 0:
            raise ValueError("Estimated request tokens must be non-negative")
        if self.tokens_per_minute and estimated_tokens > self.tokens_per_minute:
            raise LLMError("Request estimate exceeds the configured free-tier tokens-per-minute limit")
        state = self._state(key)
        await state.semaphore.acquire()
        try:
            while True:
                async with state.lock:
                    now = time.monotonic()
                    while state.minute_requests and state.minute_requests[0][0] <= now - 60:
                        state.minute_requests.popleft()
                    while state.minute_tokens and state.minute_tokens[0][0] <= now - 60:
                        state.minute_tokens.popleft()

                    today = datetime.now(timezone.utc).date()
                    if state.daily_date != today:
                        state.daily_date = today
                        state.daily_requests = 0
                    if self.requests_per_day and state.daily_requests >= self.requests_per_day:
                        raise LLMError("Configured free-tier daily request safety limit reached")

                    delay = max(state.next_start, state.blocked_until) - now
                    if state.blocked_until - now > self.max_retry_wait_seconds:
                        remaining = state.blocked_until - now
                        raise LLMError(
                            f"Provider rate-limit cooldown active for about {remaining:.0f}s")
                    if self.requests_per_minute and len(state.minute_requests) >= self.requests_per_minute:
                        delay = max(delay, state.minute_requests[0][0] + 60 - now)
                    if self.tokens_per_minute:
                        used_tokens = sum(tokens for _, tokens in state.minute_tokens)
                        tokens_to_release = used_tokens + estimated_tokens - self.tokens_per_minute
                        if tokens_to_release > 0:
                            released = 0
                            for started, tokens in state.minute_tokens:
                                released += tokens
                                if released >= tokens_to_release:
                                    delay = max(delay, started + 60 - now)
                                    break
                    if delay <= 0:
                        state.next_start = now + self.min_interval_seconds
                        state.minute_requests.append((now, 1))
                        if self.tokens_per_minute:
                            state.minute_tokens.append((now, estimated_tokens))
                        state.daily_requests += 1
                        break
                await asyncio.sleep(delay)
            yield
        finally:
            state.semaphore.release()

    async def postpone(self, key: str, seconds: float) -> None:
        state = self._state(key)
        async with state.lock:
            state.blocked_until = max(state.blocked_until, time.monotonic() + seconds)

    async def record_backoff(self, key: str, headers, attempt: int) -> float | None:
        """Record provider cooldown even when it is too long to retry inline."""
        delay = self.retry_delay(headers, attempt, max_wait_seconds=float("inf"))
        if delay is not None:
            await self.postpone(key, delay)
        if delay is not None and delay <= self.max_retry_wait_seconds:
            return delay
        return None

    @staticmethod
    def retry_delay(headers, attempt: int, *, max_wait_seconds: float = 30.0) -> float | None:
        retry_after = headers.get("retry-after")
        if retry_after:
            try:
                delay = max(0.0, float(retry_after))
            except ValueError:
                try:
                    retry_at = parsedate_to_datetime(retry_after)
                    if retry_at.tzinfo is None:
                        retry_at = retry_at.replace(tzinfo=timezone.utc)
                    delay = max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())
                except (TypeError, ValueError, OverflowError):
                    delay = None
            if delay is not None:
                return delay if delay <= max_wait_seconds else None
        base = min(30.0, 1.0 * (2 ** attempt))
        return min(max_wait_seconds, base + random.uniform(0.0, base * 0.25))


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str = ""

    def args(self) -> dict:
        try:
            v = json.loads(self.arguments or "{}")
            return v if isinstance(v, dict) else {}
        except json.JSONDecodeError:
            return {}


@dataclass
class ChatResult:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = "stop"
    raw: dict | None = None


_THINK = re.compile(r"<think>.*?</think>", re.S)


def strip_reasoning(text: str) -> str:
    """Remove inline reasoning blocks some open models emit."""
    text = _THINK.sub("", text or "")
    if "</think>" in text:  # unterminated opening already streamed
        text = text.split("</think>", 1)[1]
    return text.strip()


class LLM:
    """Base interface.

    ``on_health`` is an optional :class:`subsystems.SubsystemHealth` the LLM
    reports to on every call: :meth:`ok` on a successful response, :meth:`fail`
    with the exception text otherwise. Wiring is done at Mind construction so
    each lobe's calls land on their own named subsystem (a_llm, b_llm, c_llm).
    """

    name: str = "llm"
    on_health = None  # type: ignore[assignment]

    async def chat(self, messages: list[dict], *, tools: list[dict] | None = None,
                   temperature: float = 0.7, max_tokens: int = 1024,
                   json_mode: bool = False) -> ChatResult:
        raise NotImplementedError

    async def stream(self, messages: list[dict], *, tools: list[dict] | None = None,
                     temperature: float = 0.7, max_tokens: int = 1024) -> AsyncIterator[dict]:
        result = await self.chat(messages, tools=tools, temperature=temperature, max_tokens=max_tokens)
        if result.text:
            yield {"type": "text", "text": result.text}
        for call in result.tool_calls:
            yield {"type": "tool_call", "call": call}
        yield {"type": "done", "finish_reason": result.finish_reason}

    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError


class OpenAICompatLLM(LLM):
    def __init__(self, endpoint: ModelEndpoint, *, name: str = "llm", client: "httpx.AsyncClient | None" = None,
                 pacer: ProviderPacer | None = None):
        self.endpoint = endpoint
        self.name = name
        self._client = client
        self.pacer = pacer
        self._provider_key = ProviderPacer.provider_key(endpoint) if pacer is not None else ""
        # Optional runtime metrics. Reset between runs by callers who care.
        # Keys: n_calls, n_errors, total_ms, last_status.
        self.call_stats: dict = {"n_calls": 0, "n_errors": 0, "total_ms": 0.0,
                                 "last_status": 0, "last_error": ""}

    def reset_stats(self) -> None:
        self.call_stats = {"n_calls": 0, "n_errors": 0, "total_ms": 0.0,
                           "last_status": 0, "last_error": ""}

    @property
    def model(self) -> str:
        return self.endpoint.model

    def _http(self) -> "httpx.AsyncClient":
        if self._client is None:
            # HTTP/2 so Cloudflare sees a modern-browser TLS/ALPN fingerprint
            # alongside the browser headers. If h2 isn't installed, httpx
            # silently falls back to HTTP/1.1, which still passes with the
            # richer headers in _headers().
            try:
                self._client = httpx.AsyncClient(timeout=self.endpoint.timeout, http2=True)
            except Exception:  # noqa: BLE001
                self._client = httpx.AsyncClient(timeout=self.endpoint.timeout)
        return self._client

    def _health_begin(self) -> None:
        begin = getattr(self.on_health, "begin", None)
        if callable(begin):
            begin()

    def _health_end(self) -> None:
        end = getattr(self.on_health, "end", None)
        if callable(end):
            end()

    @asynccontextmanager
    async def _request_slot(self, estimated_tokens: int = 0):
        if self.pacer is None:
            yield
        else:
            async with self.pacer.request(self._provider_key, estimated_tokens):
                yield

    @staticmethod
    def _estimate_tokens(body: dict) -> int:
        serialized = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
        prompt_tokens = max((len(serialized) + 3) // 4,
                            (len(serialized.encode("utf-8")) + 1) // 2)
        return prompt_tokens + int(body.get("max_tokens") or 0)

    async def _post(self, url: str, *, json_body: dict):
        if self.pacer is None:
            return await self._http().post(url, headers=self._headers(), json=json_body)
        for attempt in range(self.pacer.retry_attempts + 1):
            retry = False
            async with self.pacer.request(self._provider_key, self._estimate_tokens(json_body)):
                response = await self._http().post(url, headers=self._headers(), json=json_body)
                if response.status_code in self.pacer.RETRYABLE_STATUSES:
                    delay = await self.pacer.record_backoff(self._provider_key, response.headers, attempt)
                    retry = delay is not None and attempt < self.pacer.retry_attempts
            if not retry:
                return response
        raise AssertionError("unreachable")

    @asynccontextmanager
    async def _stream_response(self, url: str, *, json_body: dict):
        attempts = self.pacer.retry_attempts if self.pacer is not None else 0
        for attempt in range(attempts + 1):
            async with self._request_slot(self._estimate_tokens(json_body)):
                async with self._http().stream(
                        "POST", url, headers=self._headers(), json=json_body) as response:
                    if self.pacer is None or response.status_code not in self.pacer.RETRYABLE_STATUSES:
                        yield response
                        return
                    delay = await self.pacer.record_backoff(
                        self._provider_key, response.headers, attempt)
                    if delay is None or attempt >= attempts:
                        yield response
                        return
                    await response.aread()

    def _headers(self) -> dict:
        h = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
            "User-Agent": "sawii-persona/0.1",
        }
        host = (urlsplit(self.endpoint.base_url).hostname or "").lower()
        if host == "api.featherless.ai" or host.endswith(".featherless.ai"):
            h.update({
                "Accept-Language": "en-US,en;q=0.9",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                              "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
                "sec-ch-ua": '"Not/A)Brand";v="8", "Chromium";v="126", "Google Chrome";v="126"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"',
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "same-origin",
                "Origin": "https://featherless.ai",
                "Referer": "https://featherless.ai/",
            })
        if self.endpoint.api_key:
            h["Authorization"] = f"Bearer {self.endpoint.api_key}"
        return h

    def _body(self, messages, tools, temperature, max_tokens, *, stream: bool, json_mode: bool = False,
              model: str | None = None) -> dict:
        body: dict[str, Any] = {
            "model": model or self.endpoint.model,
            "messages": messages,
            "temperature": temperature,
            "stream": stream,
        }
        # ``max_tokens`` is optional at the wire level: pass None and the body
        # omits it, letting the provider's own ceiling (model context window)
        # apply. Callers that want a cap pass an int.
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        if tools:
            body["tools"] = tools
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        if getattr(self.endpoint, "no_think", False):
            # Ask reasoning models (Qwen3, DeepSeek-R1, ...) to skip the hidden
            # "thinking" stream (delta.reasoning). Measured: 26-74s per reply with
            # thinking on vs ~3s with it off. Providers that do not understand the
            # field ignore it; ones that reject it are handled by the 400-fallback
            # in chat()/stream().
            body["chat_template_kwargs"] = {"enable_thinking": False}
        return body

    async def chat(self, messages, *, tools=None, temperature=0.7, max_tokens=1024, json_mode=False,
                   model: str | None = None) -> ChatResult:
        import time as _time
        if not self.endpoint.configured:
            if self.on_health is not None:
                self.on_health.fail("endpoint not configured")
            self.call_stats["n_errors"] += 1
            self.call_stats["last_error"] = "endpoint not configured"
            raise LLMError(f"{self.name}: endpoint not configured")
        url = f"{self.endpoint.base_url}/chat/completions"
        body = self._body(messages, tools, temperature, max_tokens, stream=False, json_mode=json_mode, model=model)
        t0 = _time.monotonic()
        self._health_begin()
        try:
            resp = await self._post(url, json_body=body)
            if resp.status_code == 400 and (json_mode or "chat_template_kwargs" in body):
                # Provider rejected an optional field we added (response_format
                # and/or the thinking-off hint). Drop them and retry once.
                body.pop("response_format", None)
                body.pop("chat_template_kwargs", None)
                resp = await self._post(url, json_body=body)
            self.call_stats["last_status"] = resp.status_code
            if resp.status_code >= 400:
                raise LLMError(f"{self.name}: HTTP {resp.status_code}: {resp.text[:300]}")
            data = resp.json()
        except LLMError as exc:
            self.call_stats["total_ms"] += (_time.monotonic() - t0) * 1000
            self.call_stats["n_calls"] += 1
            self.call_stats["n_errors"] += 1
            self.call_stats["last_error"] = str(exc)[:200]
            if self.on_health is not None:
                self.on_health.fail(str(exc))
            raise
        except Exception as exc:  # noqa: BLE001
            self.call_stats["total_ms"] += (_time.monotonic() - t0) * 1000
            self.call_stats["n_calls"] += 1
            self.call_stats["n_errors"] += 1
            self.call_stats["last_error"] = f"{type(exc).__name__}: {exc}"[:200]
            if self.on_health is not None:
                self.on_health.fail(f"{type(exc).__name__}: {exc}")
            raise
        finally:
            self._health_end()
        self.call_stats["total_ms"] += (_time.monotonic() - t0) * 1000
        self.call_stats["n_calls"] += 1
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        calls = [ToolCall(id=c.get("id") or f"call_{i}", name=(c.get("function") or {}).get("name", ""),
                          arguments=(c.get("function") or {}).get("arguments") or "")
                 for i, c in enumerate(msg.get("tool_calls") or [])]
        if self.on_health is not None:
            self.on_health.ok(extra={"model": self.endpoint.model})
        return ChatResult(text=strip_reasoning(msg.get("content") or ""), tool_calls=calls,
                          finish_reason=choice.get("finish_reason") or "stop", raw=data)

    async def stream(self, messages, *, tools=None, temperature=0.7, max_tokens=1024,
                     model: str | None = None) -> AsyncIterator[dict]:
        if not self.endpoint.configured:
            if self.on_health is not None:
                self.on_health.fail("endpoint not configured")
            raise LLMError(f"{self.name}: endpoint not configured")
        url = f"{self.endpoint.base_url}/chat/completions"
        body = self._body(messages, tools, temperature, max_tokens, stream=True, model=model)
        calls: dict[int, ToolCall] = {}
        finish = "stop"
        in_think = False
        ok = False
        self._health_begin()
        try:
            # Two attempts at most: the second drops the thinking-off hint in case
            # the provider rejected the unknown field with a 400.
            for _attempt in (0, 1):
                if _attempt:
                    body.pop("chat_template_kwargs", None)
                async with self._stream_response(url, json_body=body) as resp:
                    if resp.status_code == 400 and "chat_template_kwargs" in body:
                        await resp.aread()
                        continue
                    if resp.status_code >= 400:
                        text = (await resp.aread()).decode(errors="replace")
                        raise LLMError(f"{self.name}: HTTP {resp.status_code}: {text[:300]}")
                    async for line in resp.aiter_lines():
                        line = line.strip()
                        if not line.startswith("data:"):
                            continue
                        payload = line[5:].strip()
                        if payload == "[DONE]":
                            break
                        try:
                            chunk = json.loads(payload)
                        except json.JSONDecodeError:
                            continue
                        if chunk.get("error"):
                            raise LLMError(f"{self.name}: stream error: {str(chunk['error'])[:300]}")
                        choice = (chunk.get("choices") or [{}])[0]
                        delta = choice.get("delta") or {}
                        piece = delta.get("content") or ""
                        if piece:
                            # drop <think> sections from models that inline reasoning
                            if "<think>" in piece:
                                in_think = True
                                piece = piece.split("<think>", 1)[0]
                            if in_think:
                                if "</think>" in piece:
                                    in_think = False
                                    piece = piece.split("</think>", 1)[1]
                                else:
                                    piece = ""
                            if piece:
                                yield {"type": "text", "text": piece}
                        for tc in delta.get("tool_calls") or []:
                            idx = tc.get("index", 0)
                            call = calls.setdefault(idx, ToolCall(id=tc.get("id") or f"call_{idx}", name=""))
                            fn = tc.get("function") or {}
                            if tc.get("id"):
                                call.id = tc["id"]
                            if fn.get("name"):
                                call.name += fn["name"]
                            if fn.get("arguments"):
                                call.arguments += fn["arguments"]
                        if choice.get("finish_reason"):
                            finish = choice["finish_reason"]
                ok = True
                break
        except LLMError as exc:
            if self.on_health is not None:
                self.on_health.fail(str(exc))
            raise
        except Exception as exc:  # noqa: BLE001
            if self.on_health is not None:
                self.on_health.fail(f"stream {type(exc).__name__}: {exc}")
            raise
        finally:
            self._health_end()
        if ok and self.on_health is not None:
            self.on_health.ok(extra={"model": self.endpoint.model})
        for idx in sorted(calls):
            yield {"type": "tool_call", "call": calls[idx]}
        yield {"type": "done", "finish_reason": finish}

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not self.endpoint.configured:
            if self.on_health is not None:
                self.on_health.fail("endpoint not configured")
            raise LLMError(f"{self.name}: endpoint not configured")
        url = f"{self.endpoint.base_url}/embeddings"
        self._health_begin()
        try:
            resp = await self._post(url, json_body={"model": self.endpoint.model, "input": texts})
            if resp.status_code >= 400:
                raise LLMError(f"{self.name}: HTTP {resp.status_code}: {resp.text[:300]}")
            data = sorted(resp.json().get("data", []), key=lambda d: d.get("index", 0))
        except LLMError as exc:
            if self.on_health is not None:
                self.on_health.fail(str(exc))
            raise
        except Exception as exc:  # noqa: BLE001
            if self.on_health is not None:
                self.on_health.fail(f"{type(exc).__name__}: {exc}")
            raise
        finally:
            self._health_end()
        if self.on_health is not None:
            self.on_health.ok(extra={"model": self.endpoint.model})
        return [d["embedding"] for d in data]

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()


# ---------------------------------------------------------------------------
# Test double
# ---------------------------------------------------------------------------

Responder = Callable[[list[dict], dict], "ChatResult | str | Awaitable[ChatResult | str]"]


class ScriptedLLM(LLM):
    """Deterministic fake. ``responder(messages, opts)`` returns text or ChatResult."""

    def __init__(self, responder: Responder | str = "ok", *, name: str = "fake", delay: float = 0.0,
                 chunk: int = 12, chunk_delay: float = 0.0):
        self.responder = responder
        self.name = name
        self.delay = delay
        self.chunk_delay = chunk_delay
        self.chunk = chunk
        self.calls: list[dict] = []
        self.model = name

    async def chat(self, messages, *, tools=None, temperature=0.7, max_tokens=1024, json_mode=False,
                   model=None) -> ChatResult:
        self.calls.append({"messages": messages, "tools": tools, "json_mode": json_mode})
        if self.delay:
            await asyncio.sleep(self.delay)
        if isinstance(self.responder, str):
            out: Any = self.responder
        else:
            out = self.responder(messages, {"tools": tools, "json_mode": json_mode, "n": len(self.calls)})
            if asyncio.iscoroutine(out):
                out = await out
        return out if isinstance(out, ChatResult) else ChatResult(text=str(out))

    async def stream(self, messages, *, tools=None, temperature=0.7, max_tokens=1024, model=None):
        result = await self.chat(messages, tools=tools)
        text = result.text
        for i in range(0, len(text), self.chunk):
            if self.chunk_delay:
                await asyncio.sleep(self.chunk_delay)
            yield {"type": "text", "text": text[i:i + self.chunk]}
        for call in result.tool_calls:
            yield {"type": "tool_call", "call": call}
        yield {"type": "done", "finish_reason": "tool_calls" if result.tool_calls else "stop"}

    async def embed(self, texts):
        from .embedder import hash_embed
        return [hash_embed(t) for t in texts]


def extract_json(text: str) -> dict | None:
    """First balanced JSON object in a model reply (handles fences / prose)."""
    text = strip_reasoning(text or "")
    start = text.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        val = json.loads(text[start:i + 1])
                        if isinstance(val, dict):
                            return val
                    except json.JSONDecodeError:
                        pass
                    break
        start = text.find("{", start + 1)
    return None
