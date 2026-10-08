"""Durable storage: the single source of truth.

SQLite in WAL mode, accessed through one serialized connection on a worker
thread. Every table is scoped by ``persona_id``; nothing crosses personas.
The domain logic lives in ``memory/`` and ``life/``; this module owns the
schema and the plumbing only.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import time
from typing import Any, Iterable

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- A persona is an API key: same key from any device = same being.
CREATE TABLE IF NOT EXISTS personas (
    id            INTEGER PRIMARY KEY,
    key_hash      TEXT NOT NULL UNIQUE,
    key_hint      TEXT NOT NULL DEFAULT '',
    name          TEXT NOT NULL DEFAULT '',
    definition    TEXT NOT NULL DEFAULT '',       -- written by the user (preset or clone)
    preset        TEXT NOT NULL DEFAULT '',
    user_name     TEXT NOT NULL DEFAULT '',
    timezone      TEXT NOT NULL DEFAULT 'UTC',
    contact       TEXT NOT NULL DEFAULT '{}',     -- {"sms": "+1..", "whatsapp": .., "email": .., "telegram": chat_id}
    preferred_channel TEXT NOT NULL DEFAULT 'app',
    a_model_override TEXT NOT NULL DEFAULT '',    -- fine-tuned adapter, plug and play
    narrative     TEXT NOT NULL DEFAULT '',       -- A's own first-person story, rewritten at sleep
    narrative_at  REAL NOT NULL DEFAULT 0,
    psych_profile TEXT NOT NULL DEFAULT '',       -- C's longitudinal read of the user (hedged)
    psych_at      REAL NOT NULL DEFAULT 0,
    mood          TEXT NOT NULL DEFAULT '{}',     -- A's current chord (declared + drift)
    user_mood     TEXT NOT NULL DEFAULT '{}',     -- user's chord as C reads it (rolling)
    baseline      BLOB,                            -- subconscious baseline (EMA of ordinary)
    baseline_model TEXT NOT NULL DEFAULT '',
    last_user_at  REAL NOT NULL DEFAULT 0,
    last_reply_at REAL NOT NULL DEFAULT 0,
    last_sleep_at REAL NOT NULL DEFAULT 0,
    created_at    REAL NOT NULL
);

-- The verbatim journal. Raw text is never rewritten. Quote-or-die grounds here.
CREATE TABLE IF NOT EXISTS turns (
    id          INTEGER PRIMARY KEY,
    persona_id  INTEGER NOT NULL REFERENCES personas(id) ON DELETE CASCADE,
    user_text   TEXT NOT NULL,
    reply_text  TEXT NOT NULL DEFAULT '',
    channel     TEXT NOT NULL DEFAULT 'app',
    surface     TEXT NOT NULL DEFAULT '',       -- device/app: provenance only, never memory content
    kind        TEXT NOT NULL DEFAULT 'chat',   -- chat | outreach (persona spoke first)
    started_at  REAL NOT NULL,
    ended_at    REAL NOT NULL DEFAULT 0,
    gap_seconds REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_turns_p ON turns(persona_id, id);

-- One memory record per event (turn); layers hang off it. Recall returns the
-- whole stitched record: fact (A) + collateral (B) + affect/readings (C).
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY,
    persona_id  INTEGER NOT NULL REFERENCES personas(id) ON DELETE CASCADE,
    turn_id     INTEGER REFERENCES turns(id) ON DELETE SET NULL,
    chord       TEXT NOT NULL DEFAULT '{}',     -- the moment's feeling (C)
    surface     TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_events_p ON events(persona_id, id);

CREATE TABLE IF NOT EXISTS layers (
    id          INTEGER PRIMARY KEY,
    persona_id  INTEGER NOT NULL REFERENCES personas(id) ON DELETE CASCADE,
    event_id    INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    layer       TEXT NOT NULL CHECK (layer IN ('fact','collateral','note','reading','afterthought')),
    author      TEXT NOT NULL CHECK (author IN ('A','B','C')),
    kind        TEXT NOT NULL DEFAULT 'detail', -- preference|plan|detail|aside|joke|callback|...
    text        TEXT NOT NULL,                  -- the author's statement
    quote       TEXT NOT NULL DEFAULT '',       -- user's verbatim words (required for user claims)
    anchor      TEXT NOT NULL DEFAULT '',       -- topic/event it attaches to
    chord       TEXT NOT NULL DEFAULT '{}',
    salience    INTEGER NOT NULL DEFAULT 1,
    status      TEXT NOT NULL DEFAULT 'active', -- active | superseded | wiped
    superseded_by INTEGER,
    interpretations TEXT NOT NULL DEFAULT '[]', -- reconsolidation: later readings; quote stays carved
    embedding   BLOB,
    embed_model TEXT NOT NULL DEFAULT '',
    hvec        BLOB,                            -- local hash vector, always present
    recalled_count INTEGER NOT NULL DEFAULT 0,
    last_recalled_at REAL NOT NULL DEFAULT 0,
    activation  REAL NOT NULL DEFAULT 0,          -- priming: half-touched by cues, fades, may pop
    activated_at REAL NOT NULL DEFAULT 0,
    popped_at   REAL NOT NULL DEFAULT 0,
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_layers_p ON layers(persona_id, status, id);

-- C's psyche layer: hypotheses about the person, never facts.
CREATE TABLE IF NOT EXISTS hypotheses (
    id            INTEGER PRIMARY KEY,
    persona_id    INTEGER NOT NULL REFERENCES personas(id) ON DELETE CASCADE,
    claim         TEXT NOT NULL,
    quotes        TEXT NOT NULL DEFAULT '[]',
    confidence    REAL NOT NULL DEFAULT 0.4,
    confirmations INTEGER NOT NULL DEFAULT 1,
    contradictions INTEGER NOT NULL DEFAULT 0,
    status        TEXT NOT NULL DEFAULT 'active', -- active | revised | faded
    revision_of   INTEGER,
    embedding     BLOB,
    embed_model   TEXT NOT NULL DEFAULT '',
    hvec          BLOB,
    last_confirmed_at REAL NOT NULL,
    created_at    REAL NOT NULL
);

-- People / projects as first-class things.
CREATE TABLE IF NOT EXISTS entities (
    id           INTEGER PRIMARY KEY,
    persona_id   INTEGER NOT NULL REFERENCES personas(id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    kind         TEXT NOT NULL DEFAULT 'person',
    account      TEXT NOT NULL DEFAULT '',
    last_quote   TEXT NOT NULL DEFAULT '',
    mention_count INTEGER NOT NULL DEFAULT 1,
    updated_at   REAL NOT NULL,
    UNIQUE (persona_id, name)
);

-- B's open loops: mutable working state (tasks, promises, reminders). Not history.
CREATE TABLE IF NOT EXISTS loops (
    id          INTEGER PRIMARY KEY,
    persona_id  INTEGER NOT NULL REFERENCES personas(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL DEFAULT 'task',   -- task|promise|reminder|schedule|follow_up
    text        TEXT NOT NULL,
    quote       TEXT NOT NULL DEFAULT '',
    due_at      REAL,
    status      TEXT NOT NULL DEFAULT 'open',   -- open|done|missed|cancelled
    notified_at REAL NOT NULL DEFAULT 0,
    turn_id     INTEGER,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);

-- B's verdicts: the integrity record A sees next turn.
CREATE TABLE IF NOT EXISTS verdicts (
    id          INTEGER PRIMARY KEY,
    persona_id  INTEGER NOT NULL REFERENCES personas(id) ON DELETE CASCADE,
    turn_id     INTEGER,
    level       TEXT NOT NULL,                  -- GREEN | YELLOW | RED
    rationale   TEXT NOT NULL DEFAULT '',
    findings    TEXT NOT NULL DEFAULT '[]',
    corrected   INTEGER NOT NULL DEFAULT 0,
    created_at  REAL NOT NULL
);

-- The thought stream: an inner monologue the companion has and mostly never says.
CREATE TABLE IF NOT EXISTS thoughts (
    id          INTEGER PRIMARY KEY,
    persona_id  INTEGER NOT NULL REFERENCES personas(id) ON DELETE CASCADE,
    text        TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT 'wander', -- wander|dream|consult|loop|afterthought|correction
    score       REAL NOT NULL DEFAULT 0.0,
    status      TEXT NOT NULL DEFAULT 'pending',-- pending|voiced|inhibited|decayed
    ref         TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL
);

-- Outbox: everything the persona says first.
CREATE TABLE IF NOT EXISTS outbox (
    id          INTEGER PRIMARY KEY,
    persona_id  INTEGER NOT NULL REFERENCES personas(id) ON DELETE CASCADE,
    channel     TEXT NOT NULL DEFAULT 'app',
    text        TEXT NOT NULL,
    reason      TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'queued', -- queued|sent|failed|read
    error       TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL,
    sent_at     REAL NOT NULL DEFAULT 0
);

-- The subconscious buffer: raw high-variance "juice" for nightly fine-tuning.
CREATE TABLE IF NOT EXISTS juice (
    id          INTEGER PRIMARY KEY,
    persona_id  INTEGER NOT NULL REFERENCES personas(id) ON DELETE CASCADE,
    turn_id     INTEGER,
    raw_transcript TEXT NOT NULL DEFAULT '',     -- byte-exact user turn
    reply       TEXT NOT NULL DEFAULT '',        -- A's reply (training target)
    style_markers TEXT NOT NULL DEFAULT '[]',
    variance    REAL NOT NULL,
    sensory_vector BLOB,
    tier        TEXT NOT NULL DEFAULT 'raw',     -- raw|exported|baked|compressed
    compressed  TEXT NOT NULL DEFAULT '',
    raw_chars   INTEGER NOT NULL DEFAULT 0,
    exported_at REAL NOT NULL DEFAULT 0,
    baked_at    REAL NOT NULL DEFAULT 0,
    compressed_at REAL NOT NULL DEFAULT 0,
    created_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS finetune_jobs (
    id          INTEGER PRIMARY KEY,
    persona_id  INTEGER NOT NULL REFERENCES personas(id) ON DELETE CASCADE,
    provider    TEXT NOT NULL,
    job_ref     TEXT NOT NULL DEFAULT '',
    dataset     TEXT NOT NULL DEFAULT '',
    juice_ids   TEXT NOT NULL DEFAULT '[]',
    status      TEXT NOT NULL DEFAULT 'pending', -- pending|running|succeeded|failed
    output_model TEXT NOT NULL DEFAULT '',
    detail      TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);

-- Deep lane jobs (OpenHands / parallel workers).
CREATE TABLE IF NOT EXISTS deep_jobs (
    id          INTEGER PRIMARY KEY,
    persona_id  INTEGER NOT NULL REFERENCES personas(id) ON DELETE CASCADE,
    task        TEXT NOT NULL,
    mode        TEXT NOT NULL DEFAULT 'auto',
    status      TEXT NOT NULL DEFAULT 'queued',  -- queued|running|done|failed
    result      TEXT NOT NULL DEFAULT '',
    trace       TEXT NOT NULL DEFAULT '[]',
    seen        INTEGER NOT NULL DEFAULT 0,
    created_at  REAL NOT NULL,
    finished_at REAL NOT NULL DEFAULT 0
);
"""


# additive migrations for databases created by earlier versions
MIGRATIONS = [
    ("layers", "activation", "REAL NOT NULL DEFAULT 0"),
    ("layers", "activated_at", "REAL NOT NULL DEFAULT 0"),
    ("layers", "popped_at", "REAL NOT NULL DEFAULT 0"),
]


class Store:
    def __init__(self, path: str):
        self.path = path
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(SCHEMA)
            for table, col, ddl in MIGRATIONS:
                cols = {r[1] for r in self._conn.execute(f"PRAGMA table_info({table})")}
                if col not in cols:
                    self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")

    # -- sync core ---------------------------------------------------------
    def _run(self, sql: str, params: Iterable[Any] = (), *, many: bool = False):
        with self._lock:
            cur = self._conn.executemany(sql, params) if many else self._conn.execute(sql, tuple(params))
            return cur

    def _fetchall(self, sql, params=()):
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, tuple(params)).fetchall()]

    def _fetchone(self, sql, params=()):
        with self._lock:
            r = self._conn.execute(sql, tuple(params)).fetchone()
            return dict(r) if r else None

    def _insert(self, table: str, values: dict) -> int:
        cols = ", ".join(values)
        qs = ", ".join("?" for _ in values)
        with self._lock:
            cur = self._conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({qs})", tuple(values.values()))
            return int(cur.lastrowid)

    def _update(self, table: str, row_id: int, values: dict, *, persona_id: int | None = None) -> int:
        sets = ", ".join(f"{k} = ?" for k in values)
        sql = f"UPDATE {table} SET {sets} WHERE id = ?"
        params = [*values.values(), row_id]
        if persona_id is not None:
            sql += " AND persona_id = ?"
            params.append(persona_id)
        with self._lock:
            return self._conn.execute(sql, params).rowcount

    def _tx(self, fn):
        """Run ``fn(conn)`` inside one transaction."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                out = fn(self._conn)
                self._conn.execute("COMMIT")
                return out
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    # -- async facade ------------------------------------------------------
    async def all(self, sql: str, *params) -> list[dict]:
        return await asyncio.to_thread(self._fetchall, sql, params)

    async def one(self, sql: str, *params) -> dict | None:
        return await asyncio.to_thread(self._fetchone, sql, params)

    async def run(self, sql: str, *params) -> int:
        cur = await asyncio.to_thread(self._run, sql, params)
        return cur.rowcount

    async def insert(self, table: str, **values) -> int:
        return await asyncio.to_thread(self._insert, table, values)

    async def update(self, table: str, row_id: int, persona_id: int | None = None, **values) -> int:
        return await asyncio.to_thread(self._update, table, row_id, values, persona_id=persona_id)

    async def tx(self, fn):
        return await asyncio.to_thread(self._tx, fn)

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def now() -> float:
    return time.time()


def jdump(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, separators=(",", ":"))


def jload(v: str | None, default: Any = None) -> Any:
    if not v:
        return default
    try:
        return json.loads(v)
    except (TypeError, json.JSONDecodeError):
        return default
