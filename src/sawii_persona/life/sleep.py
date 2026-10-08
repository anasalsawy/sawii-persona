"""Sleep: the nightly circadian pass. Everything slow happens here, never on a turn.

    narrative   A rewrites its own first-person story (the stream it wakes up into)
    psyche      C consolidates readings + hypotheses into one hedged profile;
                unconfirmed hypotheses fade (accessibility decays, nothing is deleted)
    dream       distant memories are recombined into far-associations (where wit comes from)
    thoughts    stale inner thoughts decay
    juice       the day's raw high-variance turns are baked into A's weights (finetune.py)
"""
from __future__ import annotations

import logging
import random

from ..brains.prompts import DREAM, NARRATIVE, PSYCH_PROFILE
from ..llm import extract_json, strip_reasoning
from ..profiles import persona_frame
from ..store import jload, now
from ..timesense import local_dt
from .finetune import FineTuner

log = logging.getLogger(__name__)


class Sleep:
    def __init__(self, mind, finetuner: FineTuner, rng: random.Random | None = None):
        self.mind = mind
        self.ft = finetuner
        self.rng = rng or random.Random()

    def due(self, persona: dict, at: float | None = None) -> bool:
        at = at or now()
        if at - persona["last_sleep_at"] < 18 * 3600:
            return False
        local = local_dt(at, persona["timezone"])
        idle = at - max(persona["last_user_at"], persona["last_reply_at"]) > 45 * 60
        return local.hour == self.mind.s.sleep_hour and idle

    async def run(self, persona: dict) -> dict:
        pid = persona["id"]
        report: dict = {"persona": pid}
        since = max(persona["narrative_at"], persona["last_sleep_at"])
        for step in (self._narrative, self._psyche, self._dream):
            try:
                report.update(await step(persona, since))
            except Exception as exc:  # noqa: BLE001
                log.warning("sleep step %s failed: %s", step.__name__, exc)
                report[step.__name__] = f"failed: {exc}"[:200]
        report["faded_hypotheses"] = await self.mind.memory.fade_hypotheses(pid, self.mind.s.psyche_half_life_days)
        report["decayed_thoughts"] = await self.mind.store.run(
            "UPDATE thoughts SET status='decayed' WHERE persona_id = ? AND status='pending' AND created_at < ?",
            pid, now() - 3 * 86400)
        try:
            report["subconscious"] = await self.ft.consolidate(persona)
        except Exception as exc:  # noqa: BLE001
            log.warning("consolidate failed: %s", exc)
            report["subconscious"] = f"failed: {exc}"[:200]
        await self.mind.store.update("personas", pid, last_sleep_at=now())
        return report

    async def _material(self, pid: int, since: float, limit: int = 40) -> str:
        rows = await self.mind.store.all(
            "SELECT layer, author, kind, text, quote, created_at FROM layers WHERE persona_id = ? AND status='active' "
            "AND created_at >= ? ORDER BY salience DESC, id DESC LIMIT ?", pid, since, limit)
        return "\n".join(f"[{r['layer']}·{r['author']}] {r['text']}" + (f' ("{r["quote"]}")' if r["quote"] else "")
                         for r in rows)

    async def _narrative(self, persona: dict, since: float) -> dict:
        pid = persona["id"]
        material = await self._material(pid, since)
        loops = await self.mind.memory.open_loops(pid)
        if not material and not loops:
            return {"narrative": "unchanged (nothing new)"}
        body = [persona_frame(persona["definition"], persona["name"]),
                "Your previous account:\n" + (persona["narrative"] or "(none yet - this is the first one)"),
                "What happened since (from your memory):\n" + (material or "(little)")]
        if loops:
            body.append("Still open:\n" + "\n".join(f"- {lp['text']}" for lp in loops[:10]))
        kwargs = {"model": persona["a_model_override"]} if persona.get("a_model_override") else {}
        res = await self.mind.a.chat([{"role": "system", "content": NARRATIVE},
                                      {"role": "user", "content": "\n\n".join(body)}],
                                     temperature=0.6, max_tokens=500, **kwargs)
        text = strip_reasoning(res.text)
        if len(text) < 20:
            return {"narrative": "kept previous (empty rewrite)"}
        await self.mind.store.update("personas", pid, narrative=text[:2500], narrative_at=now())
        return {"narrative": "rewritten"}

    async def _psyche(self, persona: dict, since: float) -> dict:
        pid = persona["id"]
        readings = await self.mind.store.all(
            "SELECT text, quote FROM layers WHERE persona_id = ? AND layer='reading' AND status='active' "
            "ORDER BY id DESC LIMIT 40", pid)
        hyps = await self.mind.memory.hypotheses(pid)
        if not readings and not hyps:
            return {"psych_profile": "unchanged (no evidence)"}
        hl = self.mind.s.psyche_half_life_days
        body = ["Previous account:\n" + (persona["psych_profile"] or "(none yet)"),
                "Hypotheses (confidence now):\n" + "\n".join(
                    f"- ({self.mind.memory.effective_confidence(h, hl):.2f}, seen {h['confirmations']}x) {h['claim']} "
                    f"e.g. {', '.join(jload(h['quotes'], [])[:2])}" for h in hyps[:20]),
                "Recent readings:\n" + "\n".join(f"- {r['text']} (\"{r['quote']}\")" for r in readings)]
        res = await self.mind.c.chat([{"role": "system", "content": PSYCH_PROFILE},
                                      {"role": "user", "content": "\n\n".join(body)}],
                                     temperature=0.3, max_tokens=600)
        text = strip_reasoning(res.text)
        if len(text) < 20:
            return {"psych_profile": "kept previous"}
        await self.mind.store.update("personas", pid, psych_profile=text[:3000], psych_at=now())
        return {"psych_profile": "rewritten"}

    async def _dream(self, persona: dict, since: float) -> dict:
        pid = persona["id"]
        rows = await self.mind.store.all(
            "SELECT id, event_id, text, quote FROM layers WHERE persona_id = ? AND status='active' AND salience >= 2 "
            "AND layer IN ('note','fact') ORDER BY id DESC LIMIT 300", pid)
        by_event: dict[int, dict] = {}
        for r in rows:
            by_event.setdefault(r["event_id"], r)
        pool = list(by_event.values())
        if len(pool) < 3:
            return {"dream": "too few memories"}
        picks = self.rng.sample(pool, min(6, len(pool)))
        listing = "\n".join(f"#{p['id']} {p['text']}" + (f' ("{p["quote"]}")' if p["quote"] else "") for p in picks)
        res = await self.mind.a.chat([{"role": "system", "content": DREAM}, {"role": "user", "content": listing}],
                                     temperature=1.0, max_tokens=500, json_mode=True)
        data = extract_json(res.text) or {}
        n = 0
        for a in (data.get("associations") or [])[:3]:
            if isinstance(a, dict) and str(a.get("text", "")).strip():
                await self.mind.store.insert("thoughts", persona_id=pid, text=str(a["text"])[:400], source="dream",
                                             score=0.35, ref=",".join(str(i) for i in (a.get("about_ids") or [])[:6]),
                                             created_at=now())
                n += 1
        return {"dream": f"{n} associations"}
