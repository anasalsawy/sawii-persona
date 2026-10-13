# ui/ — the companion client (a fork of the Cline Hub webview)

**Why this exists.** The old `src/sawii_persona/web/index.html` is a 156-line chat page that
renders two things and discards ~90% of what the backend already emits. The owner wants a UI whose
*structure* matches the fact that three minds run at once: visible **channels**, nothing hidden,
live. The base is Cline's own client — forked and edited, per the owner's instruction.

## What the base is

`ui/webview/` is a Vite + React 19 + Tailwind 4 + shadcn app, taken from
`cline/cline@main : apps/cline-hub/src/webview`. It ships a rich component vocabulary already
built for exactly this problem:

| Component (local, kept) | Serves |
|---|---|
| `ai-elements/reasoning.tsx` | a live thinking stream |
| `ai-elements/tool.tsx`, `terminal.tsx`, `sandbox.tsx` | live tool chips + output |
| `ai-elements/chain-of-thought.tsx`, `plan.tsx`, `task.tsx`, `queue.tsx` | the working lane |
| `ai-elements/conversation.tsx`, `message.tsx`, `streamdown.tsx` | the chat |
| `ai-elements/checkpoint.tsx`, `artifact.tsx`, `commit.tsx` | state + artefacts |
| `components/ui/*` (shadcn) | the shell |
| `App.tsx` (1,454 lines) | rail + stage + view routing |

It already runs **in a browser without VS Code** — `src/vscode.ts` falls back to a WebSocket when
`acquireVsCodeApi` is absent. That is what makes the fork cheap.

## The fork surface — exactly six things to replace

| # | Coupling point | What it is | Replace with |
|---|---|---|---|
| 1 | `src/vscode.ts` (`postToHost`) | transport: WS to Cline hub `/browser` | sawii transport (SSE + REST + voice WS) |
| 2 | `src/lib/desktop-client.ts` | request/response over postMessage | sawii REST client |
| 3 | `webview-protocol.ts` | message types (imports `@cline/core`, `@cline/shared`) | local types, de-Cline'd |
| 4 | `@cline/ui` | `GeneratedMediaContent`, `components/markdown` | local stub |
| 5 | `@cline/shared` (+`/browser`) | `GeneratedMedia`, `USER_REJECTED_TOOL_REASON`, provider schema/catalog | local stub |
| 6 | `@cline/core` | `ChatMessage`, `ProviderListItem`, `ProviderModel`, account view | local stub |

Nothing else is coupled. `ai-elements/*` and `components/ui/*` are local source.

## The backend contract this will speak (already live)

| Event (SSE `GET /v1/events`) | Becomes |
|---|---|
| `delta.content` (`/v1/chat/completions` stream) | the conversation channel |
| `b_delta` | B's live thinking → `ai-elements/reasoning` |
| `b_tool_start` / `b_tool_delta` / `b_tool_done` | the workbench → `ai-elements/tool` |
| `b_nudge`, `lane_question`, `voice_answer` | the inter-lobe dialogue channel |
| `verdict` | B's judgement of A → a verdict card |
| `inner` | B/C whispering to A — **kinds only today** (see below) |
| `memory_updated`, `clock`, `message` | memory / feeling / outreach |

## Two things the owner must decide (tracked, not assumed)

1. **The in-turn inner voice is hidden by design.** `voice_a.py` writes
   `"[inner voice - not from the person; they cannot see this]"` and the bus `inner` event carries
   only `{from, kind}` — no text. Showing it is a deliberate reversal and needs a backend change.
   The working-lane dialogue (`b_nudge` / `lane_question` / `voice_answer`) is already available
   in full and needs no backend change.
2. **Where this lives.** `companion-app` is 404 for this GitHub identity, so the fork is built
   here and is portable into `companion-app` once access is granted.

## Plan — where this is

- **F0 — fork** ✅ base vendored, LICENSE + NOTICE retained, structure preserved.
- **F1 — stub** ✅ the three `@cline/*` packages are gone; the app is trimmed to the
  components it uses and builds clean (`ui/build.sh`). Cline's marks removed.
- **F2 — transport** ✅ `src/lib/api.ts` speaks sawii: REST + `POST /v1/chat/completions`
  (SSE) + `GET /v1/events` (SSE). No Cline hub, no VS Code, no second backend.
- **F3 — channels** ✅ the stage is split in the open:
  **conversation** (left) · **inter-lobe channel** (right, top) · **workbench** (right, bottom).
  Every row is a real event the server published — nothing is invented by the UI.
- **F4 — serve** ✅ built to `ui/dist`, copied to `src/sawii_persona/web/companion/`, served
  at `/app/` by the backend (`companion_route()`; falls back to the old page if unbuilt).

### The backend change this required

The in-turn inner voice used to be hidden: `inner` carried only `{from, kind}`. It now carries
the lobes' **actual words**, and each whisper is published on the live channel the moment it
happens — so the inter-lobe channel is real-time, not a post-hoc log. Gated by
`SAWII_SHOW_INNER` (default `true`). The working-lane dialogue (`b_nudge`, `lane_question`,
`voice_answer`, `b_delta`, `b_tool_*`) needed no change — it was already on the wire.

### Still open

- **Feeling channel** (mood/clock/loops pane) — clock + live dot are in the header today; the
  emotion strip and open loops have endpoints (`/v1/persona/emotion`, `/v1/persona/loops`) but
  no pane yet.
- **History bootstrap** — the conversation starts empty on load; the server keeps the tail
  (there is no "read my turns" endpoint yet).
- **Voice** — `/v1/voice` WS is not wired into this client yet.
- **Port to `companion-app`** — blocked on repo access; this fork is self-contained and moves as
  a directory.


- **F0 — fork** (done): base vendored, LICENSE + NOTICE retained, structure preserved.
- **F1 — stub**: replace the 3 `@cline/*` packages + de-Cline the protocol; the app compiles.
- **F2 — transport**: sawii REST + SSE client behind the same interface; chat streams end-to-end.
- **F3 — channels**: split the stage into conversation · inter-lobe · workbench · feeling.
- **F4 — serve**: build the app and serve it from the backend (Railway), replacing `/app`.
