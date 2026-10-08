# Architecture

## 1. The three lobes and the wheel

```
                    C  the observer (psych model)
                   ╱  ▲
      brief for B ╱    ╲  reads the exchange + B's review
                 ▼      ╲
     B  the adjutant ────────►  A  the voice  ────►  the person
        (private verifier,  brief:      ▲
         to-do keeper,      conscience, │ memories arrive (A never searches)
         conscience)        loops,      │
                            integrity   │
                                        └── written by A (fact), B (collateral), C (notes/readings)
```

* **Advice flows down the chain** (C → B → A). **Authorship stays flat**: each lobe writes only its own
  layer, nobody edits upstream. B never rewrites C's reading of you; A never edits B's loops.
* **Dissent is the job.** B lets nothing false stand, including A's. C reports what B needs to hear, not
  what B wants to hear. A subordinate overrules its superior only with the user's own recorded words.
* **Lag is fine.** B's brief uses C's read as of last turn; psyche moves slowly, and the turn stays one
  hop.

## 2. One turn

```
message ─┬─► consultants start thinking (parallel, never awaited)
         │
         ├─► load:  persona · narrative · open loops · integrity record · C's profile
         │          recall slice (5 lanes, stitched) · popped memories · thoughts on its mind
         │          verbatim tail (server-side, any device) · clock
         ▼
     A streams ──(tool)── fetch_consults: bounded wait, returns what's ready + "still working"
         │      ──(tool)── check_clock: the running seconds
         │      ──(tool)── delegate_task: the workshop
         │      private suffix ⟦feel: …⟧ removed before the person sees it → A's chord
         ▼
   the person has the answer ───────────── nothing below blocks them ──────────────
         ▼
   B review  → verdict (integrity record) · REQUIRED correction · collateral · loops
               · conscience note (ephemeral, never stored) · afterthought from late consults
               · revisit-and-revise ("I said X then, now Y")
   C observe → user's chord · notes · readings · hypotheses (new/confirm/contradict)
               · entities · reinterpretations · brief for B
   A diary   → fact layer
   B rules   → standing requests ("keep it short") · dropped threads · regulation of A's feeling
   C extras  → prediction of their next message · disagreement with B (ambivalence) · tender marks
   B deep    → slower, wider look back now that A has answered → kept for next time
   juice     → raw turn kept byte-exact if emotionally high-variance
```

## 3. Memory

```
events (one per moment)
  └─ layers
       fact        A   what happened                       quote optional
       collateral  B   off-task, what A missed             quote REQUIRED
       note        C   preference/plan/aside/joke/feeling  quote REQUIRED, chord, salience
       reading     C   behaviour + hedged inference        quote REQUIRED ("went quiet when…")
       afterthought B  revisit-and-revise
  each layer: embedding (semantic) + local hash vector, status (active/superseded/wiped),
              interpretations[] (reconsolidation: the quote never changes), tender (C: held back)

hypotheses   C's psyche layer: claim + quotes + confidence that fades (half-life 21 d)
entities     people / projects as first-class things
loops        B's working state: tasks, promises, reminders - mutable (you check off a task,
             never a fact)
verdicts     B's integrity record for A
rules        standing requests the person made about how to be talked to (B keeps them)
thoughts     the inner monologue: reflection, recollection, sudden, dream, afterthought, correction, deep
juice        the subconscious buffer (never visible to A)
```

**Recall lanes**: topic (semantic + lexical) · mood (chord resonance, emotion → text) · entity ·
unaddressed (said, never followed up). Nothing random. Tender moments (C marked them painful) are held
back unless the person is raising the subject right now.
Candidates expand to the **whole moment** (all layers), are budgeted, and every line is labeled with
layer, author, date, surface and feeling.

**Supersession**: a new preference/plan/detail that revises an old one on the same topic ("actually…",
"not anymore", or opposite polarity) marks the old one superseded - kept, linked, no longer recalled.

## 4. Remembering all of a sudden - two minds, not a trick

```
message lands ──┬──► A starts answering (streams, sentence by sentence)
                │         ▲            ▲
                │         │ inner voice│ at the next sentence end
                ├──► B: shortlist (crude, wide) ─► B judges: does anything REALLY connect?
                │        found while A talks  → A is stopped, hears it, continues the SAME reply
                │        found after A is done → "oh wait, I just remembered…" message
                ├──► B: live check of A's finished sentences (false? broke a standing request?)
                │        → A corrects itself out loud: "actually, no - …"
                └──► C: gut read of the message (tone; surprise vs C's prediction)
after the turn ────► B: slower, wider look (A's reply included) → offered next conversation
while away ────────► B: once per conversation, thinks back and searches the longer past
A stuck ───────────► tool try_to_remember → B finds it in the record (or says nothing comes back)
```

Rules that keep it honest: the search only gathers; **B's judgement decides**. B may only point at
records it was shown, and what A hears is **rendered from the stored record** (exact quotes, dated),
with B's one-line "connection" labeled as B's view. Whether A is interrupted or follows up later
depends only on whether B finished thinking before A finished talking - no dice, no timers.

## 5. Initiative

```
heartbeat (every 60 s, per persona)
  reminders due ─────────────────────────────────► always sent (you asked)
  sleep due? ────────────────────────────────────► sleep cycle
  every 30 ticks: B reflects (once per quiet conversation) ► thought stream
  candidates: corrections · afterthoughts · sudden · recollections · reflections · dream · deep · silence
       │
   voicing gate + INHIBITOR:  score ≥ 0.6 · not quiet hours · not mid-conversation
       │                      (except "oh wait"/corrections) · daily cap · min gap
       ▼
   A composes in persona voice ─► B checks: nothing invented ─► outbox ─► channel
                                                                  └► live pop-up (SSE / voice)
   the message becomes part of the one stream (A remembers it said it)
```

## 6. Sleep (nightly at SAWII_SLEEP_HOUR, local, when idle)

```
narrative      A rewrites its own first-person story from the day's memory; B audits it, strikes the unsupported
psych profile  C consolidates readings + hypotheses (hedged, ≤320 words); B strikes lines without evidence
mirror         C reads A's recent replies → habits A cannot see in itself
calibration    B groups A's recent mistakes → "where you tend to be wrong"
fade           unconfirmed hypotheses fade (accessibility, not deletion); stale thoughts decay
dream          far associations between memories far apart in time (deterministic picks) → B audit → thoughts
juice          export verbatim (archive ndsmc-raw/2 + chat training file, 80% juice / 20% replay)
               → mark exported → train (Fireworks LoRA | your command | none)
               → bake on success (A's model switches to the new adapter) / stay raw on failure
               → compress baked rows (lossy, local) → overflowing bowl: evict oldest compressed
                 rows only past capacity; raw juice is never evicted for space
```

## 7. Realtime voice

```
mic ─► browser speech recognition (instant) or server ASR on utterance end
    ─► A streams ─► sentence chunker (first chunk cut at a comma) ─► TTS per sentence (pipelined)
    ─► speaker            barge-in: you speak → {"type":"interrupt"} → audio stops at once
pop-ups arrive on the same socket and are spoken
```

## 8. Where things run

Everything is server-side. Your devices only send text/audio and receive text/audio. The workshop
(OpenHands) runs in the persona's own server workspace (`data/workspaces/<hash>`), never on your machine.
