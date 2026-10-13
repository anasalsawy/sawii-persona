import { ArrowUpIcon, SquareIcon } from "lucide-react";
import { type KeyboardEvent, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

export function Composer({
	disabled,
	busy,
	onSend,
	onStop,
}: {
	disabled?: boolean;
	busy?: boolean;
	onSend: (text: string) => void;
	onStop: () => void;
}) {
	const [value, setValue] = useState("");
	const ref = useRef<HTMLTextAreaElement>(null);

	function submit() {
		const text = value.trim();
		if (!text || disabled) return;
		setValue("");
		onSend(text);
	}

	function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
		if (event.key === "Enter" && !event.shiftKey) {
			event.preventDefault();
			submit();
		}
	}

	return (
		<div className="border-t bg-background/80 p-3 backdrop-blur">
			<div className="flex items-end gap-2 rounded-xl border bg-card p-2 shadow-sm focus-within:ring-1 focus-within:ring-ring">
				<Textarea
					ref={ref}
					value={value}
					disabled={disabled}
					onChange={(e) => setValue(e.target.value)}
					onKeyDown={onKeyDown}
					placeholder={disabled ? "Paste a persona key to begin…" : "Say something…  (Enter to send, Shift+Enter for a new line)"}
					className={cn("max-h-40 min-h-[38px] resize-none border-0 bg-transparent shadow-none focus-visible:ring-0")}
					rows={1}
				/>
				{busy ? (
					<Button size="icon" variant="secondary" onClick={onStop} title="Stop">
						<SquareIcon className="size-4" />
					</Button>
				) : (
					<Button size="icon" onClick={submit} disabled={disabled || !value.trim()} title="Send">
						<ArrowUpIcon className="size-4" />
					</Button>
				)}
			</div>
		</div>
	);
}
