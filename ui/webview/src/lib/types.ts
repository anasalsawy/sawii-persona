// The backend contract this client speaks. Kept intentionally loose: the
// server is the source of truth and evolves; the UI must not break when a
// new field appears on an event.

export type Role = "user" | "assistant" | "system";

export type Inner = { from: string; kind: string; text?: string };

export type Turn = {
	id: string;
	role: Role;
	text: string;
	streaming?: boolean;
	/** who spoke to A mid-reply (B/C), kinds + text when the server sends it */
	inner?: Inner[];
	/** tools A used this turn (from the stream) */
	tools?: { name: string; id: string }[];
	verdict?: Verdict | null;
	at: number;
};

export type Verdict = {
	level?: string;
	rationale?: string;
	conscience?: string;
	[key: string]: unknown;
};

/** A row in the inter-lobe channel — the minds talking to each other. */
export type LobeEvent = {
	id: string;
	at: number;
	/** "A" | "B" | "C" — who is speaking */
	from: string;
	/** who they are speaking to ("" = the room) */
	to?: string;
	/** nudge | question | answer | thinking | late_memory | memory | judgement | note */
	kind: string;
	text: string;
};

/** A live tool call in the workbench (the hands). */
export type WorkItem = {
	id: string;
	name: string;
	state: "running" | "done" | "failed";
	text: string;
	at: number;
};

export type Clock = {
	now?: string;
	since_last_reply_s?: number;
	since_last_user_s?: number;
	spent_seconds?: number;
	[key: string]: unknown;
};

export type Persona = {
	id: number;
	name?: string;
	user_name?: string;
	timezone?: string;
	preferred_channel?: string;
	[key: string]: unknown;
};
