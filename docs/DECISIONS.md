# Decisions - and where they came from

The design was worked out in a long conversation (2026-10-03 → 10-08) with several coding agents,
which kept forgetting it. This file is the canonical record. Each decision quotes the owner's own
words (verbatim, typos kept) and says where it lives in the code. If something here conflicts with
older docs (`docs/origins/`), this file wins.

## Identity and product

| Decision | Owner's words | Code |
|---|---|---|
| A separate product, not Dual-Lobe | "i feel this is a completely different project tho because even the verification which is the holy grail of dual lobe has no real value in companion … u may call it sawii persona" | this repo |
| Production, public | "keep in mind this is intended as a production build meant for publicc use" | key-hash auth, per-persona isolation, rate limit, webhook signatures |
| Memory is the core; friendship grows; user at the centre; persona chosen or cloned | "its most important feature is its memory , user needs to feel like its friend , the friendship develops natually with tine … user may choose persona (personality) to be cloned from a known persona of loved person or selected from options" | `memory/`, `profiles.py` |
| A's system prompt is persona-vacant | "A sys prompt should be vacant in terms of presona because that will be defined by the user" | `brains/prompts.py: A_BASE`, `profiles.persona_frame` |
| Persona = API key, same being everywhere | (COMPANION.md, Phase 1) | `identity.py` |
| Runs server-side, independent out of the box | "as long as the machine where persona acts is server's side and persona stays as hook up to endpoint and go setup then yes" | everything; workshop in server workspace |

## The three lobes

| Decision | Owner's words | Code |
|---|---|---|
| Three brains | "i thought aboiut adding a third brain (LLM) not two" | `mind.py` |
| B = A's assistant-manager, renamed to avoid sycophancy | "B is an assistant to A , it holds the todolist ,writes notes soA doesnt forget,keepsschedules, provides directiobs, recalculations, provideds prospectives" · "idont want that 'say pleasent trghings' assistant side effect … give it another job title , makeit verifiermaybe" | `brains/adjutant.py` ("adjutant", dissent duty) |
| B as conscience / inner guide, after A answers, invisible | "B should feel like someone's conscienee or inner guide" · answers: "2.after 3.NO" | conscience note (ephemeral), meter hidden by default |
| B verifies concurrently and tells A; A feels judged; a motivating score | "it pushes also to A the details of that verificarion … A feels that a verification is coming so the psych effect of being judged take effect we can also add some score" | integrity record + clean streak in A's context |
| The wheel: C assists B, B assists A | "if wemade B an assistant to A and mde C an assistant toB hpowwouldthat work" | C → `brief_for_b` → B → conscience/loops → A |
| C = fine-tuned psych model, Featherless preferred, long-term | "C the psych agent i want to use a fine-tuned psych agent from hugging face or featherless … continously assess the pswych … over many days and weeks and montsh" | default `Compumacy/Psych_Qwen_32B`; hypotheses + nightly profile |
| C records behaviour ("you went quiet when I pushed") | "those kind of things are the things i want the emotional or psychic model to monitor and record … 'user hesitated here implying ....' 'user was annoyed by this'" | `reading` layer |
| Psyche = hypothesis, not fact | answer: "1.hypotheis" | `hypotheses` table with fading confidence |
| Parallel consults pulled by A | "the moment user sends question all models start reasoning … A … uses a tool named 'fetch consults'" | `brains/consults.py`, `fetch_consults` tool |
| OpenHands beside the wheel, not under it; group work in parallel | "explain more how openhands work and how can we achieve group working on one task" · "i want the group work to be simltenous , the goal is completing the task in 1/3 the time" | `lanes/deep.py` (map / speculative lanes, B critic) |

## Memory

| Decision | Owner's words | Code |
|---|---|---|
| Single source of truth, three layers stitched into one record | "memory to be a single source of truth … 3 kinds ofmemory the facts … the non essential collateralinfo … and the emotional layer … when recallhappens it returns the slice consisting of the 3 layer" | `events` + `layers`, stitched recall |
| Three authors | "A writes the normal memory …, B reasons independantly collateral info and things missedd by A , C does the psych analysis" | `LAYER_AUTHOR` enforced in `records.add_layers` |
| A experiences memory, does not query it | "i domt want A quering its own memory iwant A toexperience his memorywritten by an observer" | no memory-search tool for A; recall arrives in context |
| Raw data is sacred | "the magic is in raw data , any rewrite of memry for purpose of summarization or any other purpose is strictlt forbidden" | verbatim journal; quotes carved; reconsolidation appends readings |
| Quote-or-die | (agreed in the first design: memories must be quotable and auditable) | `memory/grounding.py` |
| Emotion is a spectrum, concurrent | "emotions are a spectrum never are determined by strict values" · "something similar but complex with various emotions oncurrently" | `emotion.py` chords |
| Emotion ↔ text both ways; the speaker lobe processes it | "when persona encounters similar emotions , text memory associated with it surfaces and ehn it encounters certain text memoty … emotiion memory surfces … the one whoprocesses … to bethe speaker lobe" | mood lane + chord carried on recall; A declares `⟦feel⟧` and its mood is the recall key |
| Vague by design | "keep in mind this is a vague kind of memory by design matching embeddings" | loose recall band `SAWII_RECALL_BAND=0.30` |
| Embeddings extensively, via Featherless LLM, to broaden context | "i want touse embedding extensively forthe purposeofcontext broadening tomakeA aware of all relevant info" · "4.an embeddingLLM from featherless" · "Qwen/Qwen3-Embedding-8B" | `embedder.py`, wide retrieve → rank → budget → labeled slice |
| Human traits: spontaneous thoughts, intrusive recall, wit | "iwannamimick hhumanmemory ,iwant spontenous thoughts , insidoous recalls,randomwhitts" | wander, dream, intrusion lane |
| **Remembering things all of a sudden** | "THE THING THAT MAKES IT BEHAVE LIKE HUMAN LIKE REMEMBERING THINGS ALL OF SUDDEN" | `memory/spontaneous.py`: priming, chains, pops, late recall |
| One stream of thought without infinite context | "i want the agent to feel like its a one stream like one line of thought … snapshot then restore … problem of long context" | `context.py`: spine + self-narrative (rewritten at sleep) + loops + tail + recall |

## Life

| Decision | Owner's words | Code |
|---|---|---|
| Always on, pops up on its own | "we need to make it always on all the time , popping up on its own not just a chat bot" | `life/initiative.py` |
| Every human channel | "we will give it phone number, email address, whatsapp and all forms of communication humans have" | `channels/adapters.py` (SMS, WhatsApp, call, email, Telegram) |
| Realtime voice first, minimal lag | "the first thing i wanna make … is realtime voice conversation , it smost important and has to bve zero or minimal lag" | `voice/realtime.py`, web app |
| Sense of time, live seconds | "make it aware off both , time spent in request and time past since last responce and make timer with seconds running it can see it" | `timesense.py`, `check_clock`, live SSE clock |
| Nightly sleep & dream with fine-tune on raw juice | CSMC spec (pasted 2026-10-04) | `life/sleep.py`, `life/finetune.py`, `life/subconscious.py` |
| Not a zero-byte purge: the overflowing bowl | "a hard zero-byte purge … is too extreme … 'Lossy Overflowing Bowl' … Do NOT purge … tiered compression … overflow eviction" | compress after bake; evict oldest compressed only past capacity |
| Fine-tune plug and play, Fireworks | "set everythingsowhen we start fine tune its as simple as plug and play" · "im fixing that and setting up fireworks AI" | `SAWII_FINETUNE_PROVIDER=fireworks`; adapter auto-becomes A's model |

## Calls made in this rebuild (owner can overturn any of them)

* **Public verdict hidden by default** (`SAWII_SHOW_VERDICT=false`); B's verdict always reaches A.
  Reason: "verification meter part is notgonna be used by default in the persona".
* **SQLite (WAL)** storage - works out of the box on one server; Postgres is the scale path.
* **Default models**: A = Qwen3.8-Flash-Next on Featherless (owner: "lets workwith nofine tune with the
  3,8 next … from featherless"); swap A to a Fireworks/Together model when fine-tuning starts.
* **Two parallel peer reasoners** (THREE_BRAINS.md) stay parked; consultants cover "many minds at once".
* Tuning numbers (recall band 0.30, intrusion 12%, activation half-life 90 min, pop threshold 1.0,
  outreach cap 4/day, quiet hours 22:30-08:00, juice floor 0.55) are starting points, all env-tunable.
