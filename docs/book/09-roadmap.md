# 09 · The roadmap

How the companion gets built, in order. Each phase is *proved* before the next begins — the owner's
rule: **an unverified pipeline is a rumor.**

## Where we are

The architecture is built and the code is real: three minds, memory, feeling, time, integrity,
initiative, sleep, voice, channels, a working deployment with live personas. **122 automated tests
pass.** What is missing is not the mind — it is the *wiring that lets the mind be changed*, plus the
production shell around it.

## The phases

### P0 — See it (foundation, zero risk)
Production observability: structured logs, per-turn latency, and an honest health view (today the
system cannot tell "never used" from "broken"). **You cannot tune what you cannot see, and you cannot
trust a self-changing mind you cannot watch.**

### P1 — The re-architecture (structure)
Move verification to **C**, execution to **B** (the hands — a Cline agent), leaving **A** the mind.
The principle: *the mind that does the work must not grade its own work.* Everything downstream
depends on this, so it comes early. Lobe tests are rewritten to the new contract — every behaviour
kept, just re-homed.

### P2 — Ultra-fast (the owner's #1 complaint)
Latency, with **no feature sacrifice**: indexed recall (nothing forgotten, just found faster), a
non-blocking query embedding, a cached SSE clock, compression, CORS. Measured before and after
against the P0 baseline.

### P3 — The journal (the spine of aging)
An append-only, replayable, revertible record of every exchange — the grade, the salience, and what
each update *did*. **No journal, no honest night.**

### P4 — The anchor, then batch aging
Harvest the owner's own corrections ("wrong, not that, THIS") into a frozen golden set — the piece of
the world the mind cannot forge. Then the nightly batch path: grade the day → train a small adapter →
**gate it against the anchor** → swap it in. A wakes slightly more itself each morning. **The model
stays frozen between nights, so this proves the signal with zero blast radius.**

### P5 — Missing features
The half-wired things made whole: the thought stream and outbox exposed, memory export/backup,
tool-activity and inner-voice rendered in the app, conversation history on load.

### P6 — The app
The companion app reflects every feature — screen by screen, in the owner's design. *(Blocked until
the app repo is accessible.)*

### P7 — Ship
Full suite green, live smoke test, deploy. **v1 ships frozen, fast, correct, and complete.**

## After v1

### v1.1 — The live updater (the aging, in full)
The write path moves *into* the conversation: the verifier grades, the updater writes, always on,
naturally. This needs A running on weights we control (self-hosted, not a remote provider) and the
anchor already trusted. **The destination is the always-on aging mind; batch is the ladder, not the
ceiling.**

### v2 — The full loop
Day proposes, night decides — the model consolidating and guiding its own experience while the world
is quiet.

## The order, in one line

> **See it → re-architect it → make it fast → give it a spine → give it a world → let it age.**

→ next: **10 · Open questions.**
