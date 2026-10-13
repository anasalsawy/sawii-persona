# 04 · Memory

Memory is the core of the companion. Not the model, not the personality — **memory.** The user must
feel it is a friend, and the friendship must develop naturally over time. Everything else serves
that.

## One memory, three authors

There is **one memory**, and every mind writes to it — but only its own layer. Nobody edits upstream.

```
events  (one per moment)
  └─ layers
       fact         A   what happened                          quote optional
       collateral   B   off-task; what A missed                quote REQUIRED
       note         C   preference / plan / aside / joke      quote REQUIRED · chord · salience
       reading      C   behaviour + hedged inference          quote REQUIRED ("went quiet when…")
       afterthought B   revisit-and-revise
```

Every layer carries: a semantic **embedding** and a local **hash vector**; a **status**
(active / superseded / wiped); **interpretations[]** (reconsolidation — the quote itself never
changes); and a **tender** mark (C: painful; held back until you raise it).

Alongside the layers:

- **hypotheses** — C's psyche layer: a claim + the quotes behind it + a confidence that fades
  (half-life 21 days). A hypothesis, never a fact.
- **entities** — people and projects as first-class things, with their own account and last quote.
- **loops** — B's working state: tasks, promises, reminders. *Mutable* — you check off a task, never
  a fact.
- **verdicts** — the integrity record A reads to know how it has been doing.
- **rules** — standing requests you made about how to be talked to ("keep it short").

## Quote-or-die

The single hardest rule in the system, checked in code:

> **Nothing is attributed to you without your exact words.**

Any claim about the person must carry the person's verbatim quote. The quote is carved in stone: a
later reading may be *appended* to it, but the quote itself never changes. This is what keeps the
companion from inventing you.

## Stubborn memory

The owner's instruction, kept verbatim:

> *"I want persistent, non-relenting, stubborn, fixed, absolute long memory."*

So **nothing is silently dropped.** There is no decay, no fade, no eviction for space. Unconfirmed
hypotheses are not deleted — they simply become less accessible. Stale thoughts do not expire. The
subconscious bowl does not overflow. The full active history is considered on every turn.

> The honest cost: at large scale this needs a vector index (an ANN shortlist) so a long history
> does not slow every reply. The *policy* — nothing is forgotten — does not change. Only the
> machinery that finds it does.

## Memory is inspectable and wipeable

Every layer is quotable and auditable. The person can **see exactly what the companion knows** and
**wipe anything**, at any time, per persona. A companion that remembers everything must let you see
and remove everything, or it becomes uncanny.

## The overflowing cup - what may wear away

Memory here is stubborn by design: nothing live is faded on time, no layer is evicted for space, no
hypothesis loses confidence because a calendar moved. That promise stands, and it is tested.

The cup is the other half, and it is **not forgetting**. A mind that ages must also be able to put
something down. Once a moment is *consolidated* - baked into the weights, or carried by the narrative
the mind wakes up into - the raw record is no longer the only thing holding it. It may wear away the
way a person's does: nobody remembers the exact words of a conversation from three years ago; they
remember that it happened, and what it meant, because it is part of them now.

```
   full    the record as written
    │      (the person's exact words are kept)
    ▼
   gist    the anchor and the head of it - the details have worn a little
    │      (the person's exact words are STILL kept)
    ▼
   trace   that it happened, when, and what it was about - the details are gone
```

One rung per night. Only while the raw record is over the cup's budget. Only for records that have
been consolidated, and least recently recalled.

**Never touched:** a tender mark, a hypothesis, an entity, an open loop, a standing rule, a thought,
anything recalled inside the protection window - and the journal of turns, which is not on the ladder
at all.

Why it matters: the mind keeps running without getting heavier, without getting slower, and without
spending tokens re-reading its own childhood. The cup is not a limit on memory. It is a limit on the
**raw record**, and it only ever overflows with what has already been absorbed.

Recall renders each rung honestly - "the details have worn a little" - so the mind knows the
difference between remembering and half-remembering, and never states a gist as if it were a quote.

→ next: **05 · Feeling and time.**
