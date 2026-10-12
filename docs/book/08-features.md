# 08 · The features — full catalog

Every feature of the companion, in one place. Grouped by what it serves. Each entry says what it is
and — where it matters — *why it only works with multiple minds.*

## Identity

- **Persona = API key.** The key *is* the persona. Same key on any device = same companion, same
  memory. Only the key's hash is stored; the key is shown once.
- **One being, many surfaces.** App, voice, SMS, WhatsApp, phone, email, Telegram — all read and
  write **one memory.** The companion is recognisably the same person everywhere.
- **Presets and clones.** Choose a personality from presets, or clone someone you love (their
  name, how they talk, things they actually said, memories you share). A's base prompt is
  *persona-vacant* — the identity is yours, not baked in.
- **Surface context.** The client can say where you are talking from (device/app). It shapes the
  current turn and is recorded as *provenance* — never as memory content. Memory is what you said;
  surface is where you said it.

## Memory

- **One memory, three authors.** Fact (A) · collateral, afterthought (B) · note, reading (C).
  Authorship is flat — nobody edits upstream.
- **Quote-or-die.** Nothing is attributed to you without your exact words, checked in code.
- **Recall — five lanes.** Topic (semantic + lexical) · mood (chord resonance) · entity (people and
  projects you named) · unaddressed (said, never followed up on) · tender (held back unless you raise
  it). Candidates are stitched into whole moments and labeled by layer, author, and date.
- **A never searches its own memory.** Memories *arrive.* This is the difference between a mind and a
  database.
- **Reconsolidation & supersession.** A memory can be re-read later — the interpretation is appended,
  the quote never changes. A revised fact supersedes the old one without deleting it.
- **Tender memory.** C can mark a moment as painful. It stays down until *you* raise it — one mind
  inhibiting another's recall.
- **Stubborn memory.** Nothing is silently dropped. No decay, no eviction for space. *(At scale this
  needs an ANN index; the policy does not change.)*
- **Inspect and wipe.** See everything it knows; wipe anything, per persona.

## Feeling

- **Chords.** Several feelings at once, declared by A (private suffix), read from you by C.
- **Mood drift.** A rolling blend, not a reset — carries across turns and days.
- **Mood-congruent recall.** A feeling pulls memories that feel like now.
- **The feeling strip.** A visible timeline of how the companion has felt — you can see a bad week.

## Time

- **Live seconds.** Seconds since it last spoke, seconds this request has run, seconds since you last
  spoke. The companion can *see* the clock (`check_clock`), and so can you (the app's running timer).
- **Local time & timezone.** It lives in your timezone and knows the hour — so "it's late where you
  are" is true.
- **Deadline awareness.** Loops with due dates + a clock = it can be anxious about tomorrow.

## Integrity

- **Verdicts.** Every answer is graded (GREEN / YELLOW / RED) with findings and a rationale — the
  integrity record A reads to know how it has been doing.
- **REQUIRED corrections.** When something is wrong, the correction reaches A, and A fixes it out
  loud: *"actually, no —"*.
- **Clean streak.** A motivating score A can feel — being judged has a psychological effect.
- **Revisit-and-revise.** If a past answer on the same topic got a different verdict, the companion
  records it and can change its mind: *"I said X then; now Y."*

## Multi-lobe functions (impossible with one mind)

Self-correction mid-sentence · sudden remembering · talk-while-doing · working after you leave ·
watching your feelings while it answers · seeing its own habits · feeling judged · holding a to-do
list it did not author · refusing to let a false thing stand · disagreeing with itself (ambivalence) ·
revisit-and-revise · the unaddressed queue · tender inhibition · composing outreach while you are
gone · auditing its own outreach · calibration ("where I tend to be wrong") · nightly consolidation ·
dreaming · graduating a hypothesis over weeks · **aging.**

## Initiative — always on

- **The heartbeat.** Every tick, per persona: reminders due, corrections, finished work, thoughts on
  its mind, long silences.
- **The voicing gate + the inhibitor.** Most thoughts are never said. Quiet hours, a daily cap, a
  minimum gap, and *don't interrupt a live conversation* keep it a friend, not a notification machine.
- **Reminders.** Always delivered — you asked for them.
- **Composing outreach.** A writes the message in the persona's voice; **B checks it contains nothing
  invented** before it is sent.

## Sleep — the nightly pass

- **Narrative.** A rewrites its own first-person story from the day; B strikes anything unsupported —
  no invented events in its own life story.
- **Psyche.** C consolidates readings and hypotheses into one hedged profile; B strikes lines without
  evidence.
- **Mirror.** C reads A's own replies → the habits A cannot see in itself.
- **Calibration.** B groups A's recent mistakes → "where you tend to be wrong."
- **Dream.** Far associations between memories far apart in time (deterministic picks) → audited.
- **Juice & fine-tune.** The day's high-variance turns are baked into A's weights — see the aging
  chapter. *(This is the existing batch path; the aging design generalises it.)*

## Consultants & the workshop

- **Consultants.** The moment a question lands, several minds start reasoning in parallel. A pulls
  their work mid-answer with `fetch_consults` (bounded wait — what's ready now, plus who's still
  working). No waiting, no stalling.
- **The workshop (deep lane).** Heavy multi-step work runs server-side, beside the wheel, never inside
  a turn. Patterns: single, map (parallel subtasks, merged), speculative (same task in parallel, the
  critic keeps the best). A delegates with `delegate_task` / `work_in_background`; the result comes
  back later as a thought. **The user's machine is never touched.**

## Channels & voice

- **Every human channel.** App · realtime voice · SMS · WhatsApp · calls · email · Telegram.
- **Realtime voice.** Mic → recognition → A streams → sentence chunker → TTS pipelined per sentence.
  **Barge-in**: you speak, audio stops at once. Pop-ups arrive on the same socket and are spoken.
- **Live events (SSE).** Clock, pop-ups, workshop results — pushed live to every device.

## The app

- **Chat, voice, live timer, pop-ups, memory view, feeling strip, open loops.**
- **The companion app (PWA).** Accounts, threads, a file workspace, a plugin panel, a
  **verification/meter panel**, and a **system-health modal.** This is the surface the person lives
  in; it must *reflect* every feature above.

→ next: **Part V · The roadmap.**
