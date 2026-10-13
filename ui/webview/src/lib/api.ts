// The sawii-persona transport. Same origin as the server that serves this app,
// so there is no CORS surface and no second backend.
//
//   REST  /v1/persona, /v1/persona/emotion, /v1/persona/clock, /v1/persona/loops
//   CHAT  POST /v1/chat/completions  (SSE stream)
//   LIVE  GET  /v1/events?key=       (SSE: the lobes talking in real time)
//   VOICE WS   /v1/voice             (not wired in this pass)

export type ChatHandlers = {
	onDelta?: (text: string) => void;
	onTool?: (tool: { name: string; id: string }) => void;
	onInner?: (inner: { from: string; kind: string; text?: string }) => void;
	onVerdict?: (verdict: Record<string, unknown>) => void;
	onError?: (detail: string) => void;
	onDone?: (text: string) => void;
};

export type EventHandlers = {
	onEvent?: (type: string, data: Record<string, unknown>) => void;
	onOpen?: () => void;
	onError?: (detail: string) => void;
};

// The backend may be same-origin (browser build served by the server) or remote
// (the desktop / Android / iOS shells, which load bundled assets from
// capacitor://localhost or tauri://localhost and call the deployed server).
// Set VITE_SAWII_API_BASE at build time to point the shells at a host.
export const API_BASE = ((import.meta as unknown as { env?: Record<string, string> }).env
	?.VITE_SAWII_API_BASE || "").replace(/\/+$/, "");

function url(path: string): string {
	return `${API_BASE}${path}`;
}

export { readKey, writeKey, hydrateKey } from "./store";

async function parseError(res: Response): Promise<string> {
	try {
		const body = await res.json();
		return String(body?.detail || body?.error || res.status);
	} catch {
		return String(res.status);
	}
}

export class Sawii {
	private key = "";

	setKey(key: string): void {
		this.key = key.trim();
		writeKey(this.key);
	}

	get hasKey(): boolean {
		return Boolean(this.key);
	}

	headers(): HeadersInit {
		return { "content-type": "application/json", authorization: `Bearer ${this.key}` };
	}

	async get<T = Record<string, unknown>>(path: string): Promise<T> {
		const res = await fetch(url(path), { headers: this.headers() });
		if (!res.ok) throw new Error(await parseError(res));
		return (await res.json()) as T;
	}

	async put<T = Record<string, unknown>>(path: string, body: unknown): Promise<T> {
		const res = await fetch(url(path), { method: "PUT", headers: this.headers(), body: JSON.stringify(body) });
		if (!res.ok) throw new Error(await parseError(res));
		return (await res.json()) as T;
	}

	/** One turn. The server owns the history; only the newest message matters. */
	async send(text: string, h: ChatHandlers, signal?: AbortSignal): Promise<void> {
		const res = await fetch(url("/v1/chat/completions"), {
			method: "POST",
			headers: this.headers(),
			body: JSON.stringify({ model: "sawii-persona", stream: true, messages: [{ role: "user", content: text }] }),
			signal,
		});
		if (!res.ok || !res.body) {
			h.onError?.(await parseError(res));
			return;
		}
		const reader = res.body.getReader();
		const decoder = new TextDecoder();
		let buffer = "";
		let full = "";
		for (;;) {
			const { value, done } = await reader.read();
			if (done) break;
			buffer += decoder.decode(value, { stream: true });
			let idx = buffer.indexOf("\n\n");
			while (idx >= 0) {
				const frame = buffer.slice(0, idx).trim();
				buffer = buffer.slice(idx + 2);
				idx = buffer.indexOf("\n\n");
				if (!frame.startsWith("data:")) continue;
				const payload = frame.slice(5).trim();
				if (payload === "[DONE]") continue;
				let json: any;
				try {
					json = JSON.parse(payload);
				} catch {
					continue;
				}
				const delta = json?.choices?.[0]?.delta ?? {};
				const chunk = delta.content;
				if (chunk) {
					full += chunk;
					h.onDelta?.(chunk);
				}
				if (json.sawii_tool) h.onTool?.(json.sawii_tool);
				if (json.sawii_inner) h.onInner?.(json.sawii_inner);
				if (json.sawii_verdict) h.onVerdict?.(json.sawii_verdict);
			}
		}
		h.onDone?.(full);
	}

	/** The live channel: every event the lobes publish, per persona. */
	events(h: EventHandlers): () => void {
		const es = new EventSource(url(`/v1/events?key=${encodeURIComponent(this.key)}`));
		const names = [
			"clock", "message", "inner", "memory_updated", "verdict",
			"b_queued", "b_start", "b_done", "b_nudge", "b_delta",
			"b_tool_start", "b_tool_delta", "b_tool_done",
			"lane_question", "voice_answer", "deep_done",
		];
		for (const name of names) {
			es.addEventListener(name, (ev) => {
				try {
					h.onEvent?.(name, JSON.parse((ev as MessageEvent).data));
				} catch {
					/* ignore malformed frame */
				}
			});
		}
		es.onopen = () => h.onOpen?.();
		es.onerror = () => h.onError?.("live channel disconnected");
		return () => es.close();
	}
}

export const sawii = new Sawii();
