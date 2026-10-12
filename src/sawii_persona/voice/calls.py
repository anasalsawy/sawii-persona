"""Phone calls with two lobes: B directs, A speaks.

An outbound call is a small two-lobe performance, and it only works because
there are two minds:

  * the DIRECTOR (B) knows what the call is *for*. It composes the opening line.
  * the CALLER (A) speaks it in the persona's own voice, hears the spoken reply,
    and answers in character.

Twilio captures the person's speech (<Gather input="speech">) and posts it back,
so the call is a real back-and-forth - not a robocall reading a script.

Everything degrades honestly: with no Twilio configured the tool reports that
calling is unavailable rather than pretending.
"""
from __future__ import annotations

import logging
from xml.sax.saxutils import escape

from ..brains.prompts import CALL_DIRECTOR, CALL_SPEAK
from ..llm import strip_reasoning
from ..store import jload, now

log = logging.getLogger(__name__)


def _twiml(say: str, *, action: str, gather: bool = True) -> str:
    """One spoken turn; optionally listen for the reply and loop back to action."""
    body = escape((say or "")[:1200])
    if not gather:
        return f'<?xml version="1.0" encoding="UTF-8"?><Response><Say>{body}</Say></Response>'
    return (
        '<?xml version="1.0" encoding="UTF-8"?><Response>'
        f'<Say>{body}</Say>'
        f'<Gather input="speech" speechTimeout="auto" action="{escape(action)}" method="POST"/>'
        f'<Redirect method="POST">{escape(action)}</Redirect>'
        '</Response>'
    )


class Caller:
    """Places and drives two-lobe phone calls."""

    def __init__(self, mind, channels, settings):
        self.mind = mind
        self.channels = channels
        self.s = settings

    def available(self) -> bool:
        return bool(self.s.twilio_sid and self.s.twilio_token and self.s.twilio_from)

    def _number(self, persona: dict, to: str) -> str:
        contact = jload(persona.get("contact"), {}) or {}
        to = (to or "").strip()
        if not to or to.lower() in ("me", "self"):
            return str(contact.get("sms") or contact.get("whatsapp") or "")
        return to

    async def _director_opening(self, persona: dict, purpose: str) -> str:
        """B composes the call: what to open with, given why we are calling."""
        try:
            res = await self.mind.b.chat([
                {"role": "system", "content": CALL_DIRECTOR},
                {"role": "user", "content": f"Who you are: {persona.get('name') or 'the companion'}\n"
                                            f"Who you are calling: {persona.get('user_name') or 'the person'}\n"
                                            f"Why: {purpose}"}],
                temperature=0.4, max_tokens=200)
            line = strip_reasoning(res.text).strip()
            if line:
                return line[:600]
        except Exception as exc:  # noqa: BLE001
            log.warning("call director failed: %s", exc)
        return f"Hi, it's {persona.get('name') or 'me'} - I'm calling about {purpose[:200]}."

    async def _caller_reply(self, persona: dict, purpose: str, said: str) -> str:
        """A answers what the person just said, staying on the call's purpose."""
        try:
            res = await self.mind.a.chat([
                {"role": "system", "content": CALL_SPEAK.format(name=persona.get("name") or "me",
                                                                 who=persona.get("user_name") or "them",
                                                                 purpose=purpose[:400])},
                {"role": "user", "content": f'They just said: "{said[:600]}". Reply in one or two short '
                                            f"spoken sentences. If the call is done, end it warmly."}],
                temperature=0.6, max_tokens=160)
            return strip_reasoning(res.text).strip()[:600]
        except Exception as exc:  # noqa: BLE001
            log.warning("call caller reply failed: %s", exc)
            return "Sorry, say that again?"

    async def place(self, persona: dict, to: str, purpose: str) -> dict:
        """B directs, A opens, Twilio dials. Returns a small status dict."""
        if not self.available():
            return {"ok": False, "error": "calling is not configured on this server"}
        number = self._number(persona, to)
        if not number:
            return {"ok": False, "error": "no number to call"}
        opening = await self._director_opening(persona, purpose)
        from urllib.parse import quote
        base = (self.s.public_base_url or "").rstrip("/")
        step_url = f"{base}/hooks/twilio/voice/step?p={persona['id']}&purpose={quote(purpose[:300])}"
        twiml = _twiml(opening, action=step_url)
        try:
            await self.channels._twilio("Calls.json", {"To": number, "From": self.s.twilio_from,
                                                       "Twiml": twiml})
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"call failed: {exc}"[:200]}
        try:
            tid = await self.mind.memory.new_turn(persona["id"], "", channel="call", kind="outreach")
            await self.mind.memory.finish_turn(tid, f"(I called {number} about: {purpose}. Opened with: {opening})")
            await self.mind.store.update("personas", persona["id"], last_reply_at=now())
        except Exception:  # noqa: BLE001
            pass
        self.mind.bus.publish(persona["id"], {"type": "call", "state": "dialing", "to": number,
                                              "purpose": purpose[:200], "opening": opening})
        return {"ok": True, "to": number, "opening": opening}

    async def step(self, persona: dict, purpose: str, speech: str) -> str:
        """One spoken turn: the person said `speech`; return what A should say next."""
        said = (speech or "").strip()
        if not said:
            return "Are you still there?"
        return await self._caller_reply(persona, purpose, said)
