import { useEffect, useRef, useState } from "react";
import { ActivityIcon, CircleDotIcon, WrenchIcon } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Separator } from "@/components/ui/separator";
import {
	Conversation,
	ConversationContent,
	ConversationScrollButton,
} from "@/components/ai-elements/conversation";
import { Message, MessageContent, MessageResponse } from "@/components/ai-elements/message";
import { Composer } from "@/components/Composer";
import { InterLobe } from "@/components/InterLobe";
import { Workbench } from "@/components/Workbench";
import { hydrateKey, readKey, sawii } from "@/lib/api";
import type { Clock, LobeEvent, Persona, Turn, Verdict, WorkItem } from "@/lib/types";
import { cn } from "@/lib/utils";

let seq = 0;
const uid = (prefix: string) => `${prefix}-${Date.now().toString(36)}-${(seq++).toString(36)}`;

const LOBE_OF: Record<string, string> = {
	b_delta: "B",
	b_nudge: "A",
	lane_question: "B",
	voice_answer: "A",
	verdict: "B",
	inner: "B",
	memory_updated: "B",
	b_queued: "B",
	b_start: "B",
	b_done: "B",
	deep_done: "B",
	message: "A",
	clock: "A",
};

export default function App() {
	const [key, setKey] = useState(readKey());
	const [entered, setEntered] = useState(Boolean(readKey()));
	const [persona, setPersona] = useState<Persona | null>(null);
	const [clock, setClock] = useState<Clock>({});
	const [turns, setTurns] = useState<Turn[]>([]);
	const [lobe, setLobe] = useState<LobeEvent[]>([]);
	const [work, setWork] = useState<WorkItem[]>([]);
	const [live, setLive] = useState(false);
	const [busy, setBusy] = useState(false);
	const [error, setError] = useState("");
	const abort = useRef<AbortController | null>(null);
	const turnRef = useRef<string>("");

	function note(from: string, kind: string, text: string, to = "") {
		setLobe((prev) => [...prev.slice(-199), { id: uid("lobe"), at: Date.now(), from, to, kind, text }]);
	}

	async function enter(candidate: string) {
		const k = candidate.trim();
		if (!k) return;
		sawii.setKey(k);
		try {
			const p = await sawii.get<Persona>("/v1/persona");
			setPersona(p);
			setKey(k);
			setEntered(true);
			setError("");
		} catch (e) {
			sawii.setKey("");
			setError(`That key was refused: ${(e as Error).message}`);
		}
	}

	// Inside a native shell the key lives in the OS keychain, not the web store.
	useEffect(() => {
		let alive = true;
		hydrateKey().then((stored) => {
			if (!alive || !stored) return;
			setKey(stored);
			sawii.setKey(stored);
			sawii
				.get<Persona>("/v1/persona")
				.then((p) => {
					if (!alive) return;
					setPersona(p);
					setEntered(true);
				})
				.catch(() => {});
		});
		return () => {
			alive = false;
		};
	}, []);

	// clock + the live channel
	useEffect(() => {
		if (!entered) return;
		sawii.get<Clock>("/v1/persona/clock").then(setClock).catch(() => {});
		return sawii.events({
			onOpen: () => setLive(true),
			onError: () => setLive(false),
			onEvent: (type, data) => {
				if (type === "clock") {
					setClock(data as Clock);
					return;
				}
				if (type === "message") {
					setTurns((prev) => [
						...prev,
						{ id: uid("t"), role: "assistant", text: String(data.text ?? ""), at: Date.now() },
					]);
					note("A", "outreach", String(data.reason ?? "spoke first"));
					return;
				}
				if (type === "b_tool_start") {
					const id = String(data.id ?? data.job ?? uid("tool"));
					setWork((prev) => [...prev, { id, name: String(data.name ?? "tool"), state: "running", text: "", at: Date.now() }]);
					return;
				}
				if (type === "b_tool_delta") {
					const id = String(data.id ?? data.job ?? "");
					setWork((prev) => prev.map((w) => (w.id === id ? { ...w, text: (w.text + String(data.text ?? "")).slice(-4000) } : w)));
					return;
				}
				if (type === "b_tool_done") {
					const id = String(data.id ?? data.job ?? "");
					setWork((prev) => prev.map((w) => (w.id === id ? { ...w, state: data.error ? "failed" : "done" } : w)));
					return;
				}
				const from = LOBE_OF[type] ?? "B";
				const text = String(
					data.text ?? data.note ?? data.question ?? data.answer ?? data.delta ?? data.rationale ?? "",
				);
				const kind =
					type === "b_nudge" ? "nudge"
					: type === "lane_question" ? "question"
					: type === "voice_answer" ? "answer"
					: type === "b_delta" ? "thinking"
					: type === "verdict" ? "judgement"
					: type === "inner" ? "late_memory"
					: type;
				note(from, kind, text || `(${type})`);
			},
		});
	}, [entered]);

	async function send(text: string) {
		setTurns((prev) => [...prev, { id: uid("t"), role: "user", text, at: Date.now() }]);
		const id = uid("t");
		turnRef.current = id;
		setTurns((prev) => [...prev, { id, role: "assistant", text: "", streaming: true, at: Date.now() }]);
		setBusy(true);
		abort.current = new AbortController();
		const patch = (fn: (t: Turn) => Turn) =>
			setTurns((prev) => prev.map((t) => (t.id === id ? fn(t) : t)));
		try {
			await sawii.send(
				text,
				{
					onDelta: (chunk) => patch((t) => ({ ...t, text: t.text + chunk })),
					onTool: (tool) => patch((t) => ({ ...t, tools: [...(t.tools ?? []), tool] })),
					onInner: (inner) => {
						patch((t) => ({ ...t, inner: [...(t.inner ?? []), inner] }));
						note(inner.from || "B", "late_memory", inner.text || `(${inner.kind})`);
					},
					onVerdict: (verdict) => patch((t) => ({ ...t, verdict: verdict as Verdict })),
					onError: (detail) => setError(detail),
					onDone: () => patch((t) => ({ ...t, streaming: false })),
				},
				abort.current.signal,
			);
		} catch (e) {
			if ((e as Error).name !== "AbortError") setError((e as Error).message);
		} finally {
			patch((t) => ({ ...t, streaming: false }));
			setBusy(false);
			abort.current = null;
		}
	}

	// ---- gate -----------------------------------------------------------------
	if (!entered) {
		return (
			<div className="grid min-h-screen place-items-center bg-background p-6">
				<div className="w-full max-w-sm space-y-4">
					<div className="space-y-1">
						<h1 className="text-2xl font-semibold tracking-tight">Sawii</h1>
						<p className="text-sm text-muted-foreground">
							Paste your persona key. The key <em>is</em> the persona - the same key on
							any device is the same being.
						</p>
					</div>
					<Input
						value={key}
						onChange={(e) => setKey(e.target.value)}
						onKeyDown={(e) => e.key === "Enter" && enter(key)}
						placeholder="sp_..."
					/>
					<Button className="w-full" onClick={() => enter(key)}>
						Enter
					</Button>
					{error && <p className="text-sm text-destructive">{error}</p>}
				</div>
			</div>
		);
	}

	return (
		<div className="flex h-screen flex-col bg-background text-foreground">
			<header className="flex items-center gap-3 border-b px-4 py-2.5">
				<CircleDotIcon className={cn("size-4", live ? "text-emerald-500" : "text-muted-foreground")} />
				<div className="min-w-0">
					<div className="truncate text-sm font-medium">{persona?.name || "unnamed persona"}</div>
					<div className="text-[11px] text-muted-foreground">
						{live ? "live" : "connecting..."} - three minds, one being
					</div>
				</div>
				<div className="ml-auto flex items-center gap-3 text-[11px] text-muted-foreground">
					{clock.since_last_reply_s != null && (
						<span>{Math.round(Number(clock.since_last_reply_s))}s since it spoke</span>
					)}
					<Badge variant="secondary" className="font-mono text-[10px]">
						{clock.now ?? "-"}
					</Badge>
				</div>
			</header>

			{error && (
				<div className="border-b bg-destructive/10 px-4 py-1.5 text-xs text-destructive">{error}</div>
			)}

			<main className="flex min-h-0 flex-1">
				<section className="flex min-w-0 flex-1 flex-col">
					<Conversation className="flex-1">
						<ConversationContent className="gap-6">
							{turns.length === 0 && (
								<p className="py-10 text-center text-sm text-muted-foreground">
									Say hello. The other two channels fill in as it thinks.
								</p>
							)}
							{turns.map((turn) => (
								<Message key={turn.id} from={turn.role}>
									<MessageContent>
										{turn.inner?.length ? (
											<div className="mb-1 flex flex-wrap gap-1">
												{turn.inner.map((inner, n) => (
													<Badge key={n} variant="outline" className="text-[10px]">
														{inner.from} to A - {inner.kind}
													</Badge>
												))}
											</div>
										) : null}
										<MessageResponse>{turn.text || (turn.streaming ? "..." : "")}</MessageResponse>
										{turn.verdict && (
											<div className="mt-1 flex items-center gap-2 text-[11px] text-muted-foreground">
												<Badge variant="secondary" className="text-[10px]">
													verified: {String(turn.verdict.level ?? "?")}
												</Badge>
												{turn.verdict.rationale ? (
													<span className="truncate">{String(turn.verdict.rationale)}</span>
												) : null}
											</div>
										)}
									</MessageContent>
								</Message>
							))}
						</ConversationContent>
						<ConversationScrollButton />
					</Conversation>
					<Composer disabled={!entered} busy={busy} onSend={send} onStop={() => abort.current?.abort()} />
				</section>

				<Separator orientation="vertical" />

				<aside className="flex w-[380px] shrink-0 flex-col">
					<div className="flex items-center gap-2 border-b px-3 py-2">
						<ActivityIcon className="size-3.5 text-violet-400" />
						<span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
							Inter-lobe channel
						</span>
					</div>
					<div className="min-h-0 flex-1">
						<InterLobe events={lobe} />
					</div>
					<div className="flex items-center gap-2 border-y px-3 py-2">
						<WrenchIcon className="size-3.5 text-amber-400" />
						<span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
							Workbench - the hands
						</span>
						{work.some((w) => w.state === "running") && (
							<Badge variant="secondary" className="ml-auto text-[10px]">
								running
							</Badge>
						)}
					</div>
					<div className="h-72">
						<Workbench items={work} />
					</div>
				</aside>
			</main>
		</div>
	);
}
