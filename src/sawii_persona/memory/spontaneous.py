"""Spontaneous recall: remembering things all of a sudden.

A person does not only remember what they look for. Memories *arrive*:

  priming      every message half-touches old memories that do not quite surface.
               They gain activation, which fades slowly (half-life ~1.5h).
  chains       a memory that comes up warms its neighbours - same moment, same
               person, same feeling, similar words. One thing leads to another,
               and across turns the chain keeps walking (2nd, 3rd hop).
  pop          when a primed memory crosses the threshold - two turns later, or an
               hour later while idle - it pops: "oh wait, I just remembered...".
               It comes into A's next turn, or, between conversations, the heartbeat
               turns it into a spontaneous message.
  late recall  after A answers, a slower and wider search keeps going in the
               background (incubation). If it finds something A should have
               remembered, it comes back seconds later as an "oh wait" afterthought.

Randomness lives only in *which* true memory surfaces and *when*; content is
always the stored record with the user's verbatim words.
"""
from __future__ import annotations

import math
import re

import numpy as np

from .. import emotion
from ..embedder import HASH_MODEL, Embedder, cosine_matrix, hash_embed
from ..store import Store, jload, now
from .records import content_words, vec_matrix

POP_THRESHOLD = 1.0


class Spontaneous:
    def __init__(self, store: Store, embedder: Embedder, *, half_life_min: float = 90.0, band: float = 0.30):
        self.store = store
        self.embedder = embedder
        self.half_life = half_life_min * 60
        self.band = band

    def level(self, row: dict, t: float) -> float:
        if not row["activation"]:
            return 0.0
        return float(row["activation"]) * 0.5 ** (max(0.0, t - row["activated_at"]) / self.half_life)

    async def _rows(self, pid: int) -> list[dict]:
        return await self.store.all(
            "SELECT l.id, l.event_id, l.layer, l.kind, l.text, l.quote, l.anchor, l.chord, l.salience, l.embedding, "
            "l.embed_model, l.hvec, l.activation, l.activated_at, l.popped_at, l.last_recalled_at, l.created_at, "
            "e.turn_id FROM layers l JOIN events e ON e.id = l.event_id "
            "WHERE l.persona_id = ? AND l.status = 'active' ORDER BY l.id DESC LIMIT 3000", pid)

    async def _similarity(self, rows: list[dict], text: str) -> np.ndarray:
        q_model, q_vec = await self.embedder.embed_one(text)
        sem = np.zeros(len(rows), dtype=np.float32)
        same = [i for i, r in enumerate(rows) if r["embed_model"] == q_model and q_model != HASH_MODEL]
        if same:
            mat = vec_matrix([rows[i] for i in same])
            if mat.shape[1] == len(q_vec):
                sem[same] = cosine_matrix(np.asarray(q_vec, dtype=np.float32), mat)
        hmat = vec_matrix(rows, "hvec")
        if hmat.size:
            sem = np.maximum(sem, cosine_matrix(np.asarray(hash_embed(text), dtype=np.float32), hmat) * 0.85)
        return sem

    @staticmethod
    def _names(text: str) -> set[str]:
        return {w.lower() for w in re.findall(r"\b[A-Z][a-z]{2,}\b", text or "")}

    async def prime(self, persona: dict, cue: str, *, surfaced: list[int], mood: dict | None = None) -> dict:
        """After a turn: near-misses gain activation; what surfaced spreads to its neighbours."""
        pid = persona["id"]
        rows = await self._rows(pid)
        if not rows:
            return {"primed": 0}
        t = now()
        sem = await self._similarity(rows, cue)
        mood = emotion.normalize(mood or {})
        surfaced_set = set(surfaced)
        levels = {r["id"]: self.level(r, t) for r in rows}
        gain: dict[int, float] = {}
        cue_words = content_words(cue)
        cue_names = self._names(cue)
        for i, r in enumerate(rows):
            if r["id"] in surfaced_set:
                continue
            s = float(sem[i])
            g = 0.0
            if 0.10 <= s < self.band + 0.25:          # half-touched: almost came up
                g += s * 1.2
            words = content_words(f"{r['text']} {r['quote']} {r['anchor']}")
            if cue_words and len(cue_words & words) >= 1:
                g += 0.06 * min(3, len(cue_words & words))
            if cue_names and cue_names & self._names(f"{r['text']} {r['quote']}"):
                g += 0.30                               # someone mentioned
            ch = jload(r["chord"], {})
            if mood and ch:
                res = emotion.resonance(mood, ch)
                if res >= 0.6:
                    g += 0.35 * res                     # the feeling rhymes
            if g:
                gain[r["id"]] = gain.get(r["id"], 0.0) + g * (0.6 + 0.2 * r["salience"])
        # chains: surfaced and strongly primed memories warm their neighbours
        by_event: dict[int, list[dict]] = {}
        for r in rows:
            by_event.setdefault(r["event_id"], []).append(r)
        # chains walk one hop per prime: only what surfaced, or was already warm before this cue, spreads
        hot = [r for r in rows if r["id"] in surfaced_set or levels[r["id"]] >= 0.6]
        if hot:
            hmat = vec_matrix(rows, "hvec")
            for h in hot[:12]:
                strength = 1.0 if h["id"] in surfaced_set else 0.6
                for sib in by_event.get(h["event_id"], []):          # same moment
                    if sib["id"] != h["id"] and sib["id"] not in surfaced_set:
                        gain[sib["id"]] = gain.get(sib["id"], 0) + 0.30 * strength
                h_names = self._names(f"{h['text']} {h['quote']}")
                hv = vec_matrix([h], "hvec")
                sims = cosine_matrix(hv[0], hmat) if hmat.size and hv.size else np.zeros(len(rows))
                h_chord = jload(h["chord"], {})
                for i, r in enumerate(rows):
                    if r["id"] in surfaced_set or r["event_id"] == h["event_id"]:
                        continue
                    link = 0.0
                    if h_names and h_names & self._names(f"{r['text']} {r['quote']}"):
                        link += 0.30                                # same person
                    if float(sims[i]) >= 0.5:
                        link += 0.20                                # similar words
                    ch = jload(r["chord"], {})
                    if h_chord and ch and emotion.resonance(h_chord, ch) >= 0.85:
                        link += 0.15                                # same feeling
                    if link:
                        gain[r["id"]] = gain.get(r["id"], 0) + link * strength
        changed = 0
        for r in rows:
            rid = r["id"]
            if rid in surfaced_set:
                await self.store.run("UPDATE layers SET activation = 0, activated_at = ? WHERE id = ?", t, rid)
                continue
            if rid in gain:
                new = min(2.5, levels[rid] + gain[rid])
                await self.store.run("UPDATE layers SET activation = ?, activated_at = ? WHERE id = ?", new, t, rid)
                changed += 1
        return {"primed": changed}

    async def pops(self, persona: dict, *, limit: int = 1, at: float | None = None,
                   min_age_s: float = 600) -> list[dict]:
        """Memories that crossed the threshold and are ready to arrive unbidden."""
        t = at or now()
        rows = await self._rows(persona["id"])
        ready = []
        for r in rows:
            lvl = self.level(r, t)
            if lvl < POP_THRESHOLD:
                continue
            if t - r["created_at"] < min_age_s:               # not something from minutes ago
                continue
            if r["popped_at"] and t - r["popped_at"] < 3 * 86400:
                continue
            if r["last_recalled_at"] and t - r["last_recalled_at"] < 1800:
                continue
            ready.append((lvl, r))
        ready.sort(key=lambda x: -x[0])
        out = []
        used_events: set[int] = set()
        for lvl, r in ready:
            if len(out) >= limit:
                break
            if r["event_id"] in used_events:
                continue
            used_events.add(r["event_id"])
            # one memory, all its layers: the whole moment has now been remembered
            await self.store.run("UPDATE layers SET popped_at = ?, activation = 0.3, activated_at = ?, "
                                 "last_recalled_at = ?, recalled_count = recalled_count + 1 WHERE event_id = ?",
                                 t, t, t, r["event_id"])
            out.append({k: r[k] for k in ("id", "event_id", "layer", "kind", "text", "quote", "anchor", "salience",
                                          "created_at")} | {"level": round(lvl, 2)})
        return out

    async def late_recall(self, persona: dict, cue: str, *, already: list[int], current_turn: int | None,
                          threshold: float | None = None) -> dict | None:
        """Incubation: the slower, wider search that finishes after A already answered."""
        rows = [r for r in await self._rows(persona["id"]) if r["turn_id"] != current_turn]
        if not rows:
            return None
        t = now()
        sem = await self._similarity(rows, cue)
        cue_names = self._names(cue)
        seen_events = {r["event_id"] for r in rows if r["id"] in set(already)}
        best, best_score = None, 0.0
        for i, r in enumerate(rows):
            if r["id"] in already or r["event_id"] in seen_events or r["salience"] < 2:
                continue
            if r["layer"] not in ("note", "fact", "collateral"):
                continue
            if t - r["created_at"] < 600:
                continue
            score = float(sem[i]) + 0.1 * r["salience"]
            if cue_names and cue_names & self._names(f"{r['text']} {r['quote']}"):
                score += 0.25
            if score > best_score:
                best, best_score = r, score
        need = threshold if threshold is not None else self.band + 0.25
        if best is None or best_score < need:
            return None
        await self.store.run("UPDATE layers SET last_recalled_at = ?, recalled_count = recalled_count + 1 "
                             "WHERE id = ?", t, best["id"])
        return {k: best[k] for k in ("id", "event_id", "layer", "kind", "text", "quote", "anchor", "salience",
                                     "created_at")} | {"score": round(best_score, 3)}


def render_pop(r: dict, tz_fmt) -> str:
    line = f"{r['text']}"
    if r["quote"] and r["quote"] not in r["text"]:
        line += f' (they said: "{r["quote"]}")'
    return f"• {line} - from {tz_fmt(r['created_at'])}"
