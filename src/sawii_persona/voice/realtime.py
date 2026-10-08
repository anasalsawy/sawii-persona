"""Realtime voice - the lag budget is everything.

Pipeline (each stage starts before the previous one ends):

    mic ──► ASR ──► A streams tokens ──► sentence chunker ──► TTS per sentence ──► speaker
            (browser Web Speech      (first chunk cut early   (sentence k+1 is synthesised
             partials, or server      at a comma so speech      while sentence k plays)
             ASR on utterance end)    starts in ~1 token-burst)

Barge-in: the person starts talking -> client sends {"type":"interrupt"} -> audio
for the rest of the reply is dropped instantly; the turn still completes so
memory stays consistent. Pop-ups (outreach) arrive on the same socket and are spoken.

WebSocket protocol (JSON text frames; audio as binary frames):
  client -> server  {"type":"hello","key":"sp_..."}              (or ?key= / Authorization header)
                    {"type":"text","text":"..."}                 (client-side ASR - zero latency path)
                    <binary audio chunk> ... {"type":"end_utterance","mime":"audio/webm"}
                    {"type":"interrupt"}
  server -> client  {"type":"ready","persona":..,"tts":"server|client"}
                    {"type":"transcript","text":".."}            (server ASR result)
                    {"type":"delta","text":".."}                 (live text)
                    {"type":"say","seq":n,"text":".."}           (client TTS mode)
                    {"type":"audio","seq":n,"mime":"audio/mpeg"} followed by one binary frame
                    {"type":"done","text":"..","first_audio_ms":..}
                    {"type":"message","text":".."}               (the persona speaking first)
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time

try:
    import httpx  # type: ignore
except ImportError:  # pragma: no cover
    import httpx2 as httpx  # type: ignore

from ..config import ModelEndpoint

log = logging.getLogger(__name__)

_END = re.compile(r"([.!?…]+[\"')\]]*\s+|\n+)")


class SentenceChunker:
    """Split a token stream into speakable chunks; the first one is cut early."""

    def __init__(self, first_min: int = 24, min_len: int = 12):
        self.buf = ""
        self.first = True
        self.first_min = first_min
        self.min_len = min_len

    def feed(self, piece: str) -> list[str]:
        self.buf += piece
        out = []
        while True:
            m = _END.search(self.buf)
            cut = m.end() if m and len(self.buf[:m.end()].strip()) >= self.min_len else None
            if cut is None and self.first and len(self.buf) >= self.first_min:
                c = self.buf.find(", ", self.first_min // 2)
                if c != -1:
                    cut = c + 2
            if cut is None:
                break
            chunk, self.buf = self.buf[:cut].strip(), self.buf[cut:]
            if chunk:
                out.append(chunk)
                self.first = False
        return out

    def flush(self) -> list[str]:
        rest, self.buf = self.buf.strip(), ""
        return [rest] if rest else []


class SpeechIO:
    """OpenAI-compatible ASR (/audio/transcriptions) and TTS (/audio/speech)."""

    def __init__(self, asr: ModelEndpoint, tts: ModelEndpoint, voice: str = "alloy",
                 http: "httpx.AsyncClient | None" = None):
        self.asr, self.tts, self.voice = asr, tts, voice
        self._http = http

    def http(self) -> "httpx.AsyncClient":
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=60)
        return self._http

    @property
    def has_asr(self) -> bool:
        return self.asr.configured

    @property
    def has_tts(self) -> bool:
        return self.tts.configured

    async def transcribe(self, audio: bytes, mime: str = "audio/webm") -> str:
        ext = {"audio/webm": "webm", "audio/wav": "wav", "audio/mpeg": "mp3", "audio/ogg": "ogg"}.get(mime, "webm")
        r = await self.http().post(f"{self.asr.base_url}/audio/transcriptions",
                                   headers={"Authorization": f"Bearer {self.asr.api_key}"},
                                   data={"model": self.asr.model}, files={"file": (f"speech.{ext}", audio, mime)})
        r.raise_for_status()
        return (r.json().get("text") or "").strip()

    async def synthesize(self, text: str) -> bytes:
        r = await self.http().post(f"{self.tts.base_url}/audio/speech",
                                   headers={"Authorization": f"Bearer {self.tts.api_key}"},
                                   json={"model": self.tts.model, "voice": self.voice, "input": text,
                                         "response_format": "mp3"})
        r.raise_for_status()
        return r.content


class VoiceSession:
    """One live voice connection. ``send_json``/``send_bytes`` are the socket's."""

    def __init__(self, mind, persona: dict, speech: SpeechIO, send_json, send_bytes):
        self.mind = mind
        self.persona = persona
        self.speech = speech
        self.send_json = send_json
        self.send_bytes = send_bytes
        self.audio = bytearray()
        self.speaking = False
        self.interrupted = False
        self.turn_task: asyncio.Task | None = None
        self._send_lock = asyncio.Lock()

    async def _emit(self, obj: dict, blob: bytes | None = None) -> None:
        async with self._send_lock:
            await self.send_json(obj)
            if blob is not None:
                await self.send_bytes(blob)

    async def on_text(self, text: str) -> None:
        if self.turn_task and not self.turn_task.done():
            self.interrupted = True  # new speech while talking = barge-in
            await self.turn_task
        self.interrupted = False
        self.turn_task = asyncio.create_task(self._turn(text))

    async def on_audio(self, chunk: bytes) -> None:
        if len(self.audio) < 25_000_000:
            self.audio.extend(chunk)

    async def on_end_utterance(self, mime: str) -> None:
        audio, self.audio = bytes(self.audio), bytearray()
        if not audio:
            return
        if not self.speech.has_asr:
            await self._emit({"type": "error", "detail": "server ASR not configured; send text frames"})
            return
        text = await self.speech.transcribe(audio, mime)
        await self._emit({"type": "transcript", "text": text})
        if text:
            await self.on_text(text)

    async def on_interrupt(self) -> None:
        self.interrupted = True

    async def speak_text(self, text: str, kind: str = "message") -> None:
        """Pop-ups: the persona speaking first on an open voice line."""
        await self._emit({"type": kind, "text": text})
        chunker = SentenceChunker()
        for i, s in enumerate(chunker.feed(text + " ") + chunker.flush()):
            await self._say(i, s)

    async def _say(self, seq: int, sentence: str) -> None:
        if self.interrupted:
            return
        if self.speech.has_tts:
            try:
                audio = await self.speech.synthesize(sentence)
            except Exception as exc:  # noqa: BLE001
                log.warning("tts failed: %s", exc)
                await self._emit({"type": "say", "seq": seq, "text": sentence})
                return
            if not self.interrupted:
                await self._emit({"type": "audio", "seq": seq, "mime": "audio/mpeg", "text": sentence}, audio)
        else:
            await self._emit({"type": "say", "seq": seq, "text": sentence})

    async def _turn(self, text: str) -> None:
        t0 = time.monotonic()
        first_audio = None
        chunker = SentenceChunker()
        seq = 0
        tts_queue: asyncio.Queue = asyncio.Queue()

        async def speaker():  # synthesis of k+1 overlaps playback/transfer of k
            nonlocal first_audio
            while True:
                item = await tts_queue.get()
                if item is None:
                    return
                n, sentence = item
                await self._say(n, sentence)
                if first_audio is None:
                    first_audio = round((time.monotonic() - t0) * 1000)

        spk = asyncio.create_task(speaker())
        reply = ""
        try:
            async for ev in self.mind.respond(self.persona["id"], text, channel="voice"):
                if ev["type"] == "delta":
                    reply += ev["text"]
                    if not self.interrupted:
                        await self._emit({"type": "delta", "text": ev["text"]})
                    for s in chunker.feed(ev["text"]):
                        await tts_queue.put((seq, s))
                        seq += 1
            for s in chunker.flush():
                await tts_queue.put((seq, s))
                seq += 1
        finally:
            await tts_queue.put(None)
            await spk
        await self._emit({"type": "done", "text": reply, "first_audio_ms": first_audio,
                          "interrupted": self.interrupted})

    async def handle_frame(self, frame: dict) -> None:
        kind = frame.get("type")
        if kind == "text":
            await self.on_text(str(frame.get("text", ""))[:4000])
        elif kind == "end_utterance":
            await self.on_end_utterance(str(frame.get("mime", "audio/webm")))
        elif kind == "interrupt":
            await self.on_interrupt()

    async def finish(self) -> None:
        if self.turn_task:
            await asyncio.gather(self.turn_task, return_exceptions=True)


def parse_frame(raw: str) -> dict:
    try:
        v = json.loads(raw)
        return v if isinstance(v, dict) else {}
    except json.JSONDecodeError:
        return {}
