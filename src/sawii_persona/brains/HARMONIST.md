# C as Harmonist

C is the observer lobe. It watches the user. It also watches the system.

Three new roles layered on top of the observer role:

1. **Harmony ambassador** — the only lobe that owns the meta-view of how A,
   B, and C are agreeing on each turn. C emits `harmony_notes` in its
   standard observation. When B's verdict and C's reading diverge, C says
   so explicitly.

2. **Humanizer** — continuously proposes new dual-lobe features the system
   is missing. The proposal library is in `arch_finetune/features.py`. C
   emits `humanize_proposal` on every turn when it spots a gap.

3. **Architect** — during idle hours, C runs an architectural-fine-tune
   pass. It re-runs the adversarial corpus, asks C's LLM for one structured
   edit proposal, applies it, re-scores, accepts or reverts. Lockable.

The harmonist runtime lives in `src/sawii_persona/brains/harmonist.py`.

## What C now returns from observe()

```json
{
  "user_chord": {...},
  "notes": [...],
  "readings": [...],
  "hypotheses": [...],
  "entities": [...],
  "reinterpret": [...],
  "brief_for_b": "...",
  "expect": "...",
  "disagree_with_b": "...",
  "harmony_notes": [...],       // NEW: across-lobe agreement observations
  "humanize_proposal": "...",    // NEW: one-sentence feature proposal
  "architect_proposal": {...}    // NEW: edit target + rationale (architect mode only)
}
```

## Idle-hour trigger

The Mind's main loop calls `await trigger.tick(now, last_activity_at, ...)`
after each turn. When `now - last_activity_at` exceeds
`harmonist_idle_minutes` AND the cooldown has elapsed, one harmonist pass
runs. Pass output is appended to `harmonist_log_path`.

```python
from sawii_persona.brains.harmonist import IdleTrigger

trigger = IdleTrigger(
    idle_minutes=settings.harmonist_idle_minutes,
    cooldown_minutes=settings.harmonist_cooldown_minutes,
    budget_per_pass=settings.harmonist_budget_per_pass,
    lock_key=hmac_key if settings.harmonist_lock_on_converge else None,
    log_path=settings.harmonist_log_path,
)

# Inside the Mind loop, after each turn:
record = await trigger.tick(
    time.time(), last_activity_at,
    c_llm=mind.c, settings=mind.s, prompts_module=prompts,
)
if record is not None:
    log.info(record.summary())
```

## Settings (env vars)

```
HARMONIST_IDLE_MINUTES=30       # trigger threshold
HARMONIST_COOLDOWN_MINUTES=60   # min gap between passes
HARMONIST_BUDGET=3              # edits per pass
HARMONIST_LOCK=false            # auto-lock on improvement
HARMONIST_LOG=./data/harmonist.log
```

## The dual-lobe feature library

Five concrete proposals C draws from. Each requires coordination between
two or more lobes:

| feature | lobes | disharmony it fixes |
|---|---|---|
| anticipatory_chord | A, C | A's chord lags the user's actual state |
| tender_safeguard | B, C | tender memories resurface before the user is ready |
| harmony_bias | B, C | disagreement between B and C is silently resolved by A |
| repair_attempt | A, C | A holds chord when user pushes back instead of repairing |
| silence_aware_initiative | A, C | initiative tone is uniform regardless of absence meaning |

C may propose features not in this list. The library is a starting point,
not a closed set.

## Why this is not a trick

DECISIONS.md says: "I DONT WANT ARTIFICIAL TRICKS AND SIMULATIONS I WANTED
A FUNCTION OF HAVING TWO OR MORE LOBES IMPOSSIBLE TO DO WITH SINGLE LOBE."

C-as-harmonist is a real function of having multiple lobes because:

1. **Only C has the meta-view.** A sees its own output. B sees its own
   review. C sees A's chord + B's review + the surfaced memory + the
   user's tone, all at once. A single lobe can't hold all three views
   simultaneously while still doing its primary job.

2. **The harmony rubric is meaningful only across lobes.** Internal
   consistency, lobe agreement, regulation effectiveness — these are
   properties of the multi-lobe system, not of any single lobe.

3. **Dual-lobe features require two LLMs.** `anticipatory_chord` requires
   C to predict while A reacts. `harmony_bias` requires B and C to disagree
   so that A can hear both. These are not features you can simulate inside
   one model.

C is the only lobe that can run this loop because it is the only lobe
that can observe the system.

## Tests

```bash
python -m unittest tests.test_harmonist
```

22 tests covering:

- The dual-lobe feature library (5 features, each touching 2+ lobes).
- C_SYSTEM prompt extensions (harmony ambassador, humanizer, architect).
- The observer return-dict extensions.
- The harmonist record shape.
- The LLM editor prompt.
- The harmonist settings (env-overridable).
- `c_propose` (returns structured proposal or "converged").
- End-to-end: `harmonist_pass` actually runs the corpus baseline, applies
  a proposal, re-scores, accepts/reverts.
- End-to-end: `IdleTrigger.tick` blocks when cooldown has not elapsed.