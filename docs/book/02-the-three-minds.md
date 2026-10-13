# 02 · The three minds

A companion made of three minds. One **speaks**. One **feels and does**. One **verifies**. They run
at the same time, so the companion can do what a single model cannot: remember something while it is
still talking, catch its own mistake mid-sentence, notice your tone before it finishes answering, and
keep thinking after you leave.

```
                     ┌──────────── the person (one key = one being, any device) ────────────┐
                     │        app · voice · SMS · WhatsApp · phone · email · Telegram        │
                     └──────────────────────────────┬───────────────────────────────────────┘
                                                    │
                  ┌─────────────────────────────────▼─────────────────────────────────┐
                  │  A  THE VOICE       the only part the person ever hears            │
                  │     streams the answer, feels (a chord), uses tools mid-sentence   │
                  │        ▲ inner voice at sentence ends                               │
                  ├────────┼───────────────────────────────────────────────────────────┤
                  │  B  THE PSYCHE + HANDS   feels the exchange, keeps the to-do list   │
                  │     gut read WHILE A talks · searches the past · runs the tools     │
                  ├────────────────────────────────────────────────────────────────────┤
                  │  C  THE VERIFIER    judges every answer, and keeps the record        │
                  │     the meter · the wrongness dataset · the anchor · the update      │
                  └────────────────────────────────────────────────────────────────────┘
                         one memory · one clock · one thought stream · one sleep
```

## The roles, as built

| | **A — the voice** | **B — the psyche + hands** | **C — the verifier** |
|---|---|---|---|
| **Speaks to the person** | yes — only A | never | never |
| **While A talks** | streams the answer | gut read: tone, surprise · searches the past · live-checks finished sentences | (waits — verification is after the turn) |
| **After A answers** | writes the *fact* layer (its diary) | notes, readings, your feeling, hypotheses, entities, reinterpretations · collateral, loops, rules, conscience, afterthought | **the verdict** — level, findings, correction evidence · records the case when A was wrong |
| **While you are away** | composes outreach, dreams | psych profile, mirror of A's habits, calibration of A's weak spots | **replays the wrongness dataset — the anchor** |
| **Writes to memory** | `fact` layer only | `collateral`, `note`, `reading`, `afterthought` layers, hypotheses, entities, loops, rules | `verdicts`, `anchor_runs` — **no memory layers** |

**Advice flows down the chain** (C → B → A). **Authorship stays flat**: each mind writes only its own
layer; nobody edits upstream. B never rewrites C's judgement; A never edits B's loops.

**Dissent is the job.** C lets nothing false stand, including A's. B tells C what it *needs* to hear,
not what it *wants* to hear. A junior mind overrules a senior one only with your own recorded words.

## The swap (decided and built)

This chapter used to describe a re-architecture that was *intended*. It is built. The owner's decision:

> *"Moving emotions, all the emotional psyche layer, from C and giving it to B, so now B runs the
> emotion and the psyche instead of C — and C runs the verification instead of B."*

| Function | Before | **Now** |
|---|---|---|
| Verification — verdict, findings, correction evidence, the meter | B | **C** |
| Psych read — chord, hypotheses, notes, readings, entities | C | **B** |
| The gut read while A talks | C | **B** |
| Open loops, reminders, collateral, conscience | B | **B** (unchanged) |
| Execution — tools, workshop, background work | workshop | **B** (the hands) |

Two consequences worth naming, because they are the point:

- **C no longer writes memory layers.** It writes verdicts. The one-writer-per-layer rule follows the
  lobe: `note` and `reading` now belong to B, `fact` to A.
- **C is now the teacher.** Verification is exactly the loss signal the aging design needed — so the
  mind that judges is the mind that will feed the update.

The principle behind it is not taste: **the mind that does the work must not grade its own work.**
An executor that verifies itself is a closed loop.

## What C does with the record

Because C judges every turn, every judgement is also data:

- **Persistent verification** — every answer is judged, and the verdict is the record of truth.
- **The wrongness dataset** — every case where C judged A wrong is kept, joined to what was actually
  said (`life/anchor.py`).
- **The rehearsal** — during sleep those cases are replayed: A is asked the same things again, C
  verifies the new answers, and the pass rate becomes the **anchor score** (`anchor_runs`).
- **The anchor** — that score is the outside the mind cannot forge, and the seed of the gate that will
  refuse a night's learning when the mind has drifted.

See **06 · The aging** for where this is going.

→ next: **03 · One turn.**
