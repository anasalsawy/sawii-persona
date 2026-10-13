# Platforms — browser · desktop · Android · iOS

Production is four targets. This is how one client reaches all four, and what
each target actually costs.

## The constraint that makes this easy

**The client is thin.** The backend owns everything that matters: the three lobes,
memory, the wheel, the workspace, the voice pipeline, sleep. A client only needs
to *talk* to it — REST, one SSE stream, one WebSocket. There is no on-device
model, no on-device state, no local database.

That is why **one UI codebase can serve all four targets**, and why the answer is
not "write four apps".

## The recommendation: one bundle, wrapped

```
                    ui/dist  (one Vite build — React 19 + the forked components)
                        │
   ┌────────────┬───────┴────────┬─────────────────┐
   │            │                │                 │
 browser      desktop         Android            iOS
 PWA          Tauri v2        Capacitor          Capacitor
 (install-    (~3 MB native   (native shell +    (native shell +
  able)        binary, system  plugin bridge)     plugin bridge)
               webview)
```

| Target | How | Why this one |
|---|---|---|
| **Browser** | the web build + a PWA manifest | already installable; zero extra work |
| **Desktop** | **Tauri v2** | ~3 MB binary using the OS webview (not a bundled Chromium). Same Rust toolchain also targets mobile if we ever want to drop Capacitor |
| **Android** | **Capacitor** | the most mature "wrap the web build, ship to Play" path; real native plugin bridge for mic, push, files, share |
| **iOS** | **Capacitor** | same code as Android; only the signing/toolchain differs |

**One UI, four shells.** Fix a bug in a channel pane once, all four get it.

### Why not React Native, and why not Kotlin/Swift

- The UI is **DOM**. Everything valuable here — `@base-ui/react`, Tailwind,
  shadcn, `streamdown`, `shiki`, and the forked `ai-elements` components — has no
  React Native equivalent. Moving to RN means **rewriting every component and
  throwing away the fork** we just built on. That is the expensive path, and it
  buys nothing the wrapper doesn't: this is a *rich-panel* app (streams, chips,
  panes), not a native-navigation app.
- Truly native (Kotlin + Swift) is **four codebases** maintained by one person.
  It would double the UI work and permanently fork the design.

If "Android native" to you means *Kotlin specifically*, say so — but it costs a
full rewrite of the client for every platform, forever. The wrapped build ships
to the Play Store and the App Store as a real, installable app either way.

## What native actually buys — and therefore what must be wired

The webview gives us the UI; these are the things that must be native, per shell:

| Need | Web | Native shells |
|---|---|---|
| **Voice** (mic → ASR, TTS out) | browser Web Speech | Capacitor mic + audio plugins (needed for a real phone experience) |
| **The persona speaks first** (outreach) | SSE while the tab is open | **push notifications** (FCM / APNs) — see the hard part below |
| **Key storage** | localStorage | OS keychain ✅ *wired* (`src/lib/store.ts`) |
| **Files / workspace** | `<input type=file>` | native file picker + share sheet |
| **Always-on presence** | SSE while open | background task + push |

## The three hard consequences (named, not smuggled)

1. **CORS is now required.** The shells load bundled assets from
   `capacitor://localhost` / `tauri://localhost` and call the deployed server
   cross-origin. This was reverted from the repo earlier; **it is back** —
   `SAWII_CORS_ORIGINS` (default `*`, safe because auth is a bearer key, never a
   cookie). Browser stays same-origin.

2. **"Always-on presence" cannot be a socket on mobile.** iOS and Android suspend
   background network connections within seconds. The presence idea from earlier
   — *the message arrives and something notices without waking A* — must become
   **server-side**: the server watches the inbound channels, decides, and pushes.
   The socket is only for while the app is open. This is a backend feature, not a
   client one, and it is the same work the presence lane needed anyway.

3. **Auth needs a device story.** A bearer persona key in the keychain works
   today. For push, the device must register a token against the persona, and the
   server must be able to send to it. Small, but it has to exist.

## Configurability (done)

The bundle reads `VITE_SAWII_API_BASE` at build time:

```bash
# browser build served by the server itself (same origin)
npm run build

# shells that call the deployed backend
VITE_SAWII_API_BASE=https://dl-companion-production.up.railway.app npm run build
```

Empty = same origin. That single switch is what lets one bundle serve all four.

## What is needed to actually produce store builds

- **Android**: JDK 17 + Android SDK, a signing keystore, a Play Console account.
- **iOS**: macOS + Xcode, an Apple Developer account, provisioning profiles.
- Neither can be produced in this sandbox — these are your machine's job. The
  scaffold can be committed and built on your side.

## Order

1. **Browser** — already shipping; add the PWA install + offline shell.
2. **Presence as server-push** — the backend half of outreach on mobile.
3. **Capacitor scaffold** — `capacitor.config.ts`, `android/`, `ios/`, plugins for
   mic / push / preferences. Committed, built on your machine.
4. **Tauri scaffold** — `src-tauri/`, desktop window, keychain store.
5. **Store packaging** — icons, splash, signing, listings.
