"""Architectural fine-tuning for sawii-persona.

A process for finding the configuration (Settings, prompts, coefficients) at
which the three-lobe system produces harmony rather than disharmony. The
process:

  1. Run an adversarial prompt corpus against the current configuration.
  2. Score the run on six harmony axes.
  3. An editor model proposes edits to the configuration surface.
  4. Apply, re-run, accept or revert.
  5. On convergence, lock and sign the configuration.

Each module owns one step:
  corpus     - the adversarial prompts and expected-behaviour annotations.
  runner     - executes a corpus against a Mind with scripted or real LLMs.
  scorer     - computes the six harmony axes from a corpus run.
  editor     - the LLM-driven edit loop with bounded budget.
  features   - dual-lobe feature proposals (the humanizer's library).
  snapshot   - configuration save / restore.
  lock       - cryptographic signing of a locked configuration.
  drift      - sample real traffic and re-score, alert on threshold breach.

The C-as-harmonist runtime lives in src/sawii_persona/brains/harmonist.py;
it imports the editor, runner, scorer, snapshot, and lock modules from here.

Run end-to-end:
    python -m arch_finetune.cli --budget 20 --lock-on-converge
"""