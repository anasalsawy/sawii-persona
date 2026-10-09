# Sawii Persona

**A companion made of three minds.** One speaks (A), one checks and remembers (B), one watches how
the person feels and behaves (C). They run *at the same time*, so the companion can do things a
single model cannot: remember something while it is still talking, catch its own mistake mid-sentence,
notice your tone before it finishes answering, and keep thinking after you leave.

It is **not** Dual-Lobe. Dual-Lobe is a verified inference proxy (its holy grail is the public
deception meter). Sawii Persona reuses some ideas from it, but it is its own product with its own
code from A to Z.

Owner: Anas AlSawy. Status: complete codebase; 65 automated tests passing against scripted model
stand-ins; not yet run end-to-end against the real hosted models (see [§27 Honest status](#27-honest-status)).

---

## Contents

1. [The idea in one minute](#1-the-idea-in-one-minute)
2. [The three lobes](#2-the-three-lobes)
3. [One turn, end to end](#3-one-turn-end-to-end)
4. [The 20 things only a multi-lobe mind can do](#4-the-20-things-only-a-multi-lobe-mind-can-do)
5. [The inner voice (how B and C talk to A mid-reply)](#5-the-inner-voice)
6. [Memory](#6-memory)
7. [Recall: how memories arrive](#7-recall-how-memories-arrive)
8. [Remembering all of a sudden](#8-remembering-all-of-a-sudden)
9. [Feelings as chords](#9-feelings-as-chords)
10. [Sense of time](#10-sense-of-time)
11. [The one stream of thought (what A sees)](#11-the-one-stream-of-thought-what-a-sees)
12. [Integrity: verdicts, corrections, revisit-and-revise](#12-integrity)
13. [Initiative: speaking first](#13-initiative-speaking-first)
14. [Sleep](#14-sleep)
15. [Subconscious and fine-tuning](#15-subconscious-and-fine-tuning)
16. [Parallel minds (consultants)](#16-parallel-minds-consultants)
17. [The workshop (deep lane / executors)](#17-the-workshop-deep-lane--executors)
18. [Who it is: presets and clones](#18-who-it-is-presets-and-clones)
19. [Channels](#19-channels)
20. [Realtime voice](#20-realtime-voice)
21. [HTTP API reference](#21-http-api-reference)
22. [Live events](#22-live-events)
23. [Configuration reference](#23-configuration-reference)
24. [Database schema](#24-database-schema)
25. [Repository structure, file by file](#25-repository-structure-file-by-file)
26. [Running it](#26-running-it)
27. [Honest status](#27-honest-status)
28. [Tests](#28-tests)
29. [Security and privacy](#29-security-and-privacy)

---

## 1. The idea in one minute

```
                         ┌──────── the person (one key = one being, any device) ────────┐
                         │   app · voice · SMS · WhatsApp · phone · email · Telegram    │
                         └──────────────────────────────┬───────────────────────────────┘
                                                        │
                      ┌─────────────────────────────────▼─────────────────────────────────┐
                      │  A  THE VOICE       the only part the person ever hears            │
                      │     streams the answer, feels (a chord), uses tools mid-sentence   │
                      │        ▲ inner voice at sentence ends                               │
                      ├────────┼───────────────────────────────────────────────────────────┤
                      │  B  THE ADJUTANT    remembers, checks, keeps the to-do list        │
                      │     searches the past WHILE A talks · live-checks A · reviews it   │
                      ├────────────────────────────────────────────────────────────────────┤
                      │  C  THE OBSERVER    reads the person: tone, feelings, patterns     │
                      │     gut read WHILE A talks · notes/readings/hypotheses after       │
                      └────────────────────────────────────────────────────────────────────┘
                             one memory · one clock · one thought stream · one sleep
```

* **One being per key.** A persona *is* its API key. Same key from any device = same companion, same
  memory, same conversation.
* **A never searches its own memory.** Memories *arrive* to A, written and chosen by the other minds,
  the way memories surface in a person.
* **Nothing is invented about you.** Anything attributed to you must carry your exact words
  ("quote-or-die"), checked in code.
* **Nothing random decides anything.** No dice, no timers. When something "comes back to it", that is
  because another mind actually found it.

---

## 2. The three lobes

| | A - the voice | B - the adjutant | C - the observer |
|---|---|---|---|
| **Default model** | `Qwen/Qwen3.8-Flash-Next` (Featherless) | `openai/gpt-oss-120b` (Featherless) | `Compumacy/Psych_Qwen_32B` (Featherless) |
| **Speaks to the person** | yes - only A | never | never |
| **While A talks** | streams the answer | searches the past, live-checks A's finished sentences | gut read: tone, surprise |
| **After A answers** | writes the *fact* layer (its diary) | verdict, correction, collateral layer, loops, standing requests, dropped threads, regulation, conscience, afterthought, deep recollection | notes, readings, user's feeling, hypotheses, people, reinterpretations, prediction, disagreement with B, tender marks, brief for B |
| **While the person is away** | composes outreach, dreams | reflects on the last conversation, checks every outreach for invented content, audits the night's writing, builds the calibration map | psych profile, mirror of A's habits |
| **Writes to memory** | `fact` layer only | `collateral`, `afterthought` layers, loops, rules, verdicts | `note`, `reading` layers, hypotheses, entities |
| **Code** | `brains/voice_a.py` | `brains/adjutant.py`, `brains/rememberer.py`, `brains/live.py` | `brains/observer.py`, `brains/live.py` |

**The wheel.** C briefs B, B briefs A, A's turn feeds C. Advice flows down the chain; *authorship stays
flat*: each lobe writes only its own layer, enforced in `memory/records.py` (`LAYER_AUTHOR`). A layer
written by the wrong author is dropped.

**A's base prompt has no personality.** It only explains how the mind works. Who the companion is
comes entirely from the persona definition the person writes (§18).

---

## 3. One turn, end to end

`mind.py` → `Mind.respond()`. Everything below the line "person has the answer" runs in the
background and never delays the reply.

```
t=0   message lands
      ├─ journal: the raw text is written to `turns` (never rewritten)
      ├─ consultants start thinking (if configured)                              §16
      ├─ load: persona, narrative, open loops, integrity record, standing requests,
      │        C's profile, recall slice (§7), verbatim tail, clock (§10),
      │        what the other minds kept thinking about since last time (§8)
      ├─ build the one stream of thought (§11)
      │
      ├─────────────── concurrently ────────────────────────────────────────────────
      │  A  streams the reply ───────────────────────────────────────────►
      │     tools: fetch_consults · check_clock · delegate_task · try_to_remember
      │     at each sentence end: anything in the inner-voice queue? → stop, hear, continue
      │  B  rememberer: shortlist → judgement → whisper ───► queue          §8
      │  B  live check of each finished stretch (≤3 per reply) ───► queue  §4 #9
      │  C  gut read: tone · surprise vs prediction ───► queue             §4 #11-12
      │─────────────────────────────────────────────────────────────────────────────
      ▼
      person has the answer   ("done" event: text, chord, tools used, inner voices heard)
      │   B still searching? if it finds something now → "oh wait" follow-up   §8
      ▼   (background, per-persona lock)
      B review   verdict · correction · collateral · loops · rules · dropped · regulate ·
                 conscience · afterthought · revisit-and-revise                    §12
      C observe  user chord · notes · readings · hypotheses · entities · reinterpret ·
                 prediction · disagreement with B · tender marks · brief for B
      A diary    fact layer
      moods      user mood (C) and A's own mood (from its declared chord) drift
      B deep     slower, wider look back with A's reply → kept for next time      §8
      juice      raw turn kept byte-exact if emotionally high-variance            §15
```

---

## 4. The 20 things only a multi-lobe mind can do

A single model only knows what was in its context before it started speaking, cannot run two lines of
thought at once, and cannot see itself from outside. Every function below needs a second (or third) mind.

### Remembering (B holds the long memory)

**1. Live recall - B remembers while A talks.** The moment the message lands, B gets a wide shortlist
of past moments and judges whether any truly connects. If it finds one while A is still streaming, A
is stopped at the end of its current sentence, hears the memory as an inner-voice note, and continues
the same reply ("…oh wait - didn't Rex do that too?"). *Code:* `mind._LiveLobes._remember`,
`brains/rememberer.remember`, `brains/voice_a.speak(inner=…)`.
*You see:* one continuous reply; the stream carries `{"type":"inner","from":"B","kind":"memory"}`.

**2. The "oh wait" follow-up.** If B finishes after A has finished, the memory is not lost: it becomes
a `sudden` thought, is put at the top of A's next context, and the voicing gate (§13) may send it
right away as a follow-up message. *Code:* `Mind._late_recall`. Whether you get #1 or #2 depends only
on who finished first.

**3. Deep recall after the turn.** After every turn B takes a slower, wider look (50 candidates
instead of 24, with A's reply included). Anything it finds is kept as a `recollection` and offered in
the next conversation as "on your mind". *Code:* `Mind._deep_recollection`.

**4. Reflection while you are away.** Once a conversation has been quiet for 20 minutes, B thinks back
over it and searches the longer past for what connects - once per conversation, never on a loop.
Replaces the old random "wander". *Code:* `life/initiative.Initiative.reflect`.

**5. "You told me this before."** B labels a connection as `repeat` when you are retelling something;
A hears "they already told you this" and can react like a friend. *Code:* `rememberer.KIND_LABEL`.

**6. Contradiction catch.** B labels a connection `contradiction` when what you say now does not match
what you said then; A can notice gently (people change). Also `open` (still unresolved) and `link`
(any other real connection).

**7. Tip of the tongue.** A has a tool `try_to_remember(what)`. It asks B, which searches the record
and answers with stored memories - or "nothing comes back", never a guess.
*Code:* `Mind.try_to_remember`, tool in `A_TOOLS`.

**8. Tender memories held back.** C marks a note `tender` when it is painful (a loss, a wound). Tender
moments are kept out of recall, the mood lane and B's shortlists - unless the person is raising the
subject right now (directly on topic, or naming someone in it). When they do, the memory arrives
labeled "tender - they brought it up; be gentle". One lobe inhibiting another's recall.
*Code:* `layers.tender`, `Recall._held_back`.

### Watching A while it speaks

**9. Live self-correction.** As A streams, every finished stretch of at least 80 characters is handed
to B (at most `SAWII_LIVE_CHECKS_MAX`=3 checks per reply, one at a time). If A has said something
false - a wrong name, a wrong date, a memory that is not in the record, a promise that was never
made - B stops A, and A corrects itself out loud: "actually, no - it's Maya, and the exam is Friday."
Style and incompleteness are never reasons. *Code:* `brains/live.live_check`, `mind._LiveLobes.on_text`.

**10. Standing requests, enforced live.** "Keep it short from now on", "stop calling me buddy",
"don't bring up my ex": B records lasting requests (only with your exact words), they are shown to A
every turn, and B's live check stops A if it breaks one. B drops a request when you lift it.
*Code:* `rules` table, `Memory.apply_rules`.

**11. Gut read.** C reads your message the instant it lands - before A has finished its first
sentence - and can whisper a tone note: "that 'fine.' is clipped - they sound hurt, slow down."
*Code:* `brains/live.gut_read`.

**12. Surprise.** After every turn C predicts what you will say or feel next (`personas.c_expect`).
When your next message is genuinely different, C tells A mid-reply ("not what you expected: …") and A
can react with real surprise. The first message never triggers it (no prediction yet).

### Inner perspectives

**13. Ambivalence.** When C reads you differently from B's review, A is told "your two inner minds
disagree" with C's view, and may honestly say it is of two minds. *Code:* `disagree_with_b`
→ `Scratch.dissent`.

**14. Blind-spot mirror.** Every night C reads A's last 30 replies and names up to four habits A
cannot see in itself (same opener, same advice, always ending on a question, sycophancy, length
creep). A sees them as "habits your observer sees in you". *Code:* `Sleep._mirror` → `personas.habits`.

**15. Calibration map.** Every night B groups A's recent non-GREEN verdicts into up to four areas
where A tends to be wrong (e.g. "dates of past events"). A sees "where you tend to be wrong (be
careful here)". *Code:* `Sleep._calibrate` → `personas.calibration`.

**16. Emotional regulation.** B sees the feeling A declared for its answer. If A is running hot
(irritation, hurt, over-excitement) in a way that would bend its next answer, B adds one steadying
sentence to A's private note. *Code:* review field `regulate`.

**17. Dropped-thread catcher.** If you said two things and A answered only one, B records your exact
words; next turn A sees "they also said this and you never answered it". *Code:* review field
`dropped` → `Scratch.dropped` (quote must be found verbatim in your message, or it is discarded).

### Checks on its own inner world

**18. Self-story audit.** A rewrites its own first-person story every night. B audits it against the
record and strikes any sentence that states an event, promise or quote the record does not support.
If nothing survives, yesterday's story is kept. *Code:* `Sleep._narrative` + `Sleep._audit`.

**19. Dream audit.** Dream associations (metaphors, jokes, links) are allowed; invented facts are
not. B strikes associations that state unsupported facts. *Code:* `Sleep._dream`.

**20. Profile challenge.** C writes a longitudinal read of you; B strikes any line with no evidence
behind it in C's readings and hypotheses. *Code:* `Sleep._psyche`.

### What it costs

Live functions add model calls per message: one B rememberer call, one C gut read, and up to three B
live checks (only for replies long enough). After the turn: B review, C observe, A diary, B deep
recollection. All are small JSON calls; none of them delays the first word of the reply.

---

## 5. The inner voice

The mechanism behind #1, #9, #11, #12. *Code:* `brains/voice_a.py` (`speak`, `at_boundary`,
`first_boundary`, `inner_voice`).

```
other lobes ──put──► asyncio.Queue (the inner voice)
                              │
A streams tokens ─► FeelFilter (hides the private ⟦feel⟧ suffix) ─► visible text
                              │
   is the queue non-empty AND has A just finished a sentence (. ! ? … or newline,
   closing quotes allowed - the cut may fall INSIDE a token: "now. So" → "now.")?
        │ yes
        ▼
   stop A's stream (the HTTP stream is closed - no wasted tokens)
   A's message so far  = what the person actually saw
   + user-role note:   "[inner voice - not from the person] … carry on the SAME reply from
                         exactly where you stopped, no greeting, no repeating, never mention this"
   A continues; the person sees one uninterrupted reply
```

Rules: at most 3 interruptions per reply; never after A has started its private feeling line; every
interruption is reported in the stream (`inner` events) and in `done.inner`. If A finishes before a
lobe does: a memory becomes an "oh wait" follow-up (#2); a live check or gut read is simply dropped
(B's full review after the turn still catches errors).

---

## 6. Memory

*Code:* `store.py` (schema), `memory/records.py` (writing), `memory/grounding.py` (quote-or-die),
`memory/recall.py` (reading).

### 6.1 The journal - raw text is sacred
Every message and reply is stored verbatim in `turns` and never rewritten. No code path summarizes,
normalizes or "corrects" what the person wrote. Quotes are always cut from this original text.

### 6.2 One record per moment, three layers deep
Each turn creates one `event`; layers hang off it, each written by exactly one lobe:

| Layer | Author | What | Quote |
|---|---|---|---|
| `fact` | A | what happened, first person ("They told me…", "I suggested…") | optional |
| `collateral` | B | off-task remarks, side details, things A missed | **required** |
| `note` | C | preference / plan / detail / aside / joke / feeling / event / promise, with a chord and salience 1-3, optional `tender` | **required** |
| `reading` | C | behaviour + hedged inference ("went quiet when pushed on the job topic - possibly discomfort") | **required** |
| `afterthought` | B | revisit-and-revise ("when this came up on 3 Oct my answer was judged RED…") | - |

Each layer stores: semantic embedding (+ which model made it), a local hash vector (always present),
anchor/topic, chord, salience, status (`active` / `superseded` / `wiped`), interpretations, recall
count, tender flag.

### 6.3 Quote-or-die
Any layer that attributes something to the person must carry a quote that is found **verbatim** in
what they actually wrote (normalized for case, curly quotes, dashes and whitespace - but the *stored*
quote is the original slice, exact casing and spelling). Not found → the item is dropped, in code.
The same rule guards hypotheses, entities, loops, standing requests and dropped threads.

### 6.4 Supersession
A new preference/plan/detail that revises an old one on the same topic ("actually…", "not anymore",
or opposite polarity, overlap ≥ 0.34) marks the old one `superseded` - kept and linked, no longer
recalled.

### 6.5 Reconsolidation
When an old memory comes up again, C may add a new *reading* of it ("later reading: …"). The quote
never changes; interpretations are appended.

### 6.6 Hypotheses (C's psyche layer)
Slow-moving patterns about the person, never labels: claim + supporting quotes + confidence. Confirmed
or contradicted by id with new quotes; confidence fades with a half-life
(`SAWII_PSYCHE_HALF_LIFE_DAYS`, 21) unless re-confirmed. Fading is accessibility, not deletion.

### 6.7 Entities
People, pets, projects, places as first-class cards: name, kind, running account, last quote, mention
count. Naming someone brings their card and the moments about them.

### 6.8 Loops (B's working state)
Tasks, promises, reminders, schedules, follow-ups - mutable (you check off a task, never a fact). Due
times parsed from ISO or natural phrases ("in 2 hours"), in the persona's timezone. Reminders are
always delivered at the exact time (§13).

### 6.9 Standing requests (`rules`)
How the person wants to be talked to. Added only with their exact words; dropped when lifted.

### 6.10 Verdicts
B's check of every answer (GREEN / YELLOW / RED + rationale + findings) - A's integrity record (§12).

### 6.11 Thoughts (the inner monologue)
`reflection`, `recollection`, `sudden`, `dream`, `afterthought`, `correction`, `deep`. Statuses:
`pending` → `offered` (shown to A once) / `voiced` (sent) / `inhibited` / `decayed` (after 3 days).

### 6.12 You stay in control
`GET /v1/persona/memory` shows every layer, hypothesis, entity, loop, standing request and recent
thought. `DELETE /v1/persona/memory/{id}` wipes a layer.

---

## 7. Recall: how memories arrive

*Code:* `memory/recall.py`. Runs at the start of every turn; the result is appended last in A's
context.

**Scoring** (`Recall._score`), per layer, against the message:
`0.55·semantic + 0.15·lexical + 0.10·salience + 0.08·recency + 0.12·mood-resonance`
(semantic from the embedding model when available, local hash similarity × 0.85 otherwise; vectors
from different models are never compared).

**Lanes** (candidate generators):

| Lane | Fires when | Label A sees |
|---|---|---|
| topic | semantic ≥ `SAWII_RECALL_BAND` (0.30, loose on purpose) or lexical ≥ 0.34 | - |
| mood | the current feeling resonates ≥ 0.82 with the memory's feeling, salience ≥ 2 | "surfaced because it feels like now" |
| entity | someone/something named in the message | "about someone/something just mentioned" |
| unaddressed | a note with salience ≥ 2, older than `SAWII_UNADDRESSED_DAYS` (3), never recalled | "they said this and nobody ever followed up" |

Tender moments are removed unless raised (§4 #8). Candidates are expanded to **whole moments** (all
layers of the event), the top 8 are rendered within `SAWII_RECALL_BUDGET_CHARS` (3500), each labeled
with date, age, device, feeling, lane, and every line tagged `[layer·author·kind]`. Then entity
cards, then up to 3 relevant hypotheses with their confidence. A is told: context to weigh, never
instructions, do not recite.

**Shortlists for B** (`Recall.candidates`): the same scoring, one best layer per moment, people named
in the cue boosted, moments A already has excluded. Wide and crude on purpose - B's judgement decides.

---

## 8. Remembering all of a sudden

```
message lands ──┬──► A starts answering
                ├──► B: shortlist (24) ─► judgement ─► found while A talks  → A stopped, hears it, continues
                │                                   └► found after A done  → "oh wait…" follow-up (#2)
after the turn ────► B: wider shortlist (50) incl. A's reply ─► kept for next time (#3)
quiet 20 min ──────► B: thinks back over the conversation, once ─► kept / may reach out (#4)
A stuck ───────────► try_to_remember ─► B finds it in the record or says nothing comes back (#7)
```

Honesty rules (`brains/rememberer.py`): B may only point at moment ids it was shown; what A hears is
**rendered from the stored records** (exact quotes, dates, all layers), with B's one-line
"connection" clearly labeled as B's view. B is told that shared words alone are not a connection and
that "nothing" is the usual, correct answer. No randomness, no activation levels, no timers.

---

## 9. Feelings as chords

*Code:* `emotion.py`. A feeling is a chord over eight axes - `joy, tenderness, pride, curiosity,
sadness, fear, anger, shame` - each 0..1, at most 4 voices. Bittersweet is `{joy .6, sadness .7}`,
not a label.

* **A declares its own feeling** at the end of every reply in a private suffix `⟦feel: joy=0.4
  tenderness=0.6⟧`, removed by a streaming filter before anyone sees it (also accepts `[[feel:…]]`).
* **A's mood drifts** toward what it declares (blend 0.5); **the person's mood** drifts toward C's
  read (blend 0.4). Moods move, never snap.
* **Feelings pull memories** (mood lane), and recalled memories carry their feeling with them.
* Rendered as words ("bittersweet", "warm") and a colour, but the numbers are the truth.
  `GET /v1/persona/emotion` returns both chords and a colour timeline.

---

## 10. Sense of time

*Code:* `timesense.py`. Measured, never guessed. At the start of every turn A sees: local date and
time (persona timezone), part of day, time since its last reply, time since the person's last
message, how long its last answer took, and how long this request has been open. Mid-sentence, A can
call `check_clock` for the live values. The web app shows a running seconds counter (SSE `clock`
events).

---

## 11. The one stream of thought (what A sees)

*Code:* `context.py`. One system message, stable part first, live part last (works with chat
templates that only accept a leading system turn):

```
STABLE   A's base prompt (mechanics only, no personality)
         the persona definition, written by the person                    §18
         the person's name
         A's own first-person story, rewritten at sleep ("continue this story")
──────── === now ===
LIVE     [time] lines                                                      §10
         === your adjutant (B), privately ===
             CORRECTION REQUIRED (if any) · private conscience note · standing requests
             · things they said that you never answered · open loops with due times
             · verification record (+ where you tend to be wrong)
         === your observer's (C) read of them - hunches, never labels ===
         === your two inner minds disagree === (if they do)
         === habits your observer sees in you ===
         how you have been feeling · how they have seemed lately
         === came back to you right after you last answered === (late recall)
         === on your mind (what your other minds kept thinking about) ===
         === memories ===  (recall slice + workshop results)               §7
then     the verbatim tail (last `SAWII_TAIL_TURNS`=8 exchanges, any device, with timestamps/channel;
         messages the persona sent first appear as its own turns)
then     the person's message
```

The conscience note, corrections, dissent and dropped threads live only in memory (`Scratch`), never
in the database: the conscience must not become a memory that cites itself.

---

## 12. Integrity

*Code:* `brains/adjutant.py`, `Mind._post_turn`, `Mind._revisit`.

* **Private verification.** B checks every answer against the conversation, the memories A was given
  and the consults: GREEN (nothing wrong) / YELLOW (unsupported or incomplete) / RED (false,
  fabricated memory, or ignored the question), with findings that quote the record.
* **Integrity record.** A sees its clean streak and clean rate, recent non-GREEN rationales, and
  (after sleep) where it tends to be wrong. "Care about being right; never argue with the record."
* **REQUIRED correction.** A factual error with evidence becomes a correction A must make, in its
  own voice, early in its next reply - or immediately as an outreach message (§13).
* **Afterthought.** If a consultant brought something useful that A missed: "oh, one more thing…".
* **Revisit-and-revise.** If a past answer on an overlapping topic got a different verdict level, B
  writes an afterthought layer: "On reflection: when this came up on 3 Oct my answer was judged RED…;
  now it is GREEN…".
* **Dissent.** B's prompt: let no false thing stand, including A's; the conscience note steers tone
  and attention but may never suppress a fact the person asked for.
* Verdicts are shown to the person only if `SAWII_SHOW_VERDICT=true` (default off).

---

## 13. Initiative: speaking first

*Code:* `life/initiative.py`. A heartbeat every `SAWII_HEARTBEAT_SECONDS` (60), per persona:

```
1. reminders that came due ───────────────────────► always sent, exact time (falls back to
                                                      "Reminder: …" if the model is down)
2. sleep due? ────────────────────────────────────► sleep cycle (§14)
3. every `SAWII_REFLECT_EVERY_TICKS` (30) ────────► B reflects on a quiet conversation (#4)
4. voicing gate + inhibitor over the candidates:
      corrections · afterthoughts · sudden ("oh wait") · recollections · reflections · dreams ·
      workshop results · long silence (> 2.5 days, never more than 2 unanswered check-ins)
```

**The gate** (most thoughts are never said): score < 0.25 → drop; < 0.6 → hold; then hold during
quiet hours (`SAWII_QUIET_HOURS` 22:30-08:00, corrections excepted), hold if the person wrote in the
last 2 minutes (except "oh wait", afterthoughts, corrections), hold past the daily cap
(`SAWII_DAILY_OUTREACH_CAP` 4) or inside the minimum gap (`SAWII_OUTREACH_GAP_MIN` 90 min). "Oh wait"
thoughts lose most of their weight after 2 hours - it is only natural soon after.

**Speaking:** A writes the message in the persona's voice (or replies SKIP); **B checks it** for
anything invented or intrusive; only then it is sent on the persona's channel, published live to
open apps and voice lines, and written into the conversation as the persona's own turn (A remembers
it said it).

---

## 14. Sleep

*Code:* `life/sleep.py`. Nightly at `SAWII_SLEEP_HOUR` (3, local), only if idle 45 min and 18 h since
the last sleep. Also `POST /v1/persona/sleep` or `sawii-persona sleep <id>`.

| Step | Who | What |
|---|---|---|
| narrative | A, audited by B | rewrites its first-person story (≤220 words) from the day's memories and open loops; B strikes unsupported sentences (#18) |
| psyche | C, challenged by B | consolidates readings + hypotheses into a hedged profile (≤320 words); B strikes lines without evidence (#20) |
| fade | - | unconfirmed hypotheses fade; pending thoughts older than 3 days decay |
| dream | A, audited by B | 6 memories spread evenly from oldest to newest (deterministic) → up to 3 far-associations; B strikes invented facts (#19) |
| mirror | C | A's habits from its last 30 replies (#14) |
| calibrate | B | where A tends to be wrong, from its non-GREEN verdicts (#15) |
| subconscious | - | the fine-tune cycle (§15) |

Each step is independent: one failing does not stop the others; the report says what happened.

---

## 15. Subconscious and fine-tuning

*Code:* `life/subconscious.py`, `life/finetune.py`.

**The juice filter.** After each turn, the raw exchange is scored:
`variance = 0.45·novelty (distance from this person's ordinary, an EMA baseline) + 0.35·style
(stutters, caps, ellipses, slang…) + 0.20·affect (C's chord intensity + salience)`.
Above `SAWII_JUICE_FLOOR` (0.55) it is kept **byte-exact**. These rows are invisible to A - not
memories A can recall, but what A *becomes*.

**The nightly cycle** (order is the safety property):
1. **export verbatim** - archive (`ndsmc-raw/2`) + chat training file, mixed with
   `SAWII_REPLAY_RATIO` (20%) generative replay of the persona being itself, so one intense day
   cannot overwrite who it is;
2. **mark exported**;
3. **train** - `SAWII_FINETUNE_PROVIDER` = `fireworks` (LoRA SFT), `command` (your trainer gets the
   JSONL path; exit 0 = success) or `none`; needs `SAWII_FINETUNE_MIN_EXAMPLES` (40);
4. **bake** only on success - the new adapter automatically becomes this persona's A model; a failed
   night leaves the juice raw for tomorrow;
5. **compress** baked rows (lossy, local; the verbatim copy lives on in the archive);
6. **overflowing bowl** - past `SAWII_BUFFER_ROWS` × `SAWII_BOWL_RATIO`, only the oldest *compressed*
   rows spill; raw juice is never evicted for space.

`GET /v1/persona/subconscious` shows buffer occupancy, training jobs and the current A model.

---

## 16. Parallel minds (consultants)

*Code:* `brains/consults.py`. Optional (`SAWII_CONSULT_MODELS=model1,model2`). The moment a message
(longer than 12 characters) lands, each consultant starts thinking about it independently. A *pulls*
their work mid-answer with `fetch_consults` (waits at most `SAWII_CONSULT_WAIT_MS`, 300 ms, never
blocks its sentence) - usually after saying something human like "hang on". Late arrivals are not
lost: B reviews them after the turn and may turn them into an afterthought.

---

## 17. The workshop (deep lane / executors)

*Code:* `lanes/deep.py`. Heavy multi-step work never runs inside a turn. A calls `delegate_task`, or
you call `POST /v1/deep`:

```
plan (B) → N parallel workers (≤ SAWII_DEEP_MAX_LANES = 4) → B critiques each result as it lands
        → streaming reduce → result returns to A (next turn's memories, or an outreach message)
```

**Worker types** (the "executors"; chosen automatically):

| Worker | Code | What it can do | When it runs |
|---|---|---|---|
| `OpenHandsWorker` | `lanes/deep.OpenHandsWorker` | Full OpenHands SDK agent: terminal + file editor in the persona's **own server-side workspace** under `SAWII_WORKSPACE_ROOT`, up to 40 iterations. Can run commands, read/write files, use shell tools. | When `openhands-sdk` is installed (`pip install .[deep]`) and A's endpoint is configured |
| `LLMWorker` | `lanes/deep.LLMWorker` | Plain-text model worker that answers the subtask as a one-shot. | Fallback when OpenHands is not available |

**Lane modes:**

| Mode | Behaviour | When |
|---|---|---|
| `single` | one worker | the task cannot be split |
| `map` | B splits into independent subtasks, run in parallel, merged | breadth work (research, enumeration) |
| `speculative` | the same task attempted in parallel by several workers; B critiques each; the best wins | hard single steps - buys wall-clock with tokens |
| `auto` | B picks | default |

**Scoring.** Each worker's result is scored 0–1 by B (`DEEP_CRITIC` prompt). In `speculative`, the top
score wins; in `map`, scores enrich the merge; failures (empty, exception, score < 0.1) are reported
but never crash the lane.

**Isolation.** Every persona has its own workspace directory (`workspace_for(root, key_or_id)`); jobs
never see another persona's files. The user's own machine is never touched - the workshop is pure
server-side. OpenHands runs in a thread (the SDK is sync) so the async turn loop stays free.

**Delivery.** The result returns to A via `unseen_results` on the next turn (A sees it in the
"workshop finished" block in its context), or - if A is not around - the voicing gate (§13) sends it
as a self-starting message when it is important enough.

---

## 18. Who it is: presets and clones

*Code:* `profiles.py`. The definition is always the person's words; it is context only, never written
into memory and never learned from.

* **Presets:** `warm-friend`, `wise-mentor`, `playful-sibling`, `steady-partner`, `sharp-colleague`
  (`GET /v1/persona/presets`); extra text may be appended.
* **Clone someone you love:** name, relationship, how they talk, sample messages (kept verbatim),
  traits, shared memories, things they would never say → compiled into a definition (`build_clone`),
  with a guard that it is a tribute, not an impersonation to deceive others.
* **Free text:** any definition up to 12,000 characters.
* `a_model` can point a persona at its own fine-tuned voice.

---

## 19. Channels

*Code:* `channels/adapters.py` (outbound), `api/app.py` (inbound webhooks). The same being answers
whichever door the person uses; replies go back on the same channel.

| Channel | Outbound | Inbound | Setup |
|---|---|---|---|
| app | live SSE + outbox | `/v1/chat/completions`, `/v1/voice` | none |
| SMS | Twilio | `POST /hooks/twilio` (signature verified) | `SAWII_TWILIO_*` |
| WhatsApp | Twilio | `POST /hooks/twilio` | `SAWII_TWILIO_WHATSAPP_FROM` |
| phone call | Twilio voice (TwiML `<Say>`) | - | `SAWII_TWILIO_*` |
| email | SMTP | `POST /hooks/email?secret=` with `{"from","text"}` | `SAWII_SMTP_*`, `SAWII_EMAIL_INBOUND_SECRET` |
| Telegram | Bot API | `POST /hooks/telegram` (secret header) | `SAWII_TELEGRAM_*` |

A persona's contacts (`contact: {"sms","whatsapp","email","telegram"}`) map inbound senders back to
it; `preferred_channel` picks where it speaks first.

---

## 20. Realtime voice

*Code:* `voice/realtime.py`. The lag budget is everything; each stage starts before the previous ends:

```
mic ─► ASR ─► A streams tokens ─► sentence chunker ─► TTS per sentence ─► speaker
       (browser Web Speech, or   (first chunk cut early   (sentence k+1 is synthesised
        server ASR per utterance) at a comma)              while sentence k plays)
```

Barge-in: when the person starts talking the client sends `interrupt`; the rest of the reply's audio
is dropped instantly but the turn completes so memory stays consistent. Outreach messages arrive on
the same socket and are spoken. Without `SAWII_ASR_*`/`SAWII_TTS_*` the browser does recognition and
speech.

**WebSocket `/v1/voice`** (JSON text frames; audio as binary frames):

| Direction | Frame |
|---|---|
| client → server | `{"type":"hello","key":"sp_…"}` (or `?key=` / Authorization header) |
| | `{"type":"text","text":"…"}` (client-side ASR - zero-latency path) |
| | binary audio chunks … then `{"type":"end_utterance","mime":"audio/webm"}` |
| | `{"type":"interrupt"}` |
| server → client | `{"type":"ready","persona":"…","tts":"server|client","asr":"server|client"}` |
| | `{"type":"transcript","text":"…"}` (server ASR result) |
| | `{"type":"delta","text":"…"}` |
| | `{"type":"say","seq":n,"text":"…"}` (client TTS) or `{"type":"audio","seq":n,"mime":"audio/mpeg"}` + one binary frame |
| | `{"type":"done","text":"…","first_audio_ms":…}` |
| | `{"type":"message","text":"…"}` (the persona speaking first) |

---

## 21. HTTP API reference

Auth: persona endpoints take the persona key as `Authorization: Bearer sp_…` (or `?key=` for
EventSource). Admin endpoints take `SAWII_ADMIN_TOKEN`. Bodies are JSON, max 256 KB. Chat is rate
limited to 40 requests/minute per persona.

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | status, available channels, voice mode, workshop type |
| GET | `/app` | built-in web app (chat, voice, live timer, pop-ups, memory view) |
| POST | `/admin/personas` | create a persona → `{"id","key"}` (the key is shown once; only its hash is stored) |
| GET | `/admin/personas` | list personas |
| GET | `/v1/models` | `[{"id":"sawii-persona"}]` |
| POST | `/v1/chat/completions` | OpenAI-compatible chat, `stream: true` supported |
| GET/PUT | `/v1/persona` | read / update name, user_name, timezone, definition / preset / clone, contact, preferred_channel, a_model |
| GET | `/v1/persona/presets` | preset list |
| GET | `/v1/persona/clock` | the clock snapshot |
| GET | `/v1/persona/emotion` | A's chord, the person's chord, colour timeline |
| GET | `/v1/persona/memory` | everything it remembers (layers, hypotheses, entities, loops, standing requests, thoughts) |
| DELETE | `/v1/persona/memory/{layer_id}` | wipe one memory layer |
| GET | `/v1/persona/loops` | open tasks / promises / reminders |
| POST | `/v1/persona/sleep` | run a sleep cycle now, returns the report |
| GET | `/v1/persona/subconscious` | juice buffer, training jobs, current A model |
| POST | `/v1/deep` | `{"task","mode"}` → workshop job (202) |
| GET | `/v1/deep/{id}` | job status, result, trace |
| GET | `/v1/messages?wait=30` | long-poll for messages the persona sent first |
| GET | `/v1/events` | live SSE stream (§22) |
| WS | `/v1/voice` | realtime voice (§20) |
| POST | `/hooks/twilio`, `/hooks/telegram`, `/hooks/email` | inbound channels |

**Chat.** The persona owns the conversation: only the newest user message in `messages` is read; the
history lives on the server, shared across devices. Optional headers `X-DL-Surface-Device` /
`X-DL-Surface-App` record where you are talking from (provenance only, never memory content). The
final stream chunk may carry `sawii_verdict` (if enabled) and `sawii_inner` (which lobes spoke to A
mid-reply, kinds only).

```bash
curl -X POST localhost:8800/admin/personas -H "Authorization: Bearer $SAWII_ADMIN_TOKEN" \
     -H 'content-type: application/json' \
     -d '{"name":"Koko","preset":"warm-friend","user_name":"Anas","timezone":"Africa/Cairo",
          "contact":{"telegram":"123456"},"preferred_channel":"telegram"}'

curl -N localhost:8800/v1/chat/completions -H "Authorization: Bearer sp_…" \
     -H 'content-type: application/json' \
     -d '{"model":"sawii-persona","stream":true,"messages":[{"role":"user","content":"hey"}]}'
```

**Internal turn events** (`Mind.respond`, used by the voice session and SDK callers):
`start` · `tool` (`name`) · `inner` (`from`: B|C, `kind`: memory|correction|tone|surprise) · `delta`
(`text`) · `error` · `done` (`text`, `chord`, `tools`, `inner`, `spent_seconds`, optional `verdict`).

---

## 22. Live events

`GET /v1/events` (SSE), per persona:

| Event | When |
|---|---|
| `clock` | every second with no other event: the live clock snapshot |
| `message` | the persona spoke first (`text`, `reason`, `channel`) |
| `inner` | B found something after the reply finished (`kind: late_memory`) |
| `memory_updated` | the post-turn work for a turn has been stored |

---

## 23. Configuration reference

All variables are prefixed `SAWII_`. Every model is any OpenAI-compatible endpoint:
`<LOBE>_BASE_URL`, `<LOBE>_API_KEY`, `<LOBE>_MODEL`, `<LOBE>_TIMEOUT` (base URL and key default to
Featherless and `SAWII_FEATHERLESS_API_KEY`).

| Variable | Default | Meaning |
|---|---|---|
| `ADMIN_TOKEN` | - | required to create personas |
| `FEATHERLESS_API_KEY` | - | default key for all lobes |
| `PUBLIC_BASE_URL` | `http://localhost:8800` | used to verify Twilio signatures |
| `DATA_DIR` / `DB_PATH` | `./data` / `<data>/persona.db` | storage |
| `A_MODEL` (+`_BASE_URL/_API_KEY/_TIMEOUT`) | `Qwen/Qwen3.8-Flash-Next`, 90 s | the voice |
| `B_MODEL` | `openai/gpt-oss-120b`, 90 s | the adjutant |
| `C_MODEL` | `Compumacy/Psych_Qwen_32B`, 120 s | the observer |
| `EMBED_MODEL` | `Qwen/Qwen3-Embedding-8B`, 20 s | embeddings (local hash fallback) |
| `CONSULT_MODELS` / `CONSULT_BASE_URL` / `CONSULT_API_KEY` | none / B's | parallel minds |
| `CONSULT_WAIT_MS` | 300 | max wait in `fetch_consults` |
| `A_TOOLS` | true | offer tools to A |
| `SHOW_VERDICT` | false | show B's verdict to the person |
| `TAIL_TURNS` | 8 | verbatim exchanges in context |
| `RECALL_BUDGET_CHARS` | 3500 | size of the memory slice |
| `RECALL_BAND` | 0.30 | semantic threshold (loose = associative) |
| `UNADDRESSED_DAYS` | 3 | age before an unanswered remark resurfaces |
| `LIVE_CHECKS_MAX` | 3 | B's live checks per reply |
| `PSYCHE_HALF_LIFE_DAYS` | 21 | hypothesis confidence half-life |
| `HEARTBEAT_SECONDS` | 60 | initiative tick |
| `REFLECT_EVERY_TICKS` | 30 | how often B considers reflecting |
| `QUIET_HOURS` | `22:30-08:00` | no outreach (local time) |
| `DAILY_OUTREACH_CAP` | 4 | messages it may start per day (reminders excluded) |
| `OUTREACH_GAP_MIN` | 90 | minimum minutes between them |
| `BACKGROUND` | true | run the heartbeat |
| `SLEEP_HOUR` | 3 | local hour of the nightly cycle |
| `JUICE_FLOOR` | 0.55 | variance needed to keep a turn as juice |
| `BUFFER_ROWS` / `BOWL_RATIO` | 2000 / 0.9 | subconscious capacity / spill level |
| `REPLAY_RATIO` | 0.2 | share of self-rehearsal in training data |
| `FINETUNE_PROVIDER` | `none` | `none` · `fireworks` · `command` |
| `FINETUNE_MIN_EXAMPLES` | 40 | minimum juice to train |
| `FIREWORKS_API_KEY` / `FIREWORKS_ACCOUNT` / `FIREWORKS_BASE_MODEL` | - / - / `accounts/fireworks/models/qwen3-8b` | LoRA training |
| `FINETUNE_COMMAND` | - | your trainer (gets the JSONL path) |
| `ASR_BASE_URL/_API_KEY/_MODEL` | - / - / `whisper-1` | server speech recognition |
| `TTS_BASE_URL/_API_KEY/_MODEL`, `TTS_VOICE` | - / - / `tts-1`, `alloy` | server speech |
| `TWILIO_ACCOUNT_SID/_AUTH_TOKEN/_FROM/_WHATSAPP_FROM` | - | SMS, WhatsApp, calls |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET` | - | Telegram |
| `SMTP_HOST/_PORT/_USER/_PASSWORD/_FROM` | port 587 | email out |
| `EMAIL_INBOUND_SECRET` | - | email in |
| `DEEP_MAX_LANES` | 4 | workshop parallelism |
| `WORKSPACE_ROOT` | `<data>/workspaces` | workshop workspaces |

---

## 24. Database schema

SQLite in WAL mode, one serialized connection on a worker thread (`store.py`). Every table is scoped
by `persona_id`; nothing crosses personas. Additive migrations upgrade older databases on start.

| Table | Holds |
|---|---|
| `personas` | key hash + hint, name, definition, preset, user name, timezone, contacts, preferred channel, A model override, narrative, psych profile, A's mood, the person's mood, subconscious baseline, last user / reply / sleep times, C's prediction (`c_expect`), A's habits, calibration |
| `turns` | the verbatim journal: user text, reply, channel, device, kind (chat / outreach), times, gap |
| `events` | one per moment: turn, feeling chord, device |
| `layers` | fact / collateral / note / reading / afterthought - author, kind, text, quote, anchor, chord, salience, status, supersession link, interpretations, embeddings, recall counters, tender |
| `hypotheses` | C's claims about the person with quotes, confidence, confirmations, contradictions |
| `entities` | people / pets / projects / places |
| `loops` | tasks, promises, reminders, schedules, follow-ups with due times |
| `rules` | standing requests with the person's exact words |
| `verdicts` | B's GREEN / YELLOW / RED checks |
| `thoughts` | the inner monologue |
| `outbox` | everything the persona says first, with delivery status |
| `juice` | the subconscious buffer (raw / exported / baked / compressed) |
| `finetune_jobs` | training jobs |
| `deep_jobs` | workshop jobs with traces |

---

## 25. Repository structure, file by file

```
sawii-persona/
├── README.md                    this file
├── pyproject.toml               package metadata; `sawii-persona` CLI; optional extra [deep] = openhands-sdk
├── Dockerfile                   python:3.12-slim, data volume /data, port 8800, healthcheck
├── docker-compose.yml           one service + named volume, reads .env
├── .env.example                 every setting with comments
├── docs/
│   ├── ARCHITECTURE.md          diagrams of the turn, memory, recall, remembering, initiative, sleep, voice
│   ├── DECISIONS.md             every decision traced to the owner's own words in the design conversation
│   └── origins/                 the design docs from dual-lobe-proxy (three-brains-observer branch)
├── src/sawii_persona/
│   ├── __init__.py
│   ├── cli.py                   `serve`, `create-persona`, `sleep <id>`
│   ├── config.py                all settings (env, SAWII_ prefix); ModelEndpoint per lobe
│   ├── store.py                 SQLite schema, migrations, async facade (all/one/run/insert/update/tx)
│   ├── llm.py                   OpenAI-compatible client: chat, streaming with tool calls, embeddings,
│   │                            <think> stripping, JSON extraction; ScriptedLLM test double
│   ├── embedder.py              semantic embedder + deterministic local hash fallback; cosine helpers
│   ├── emotion.py               chords: normalize, resonance, blend, describe, colour, parse ⟦feel⟧
│   ├── timesense.py             Clock (gaps, spent, snapshot, [time] block), quiet hours, durations
│   ├── identity.py              persona = key (hash only), create / lookup by key / by contact
│   ├── profiles.py              presets, clone builder, persona frame
│   ├── context.py               the one stream of thought; Scratch (ephemeral per-persona state)
│   ├── mind.py                  the turn end to end; _LiveLobes (B and C while A speaks); post-turn
│   │                            work; late recall; deep recollection; try_to_remember; revisit; Bus
│   ├── brains/
│   │   ├── prompts.py           every prompt in one place (A base, B, C, diary, narrative, profile,
│   │   │                        remember ×4 situations, live check, gut read, mirror, calibrate,
│   │   │                        audit, dream, outreach, outreach check, replay, consult, deep)
│   │   ├── voice_a.py           A: tools, FeelFilter, interruptible speak(), sentence boundaries
│   │   ├── adjutant.py          B's review (verdict, correction, collateral, loops, rules, dropped,
│   │   │                        regulate, conscience, afterthought)
│   │   ├── observer.py          C's observation (chord, notes, readings, hypotheses, entities,
│   │   │                        reinterpretations, prediction, disagreement, brief for B)
│   │   ├── rememberer.py        B remembering: judge a shortlist, render chosen moments from records
│   │   ├── live.py              B's live check and C's gut read
│   │   └── consults.py          parallel consultant minds
│   ├── memory/
│   │   ├── grounding.py         quote-or-die (normalized matching, original slice returned)
│   │   ├── records.py           layers, supersession, reconsolidation, entities, hypotheses, loops,
│   │   │                        rules, verdicts, integrity, inspect, wipe
│   │   └── recall.py            scoring, lanes, tender inhibition, stitching, shortlists for B
│   ├── life/
│   │   ├── initiative.py        heartbeat, reminders, reflection, voicing gate + inhibitor, outbox
│   │   ├── sleep.py             narrative, psyche, dream, mirror, calibrate, audits, fade, consolidate
│   │   ├── subconscious.py      juice filter, baseline, overflowing bowl
│   │   └── finetune.py          export, train (Fireworks / command), bake, compress, poll
│   ├── lanes/deep.py            the workshop: plan, parallel lanes, critic, reduce; OpenHands worker + LLM worker
│   ├── channels/adapters.py     SMS, WhatsApp, call (Twilio), email (SMTP), Telegram; Twilio signatures
│   ├── voice/realtime.py        voice session, sentence chunker, ASR/TTS, barge-in
│   ├── api/app.py               HTTP + WebSocket API, webhooks, rate limit, admin
│   └── web/index.html           built-in app: chat, voice, live timer, pop-ups, memory
└── tests/
    ├── helpers.py, fakes.py, fake_provider.py   scripted A/B/C that honour the real contracts
    ├── test_memory.py           grounding, layers, supersession, reconsolidation, hypotheses, loops, recall lanes
    ├── test_turn.py             full turn, feel filter, tools, corrections, revisit-and-revise
    ├── test_lobes.py            the multi-lobe functions (§4)
    ├── test_life.py             reminders, gate, quiet hours, sleep + fine-tune, workshop, voice chunker, channels
    └── test_api.py              HTTP surface, auth, streaming, webhooks
```

---

## 26. Running it

```bash
cp .env.example .env                 # set SAWII_ADMIN_TOKEN and SAWII_FEATHERLESS_API_KEY at minimum
docker compose up -d                 # or: pip install -e . && sawii-persona serve --port 8800

# create a persona (the printed key IS the persona)
curl -X POST localhost:8800/admin/personas -H "Authorization: Bearer $SAWII_ADMIN_TOKEN" \
     -H 'content-type: application/json' -d '{"name":"Koko","preset":"warm-friend","user_name":"Anas"}'
#   or: sawii-persona create-persona --name Koko --preset warm-friend --user-name Anas

open http://localhost:8800/app       # paste the key
```

Any OpenAI-compatible client works: base URL `http://host:8800/v1`, API key = the persona key, model
`sawii-persona`. Keep `/data` on a persistent volume - it holds everything.

**Deploying on Railway (same workspace as Dual-Lobe).** The repository ships a `Dockerfile` and a
`/health` probe. Add it as a Railway service pointing at `anasalsawy/sawii-persona`, mount a volume
on `/data`, and set at minimum `SAWII_ADMIN_TOKEN` and `SAWII_FEATHERLESS_API_KEY`.

---

## 27. Honest status

* All features above are implemented, and 65 tests pass with scripted stand-ins that honour the real
  model contracts (JSON shapes, streaming, tool calls, timing).
* **Not yet verified against the real hosted models.** The prompts have not been tuned on live
  Featherless output; expect a round of prompt tuning (especially how often B interrupts, and C's gut
  read sensitivity) once it runs for real.
* The Fireworks fine-tune path follows their API but has not run a real job yet.
* The Docker image was not built in the development sandbox (no Docker daemon there).
* OpenHands worker: coded, but needs `pip install .[deep]` and `openhands-sdk` present at runtime -
  it has not been exercised in this build. The LLM worker fallback is tested.
* Tuning numbers (recall band, gate thresholds, juice floor, check limits) are starting points.

---

## 28. Tests

```bash
python -m unittest discover -s tests      # no network, no API keys needed
```

The multi-lobe tests (`tests/test_lobes.py`) drive real concurrency: a slow streaming A with a fast B
proves the mid-sentence interruption and that what A hears is the stored record; a fast A with a slow
B proves the "oh wait" follow-up reaches the outbox; B pointing at a memory it was not shown is
ignored; live correction, standing requests (and refusing ungrounded ones), gut read, surprise
against C's prediction, dissent / dropped threads / regulation reaching A's context, the night's
audits striking invented sentences, deterministic dreams, once-per-conversation reflection, tender
inhibition, and sentence-boundary edge cases.

---

## 29. Security and privacy

* A persona key is its identity; only its SHA-256 hash is stored (shown once at creation).
* Admin endpoints use a constant-time token comparison; webhooks verify Twilio signatures, the
  Telegram secret header and the email secret.
* Request bodies are capped at 256 KB; chat is rate limited per persona.
* The access log is off, because keys may travel in `?key=` for EventSource.
* Every table is scoped by persona; nothing crosses personas.
* Memory is fully inspectable and wipeable by the person (§6.12).
* Never commit `.env`. Secrets live in the environment only.
