# Production readiness audit — Sawii Persona

Scope reviewed: this repo (`sawii-persona`, the three-lobe backend + built-in web app),
the companion app surface (`src/sawii_persona/web/index.html` — the app the person uses),
and the Featherless/Railway deployment path.

## Verdict

The *architecture* is production-shaped: one turn = A streams, B/C run after, all slow
work is off the critical path, storage is a single SQLite writer, every endpoint is
persona-scoped. What is missing is the **shell around it**: compression, CORS, caching,
observability, migration/backup safety, and a handful of features that are half-wired
(tables exist, no endpoint). Latency is dominated by three things, all fixable without
touching the product design:

1. **Recall does a full unfiltered scan of every active layer on every turn** and
   blocks the turn start on a *remote* embedding call.
2. **The web app reads `index.html` from disk on every request** and ships several
   un-batched `fetch()` calls on every paint.
3. **No compression**: SSE deltas and JSON payloads (memory view can be hundreds of KB)
   go over the wire raw.

## Findings (severity → fix)

### Blocker / high
- [ ] `health` crashes when the deep lane is absent (`mind.deep.worker_factory()` on `None`).
- [ ] No gzip compression → large JSON + SSE payloads uncompressed.
- [ ] No CORS → a separate companion-app frontend cannot call the API.
- [ ] `web_app` reads the HTML file synchronously on every request (blocks the loop).
- [ ] Recall loads **all** active layers every turn (unbounded) → O(history) per turn.
- [ ] Recall blocks turn start on a remote `embed_one(query)` with a 15s timeout ceiling.

### Medium
- [ ] `entities_in(pid, query)` is called 3× per recall (same result each time).
- [ ] SSE sends a `clock` frame every 1s doing a fresh DB read per connection.
- [ ] `thoughts` / `outbox` tables have no read endpoint (half-wired feature).
- [ ] Rate limiter is in-process only (fine for now, but undocumented).
- [ ] `SAWII_BACKGROUND` not documented in `.env.example`.
- [ ] No `railway.json` / start command / healthcheck config committed.
- [ ] SQLite has no WAL checkpoint policy and no busy_timeout → `SQLITE_BUSY` risk.
- [ ] No request-id / structured access logging for production debugging.

### Lower / polish
- [ ] Web app: no message-history bootstrap on load (only live turns).
- [ ] Web app: `refresh()` fires 4 sequential fetches; can be batched.
- [ ] No `/v1/persona/export` (backup) though "memory wipeable" exists.
- [ ] No graceful drain logging on shutdown.

## Fix plan

Worked top-down, committed in small steps. See commits on `cline/08b6qjcx`.
