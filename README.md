# Sawii Persona

A companion that feels like a friend: it remembers you the way a person does,
has a sense of time, thinks while you are away, reaches out on its own, talks
in real time, and grows into itself every night while it sleeps.

It is **not** Dual-Lobe. Dual-Lobe is a verified inference proxy (its holy grail
is the public deception meter). Sawii Persona reuses some ideas from it, but it
is its own product with its own code from A to Z.

```
                         ┌──────────── one persona = one key = one being ────────────┐
   phone / PC / app ───► │                                                           │
   voice (realtime)  ──► │   A  the voice        the only one you ever hear          │
   SMS / WhatsApp    ──► │   B  the adjutant     checks A privately, keeps the to-do │
   email / Telegram  ──► │                       list, conscience, "what A missed"   │
                         │   C  the observer     psych eye: readings, feelings,      │
                         │                       hypotheses about you, briefs B      │
                         │                                                           │
                         │   memory · time · thoughts · heartbeat · sleep · workshop │
                         └───────────────────────────────────────────────────────────┘
```

## What makes it feel human

| Trait | How it is built |
|---|---|
| **Remembers you** | One memory store, one record per moment, written by three minds: **fact** (A), **collateral** - what A missed (B), **notes & readings** about you (C). Recall brings back the whole moment, three layers deep, labeled and dated. |
| **Never invents a memory** | *Quote-or-die*: anything attributed to you must carry your exact words, checked in code. Raw text is never rewritten. |
| **Remembers things all of a sudden** | **Priming**: what you say half-touches old memories; they warm up, fade slowly, and when one crosses the line - two turns later or an hour later - it **pops**: *"oh wait, I just remembered…"*. **Chains**: a memory warms its neighbours (same moment, same person, same feeling). **Late recall**: after it answers, a slower search keeps going and comes back seconds later. **Intrusions**: rarely, something salient comes to mind off-topic. **Unaddressed queue**: something you said that nobody followed up on. |
| **Feelings as chords** | Emotions are concurrent (bittersweet = joy .6 + sadness .7), never one label. Similar feelings bring back related memories; recalled memories bring their feeling with them. A declares its own feeling each turn; its mood drifts. |
| **Knows you over time** | C keeps hypotheses about you (never labels): each backed by your quotes, confirmed or contradicted, fading if nothing re-confirms them, rewritten into one hedged profile at night. |
| **Sense of time** | Measured, never guessed: time since its last reply, since your last message, how long the last answer took, and a running clock it can check mid-sentence. |
| **One stream of thought** | Identity → its own first-person story (rewritten while it sleeps) → open to-dos → recent conversation from any device → memories. It continues its own handwriting. |
| **Thinks while you're away** | Wander pass: memories drift up by mood; a thought forms; most are never said. Dream pass at night: far associations (where wit comes from). |
| **Speaks first** | Heartbeat: reminders you asked for (exact time), corrections, "oh wait" afterthoughts, sudden memories, workshop results, long silences. An inhibitor keeps it polite: quiet hours, daily cap, minimum gap, never talks over you. B checks every unprompted message for invented content. |
| **Every channel** | App (live pop-ups), SMS, WhatsApp, **phone calls**, email, Telegram - inbound and outbound. |
| **Realtime voice** | WebSocket; speech starts on the first sentence (first chunk cut early at a comma); next sentence is synthesised while the current one plays; barge-in stops it the moment you speak. |
| **Parallel minds** | When your message lands, consultant models start thinking at once; A pulls their work mid-answer with `fetch_consults` ("hang on…") without ever blocking. |
| **Changes its mind** | B checks every answer after the fact. Its verdicts become A's integrity record (a clean streak it cares about). A factual error with evidence becomes a REQUIRED correction. Revisit-and-revise: "last time this came up I said X; now I see Y". |
| **Has hands** | The workshop: heavy jobs run server-side (OpenHands agent in the persona's own workspace if installed, else model workers), in parallel lanes - map / speculative - each result checked by B as it lands. |
| **Sleeps and grows** | Nightly: narrative + psych profile rewritten, hypotheses fade, dreams, and the day's emotional "juice" (raw, byte-exact) is fine-tuned into A's weights (LoRA on Fireworks, 80% juice + 20% replay of itself). The new adapter becomes its voice automatically. Baked juice is compressed; the overflowing bowl only spills the oldest compressed rows at the rim. |
| **Who it is** | A's base prompt is empty of personality. You pick a preset or **clone someone you love** (their words kept verbatim). |
| **You stay in control** | See everything it remembers, wipe any line. Persona key = identity; only its hash is stored. |

## Quick start

```bash
cp .env.example .env            # put your Featherless key (and optional others) in it
docker compose up -d             # or: pip install -e . && sawii-persona serve
curl -X POST localhost:8800/admin/personas -H "Authorization: Bearer $SAWII_ADMIN_TOKEN" \
     -H 'content-type: application/json' \
     -d '{"name":"Koko","preset":"warm-friend","user_name":"Anas","timezone":"Africa/Cairo"}'
# -> {"id":1,"key":"sp_..."}   that key IS the persona
open http://localhost:8800/app   # paste the key: chat, voice, live timer, pop-ups, memory
```

Any OpenAI-compatible client also works: base URL `http://host:8800/v1`, API key = persona key,
model `sawii-persona`. The persona owns the conversation (only your newest message is read; the
history lives on the server, shared across all your devices).

## Models (all OpenAI-compatible, swap by env)

| Lobe | Default | Env |
|---|---|---|
| A - voice | `Qwen/Qwen3.8-Flash-Next` (Featherless) | `SAWII_A_*` - point at Fireworks/Together to use your fine-tuned model |
| B - adjutant | `openai/gpt-oss-120b` (Featherless) | `SAWII_B_*` |
| C - observer | `Compumacy/Psych_Qwen_32B` (Featherless) | `SAWII_C_*` |
| Embedder | `Qwen/Qwen3-Embedding-8B` (Featherless) | `SAWII_EMBED_*` (falls back to a local hash embedder if down) |
| Consultants | none | `SAWII_CONSULT_MODELS=a,b` |
| Voice | browser speech (zero config) | `SAWII_ASR_*`, `SAWII_TTS_*` for server ASR/TTS |
| Fine-tune | off | `SAWII_FINETUNE_PROVIDER=fireworks` + `SAWII_FIREWORKS_*`, or `command` |

## Repository map

```
src/sawii_persona/
  mind.py              one turn of the wheel, end to end
  context.py           the one stream of thought (what A sees)
  brains/              prompts, A (voice + tools + feeling), B (adjutant), C (observer), consults
  memory/              records (3 layers, quote-or-die, supersession, hypotheses, loops, verdicts),
                       recall (lanes, stitching), spontaneous (priming, chains, pops, late recall), grounding
  life/                initiative (heartbeat, outbox, wander), sleep, subconscious (juice), finetune
  channels/            SMS, WhatsApp, calls (Twilio), email (SMTP), Telegram
  voice/               realtime voice session + sentence chunker + ASR/TTS
  lanes/deep.py        the workshop (OpenHands / parallel lanes)
  api/app.py           HTTP + WebSocket API     web/index.html  the built-in app
docs/ARCHITECTURE.md   how it works, with diagrams
docs/DECISIONS.md      every decision, traced to the conversation where it was made
docs/origins/          the design docs from dual-lobe-proxy (three-brains-observer branch)
tests/                 54 tests (unittest), incl. a fake OpenAI provider for live smoke runs
```

## Tests

```bash
cd tests && python -m unittest discover -s . -p "test_*.py"
```

## Status - honest

Built and tested here: everything above, end to end, against scripted models and against a fake
OpenAI-compatible server over real HTTP (streaming, tool calls mid-sentence, embeddings), plus the
web app in a real browser.
Not exercised in this build environment (no network to these providers): real Featherless/Fireworks
calls, Twilio/SMTP/Telegram delivery, server-side ASR/TTS, the OpenHands SDK worker. Their adapters are
written against the providers' documented APIs and are isolated so a failure never breaks a turn.
Storage is SQLite (WAL) - right for one server; move to Postgres when you outgrow it.
