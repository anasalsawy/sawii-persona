// The inter-lobe channel: the minds talking to each other, in the open.
// Nothing here is invented by the UI — every row is an event the server
// published. A whispers to B (b_nudge), B asks A (lane_question), A answers
// (voice_answer), B thinks out loud (b_delta), B judges A (verdict).

import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { cn } from "@/lib/utils";
import type { LobeEvent } from "@/lib/types";

const LOBE: Record<string, { label: string; ring: string; text: string }> = {
	A: { label: "A", ring: "border-sky-500/40 bg-sky-500/10 text-sky-300", text: "text-sky-200" },
	B: { label: "B", ring: "border-violet-500/40 bg-violet-500/10 text-violet-300", text: "text-violet-200" },
	C: { label: "C", ring: "border-amber-500/40 bg-amber-500/10 text-amber-300", text: "text-amber-200" },
};

const KIND_LABEL: Record<string, string> = {
	nudge: "A → B",
	question: "B → A",
	answer: "A → B",
	thinking: "B thinking",
	late_memory: "B, after",
	memory: "B memory",
	judgement: "B on A",
	note: "C note",
};

export function InterLobe({ events }: { events: LobeEvent[] }) {
	return (
		<ScrollArea className="h-full">
			<div className="flex flex-col gap-2 p-3">
				{events.length === 0 && (
					<p className="px-1 py-6 text-center text-xs text-muted-foreground">
						The lobes speak here when they have something to say to each other.
						<br />
						Say something and watch.
					</p>
				)}
				{events.map((e) => {
					const lobe = LOBE[e.from] ?? LOBE.B;
					return (
						<div key={e.id} className="flex gap-2">
							<div
								className={cn(
									"mt-0.5 grid size-6 shrink-0 place-items-center rounded-md border text-[11px] font-semibold",
									lobe.ring,
								)}
							>
								{lobe.label}
							</div>
							<div className="min-w-0 flex-1">
								<div className="mb-0.5 flex items-center gap-1.5">
									<Badge variant="secondary" className="h-4 px-1.5 text-[10px] font-medium">
										{KIND_LABEL[e.kind] ?? e.kind}
									</Badge>
									{e.to && <span className="text-[10px] text-muted-foreground">→ {e.to}</span>}
								</div>
								<p className={cn("whitespace-pre-wrap break-words text-xs leading-relaxed", lobe.text)}>
									{e.text || <span className="text-muted-foreground">(no text published)</span>}
								</p>
							</div>
						</div>
					);
				})}
			</div>
		</ScrollArea>
	);
}
