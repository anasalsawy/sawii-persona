// The workbench: the hands at work. Tool calls stream here the moment they
// start, and their output grows live — while A keeps talking in the other
// channel. That concurrency is the point of the design.

import { CheckCircle2Icon, Loader2Icon, TerminalIcon, XCircleIcon } from "lucide-react";
import { ScrollArea } from "@/components/ui/scroll-area";
import { cn } from "@/lib/utils";
import type { WorkItem } from "@/lib/types";

export function Workbench({ items }: { items: WorkItem[] }) {
	return (
		<ScrollArea className="h-full">
			<div className="flex flex-col gap-1 p-3 font-mono">
				{items.length === 0 && (
					<p className="px-1 py-6 text-center text-xs text-muted-foreground">
						When A delegates real work, the tools run here — live, beside the conversation.
					</p>
				)}
				{items.map((item) => (
					<div key={item.id} className="rounded-md border bg-card/50">
						<div className="flex items-center gap-2 px-2 py-1.5 text-xs">
							{item.state === "running" && <Loader2Icon className="size-3.5 animate-spin text-amber-400" />}
							{item.state === "done" && <CheckCircle2Icon className="size-3.5 text-emerald-500" />}
							{item.state === "failed" && <XCircleIcon className="size-3.5 text-red-500" />}
							<TerminalIcon className="size-3.5 text-muted-foreground" />
							<span className="truncate font-medium">{item.name}</span>
						</div>
						{item.text && (
							<pre
								className={cn(
									"max-h-40 overflow-auto whitespace-pre-wrap break-words border-t px-2 py-1.5 text-[11px] leading-relaxed text-muted-foreground",
								)}
							>
								{item.text}
							</pre>
						)}
					</div>
				))}
			</div>
		</ScrollArea>
	);
}
