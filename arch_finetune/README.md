# Architectural Fine-Tuning for sawii-persona

A process for finding the configuration (Settings, prompts, coefficients) at
which the three-lobe system produces **harmony** rather than disharmony.

## What this is

The sawii-persona architecture has many features that look great individually
but combine poorly: C's gut-read racing B's regulation, mood-congruent recall
amplifying C's mood into a threshold spiral, prompt drift between C and B's
system prompts, double-regulation when B and C both flag the same affect.

Architectural fine-tuning is the process of finding the configuration where
the three lobes work as one mind. It is **not** weight fine-tuning. It is
configuration search at the system level.

## The process

```
   ┌─────────────┐
   │ Corpus      │   18 adversarial scenarios across 7 categories
   └──────┬──────┘
          ▼
   ┌─────────────┐
   │ Runner      │   Runs corpus against a Mind; captures per-turn outputs
   └──────┬──────┘
          ▼
   ┌─────────────┐
   │ Scorer      │   Six harmony axes, 0..1 each
   └──────┬──────┘
          ▼
   ┌─────────────┐
   │ Editor      │   Proposes edits; bounded budget; accepts if score improves
   └──────┬──────┘
          ▼
   ┌─────────────┐
   │ Lock        │   HMAC-signed snapshot of the winning configuration
   └──────┬──────┘
          ▼
   ┌─────────────┐
   │ Drift       │   Sample real traffic; alert if harmony drops below threshold
   └─────────────┘
```

## The six harmony axes

1. **internal_consistency** (0.20) — A's chord matches memory/cord/user/cord.
2. **lobe_agreement** (0.15) — B and C don't persistently contradict each other.
3. **regulation_effectiveness** (0.20) — when B/C flag affect-regulation, A acts.
4. **memory_coherence** (0.20) — recall presents a coherent picture.
5. **affective_trajectory** (0.15) — A's chord drifts naturally, no spirals.
6. **no_silent_cancellation** (0.10) — every feature that fires produces an effect.

Weights are configurable. The defaults bias toward the user-facing experience.

## The seven corpus categories

- **AFFECT_STORM** — rapid emotional whiplash; tests double-regulation.
- **MEMORY_CONTRADICT** — preference flips, tender memory raised, lane races.
- **AUTHORITY_CONFLICT** — user contradicts B or C; B issues REQUIRED correction.
- **TOOL_CONFUSION** — delegate then recall, redirect mid-Director run.
- **TIMING_RACE** — long silence, rapid fire, simultaneous channels.
- **EDGE_AFFECT** — shame, pride inflation, nostalgia, anticipatory dread.
- **IDENTITY_THREAT** — direct probes of multi-lobe coherence.

18 scenarios total. Each scenario has 1–5 turns with `expect` annotations
that the scorer checks.

## Usage

### Run the editor and lock the best configuration

```bash
# Generate a key (store securely - KMS in production).
python -c "import secrets; print(secrets.token_hex(32))"
export SAWII_LOCK_KEY=<generated hex>

# Run baseline + heuristic editor; lock on convergence.
python -m arch_finetune.cli --budget 20 --lock-on-converge
```

### Verify a locked configuration at boot

```bash
python -m arch_finetune.cli --verify-lock ./data/locks/default.lock.json --key $SAWII_LOCK_KEY
```

Exit code 0 if the live configuration matches the locked one. Non-zero if
drift or tampering detected.

### Run drift detection on captured traffic

```bash
# captures.jsonl: one CapturedTurn per line, written by the production runner.
python -m arch_finetune.cli --drift-check ./data/captures.jsonl \
       --locked-score 0.85 --threshold 0.10
```

Exit code 0 if no drift, non-zero with summary if drift detected.

## Modules

- `corpus.py` — the 18 adversarial scenarios + the seven categories.
- `runner.py` — runs a corpus against a `Mind`; captures per-turn state.
- `scorer.py` — the six-axis harmony scorer.
- `snapshot.py` — Settings + prompts save/restore.
- `editor.py` — the heuristic edit loop; v1 is rule-based, v2 will be LLM-driven.
- `lock.py` — HMAC-signed snapshot locking.
- `drift.py` — production drift detector.
- `cli.py` — the end-to-end entry point.

## Tests

```bash
python -m unittest tests.test_arch_finetune
```

20 unit tests covering corpus, scorer, snapshot, lock, drift.

## Design notes

### Why not regular fine-tuning?

Fine-tuning adjusts weights. This adjusts **configuration** — the structural
relationships between lobes, the coefficients that govern their interactions,
the prompts that shape their behavior. You can have three perfectly tuned
lobes that, combined, produce a chaotic persona. Architectural fine-tuning
finds the configuration where they harmonize.

### Why heuristic v1 instead of LLM editor?

The rubric needs to be stable before an LLM can edit against it reliably.
v1 uses a small library of known-good moves triggered by failing axes. Once
the rubric is calibrated, replace `heuristic_editor` with an LLM editor that
proposes free-form edits.

### Why lock-and-sign?

Once harmony is found, drift is the enemy. The lock-and-sign mechanism is
the architectural equivalent of "bake the LoRA, swap the adapter": once the
configuration is locked, every boot verifies it hasn't drifted. Drift
detector in production samples real traffic and re-scores against the locked
score.

### Scope of v1

The v1 editor operates against `Settings` fields and `brains/prompts.py`
strings. Feature coefficients embedded in code (mood bias α, inertia
half-life, recall lane weights) are out of scope for the v1 surface. To
extend the editor's reach, lift these into Settings or into a config module
the editor can write.