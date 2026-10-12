# 11 · Honest limits

A book that sells fog is worthless. This chapter is what is hard, what is unproven, and what is the
price — said plainly, so the idea keeps its integrity.

## What is unproven

- **Live self-updating LLMs do not exist in production — anywhere.** Test-time training works at lab
  scale. The design here (a verifier as the teacher, the natural filter of weight-space, the journal,
  the night) is a *coherent safety case*, but it is a **case**, not a proof. The batch phase will
  tell the truth before the live phase opens.
- **The prompts have not been tuned on the real hosted models.** Expect a round of tuning once it
  runs for real.
- **The anchor does not exist yet.** Until the owner's corrections are harvested into a frozen golden
  set, there is no immune system — and live updates without it are the one move to avoid.

## What is the price

- **An aging mind is mortal.** No re-download, no clean restore. One bad month and that personality
  has amnesia. **That is not a bug to engineer out — it is the price of the thing being real.** A
  model that cannot degrade cannot mature; it can only accumulate logs.
- **Weight-space filters, but it is not a conscience.** The natural dynamics keep a *single* event
  from scarring the mind. They do **not** stop *repeated, deliberate* pressure from shaping it — a
  manipulator who pushes the same direction ten thousand times will move the model, because
  repetition is exactly what survives. This is why the verifier's job quietly grows: it must watch
  for anyone trying to *farm* consistent updates, not just grade quality.
- **The slow layer still needs a human.** A periodic review by the owner — personality audits,
  monthly — is part of the design, not a temporary crutch.

## What is hard engineering

- **Scale of memory.** "Nothing is forgotten" is a promise that, at large history, needs an ANN index
  to stay fast. The policy does not change; the machinery does.
- **Multi-persona aging.** Live per-persona adapters served at once is a real systems problem —
  training, swapping, and isolating an adapter per persona. This is where the design is most likely
  to strain, and it must be built deliberately.
- **Latency.** The companion feels slow when the turn path waits on remote calls it does not need to
  wait on. This is fixable without giving up a single feature — but it must be measured, not guessed.

## What must never happen

- A false thing attributed to the person. **Quote-or-die is not negotiable.**
- A silent change to the mind that the owner cannot see or reverse. **No journal, no honest night.**
- A safety mechanism that *replaces* the natural dynamics instead of trusting them. The owner's
  instruction stands: **naturally, naturally, naturally — only.**

---

*Everything here is engineering. None of it is physics. That is why it can be built.*
