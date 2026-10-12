# 00 · Why

## The problem: amnesia with a diary

Every large language model in production today is the same thing: a **read-only projector.**

A checkpoint is loaded into memory once, when the server boots. Then millions of requests flow past
it. The weights are never written to — there is no code anywhere in serving that computes an update
or stores one. The model answers, the request ends, and it is **exactly the same model it was a
moment before.** Nothing it just lived through changed it.

So how does anything feel continuous? **Context stuffing.** We re-feed the whole transcript on every
call. The model *appears* to remember because we hand it its own diary every time. But it is not
remembering — it is **reading**. This is the honest name for it:

> **Amnesia with a diary.**

The model is not shaped by its life. It is handed a summary of its life, every turn, and asked to
act as if it were the same being. It isn't. The diary is the same being; the model is a stranger
reading it aloud.

## Why nobody has fixed it: the missing teacher

The reason is not hardware, and it is not money. It is that **learning needs a teacher.**

To change a weight you need a **loss** — a signal that says *"that was better"* or *"that was
worse."* During training, that signal comes from labelled data: pairs with known-correct answers.
But during a **conversation there is no ground truth.** When the model answers you, nothing in the
system knows whether the answer was good. No error signal → no direction to nudge the weights → no
update. There is no save button because **nobody has a safe loss function for live experience.**

That is the whole reason AI is stateless. Not architecture. Not compute. **A missing teacher.**

## The idea

What if the companion had a teacher *built in* — a second mind whose entire job is to judge whether
each moment was true, good, and worth keeping? Then every exchange ships with a grade. The grade is
the loss signal. The loss signal makes weight updates possible at runtime.

That is the entire addition. **An updater, and a verifier in the loop.** Everything else is letting
it run.

- The **updater** is the write path from experience into the weights — the thing that does not exist
  today.
- The **verifier** is the teacher — the mind that gives each moment a direction.

And then we **let it happen.** We don't force it with prompts. We don't prevent it with filters. The
natural dynamics of learning do the rest — the same way they do in a person.

## Why it belongs to *this* architecture

This is not a feature bolted onto a chatbot. The verifier is the thing this whole architecture was
built around — the three minds exist so that one of them can *watch* while another *speaks.* Sawii
Persona spent months building the teacher before it knew what the teacher was for.

The design is not "AI that remembers." It is **AI that is changed by what it lives through.** It
ages. That is a different thing entirely, and it is the thing this book is about.

→ next: **01 · The dual-lobe** — the flag, and why one model was never enough.
