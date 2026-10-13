// Where the persona key lives.
//
// On the web: localStorage. Inside the Capacitor / Tauri shells: the OS
// keychain, so the key never sits in a plaintext web store. Resolved at runtime,
// so there is no hard dependency on either shell - the same bundle runs in all
// four targets (browser, desktop, Android, iOS).

type Prefs = {
	get(options: { key: string }): Promise<{ value: string | null }>;
	set(options: { key: string; value: string }): Promise<void>;
	remove(options: { key: string }): Promise<void>;
};

const KEY = "sawii_key";
let cache = "";

function nativePrefs(): Prefs | null {
	const cap = (globalThis as unknown as { Capacitor?: { Plugins?: { Preferences?: unknown } } }).Capacitor;
	const prefs = cap?.Plugins?.Preferences as Prefs | undefined;
	return prefs && typeof prefs.get === "function" ? prefs : null;
}

export function readKey(): string {
	try {
		return localStorage.getItem(KEY) || cache;
	} catch {
		return cache;
	}
}

/** Pull the key out of the OS keychain on native, once, at startup. */
export async function hydrateKey(): Promise<string> {
	const prefs = nativePrefs();
	if (prefs) {
		try {
			const { value } = await prefs.get({ key: KEY });
			cache = value || "";
			return cache;
		} catch {
			/* fall through to the web store */
		}
	}
	return readKey();
}

export function writeKey(value: string): void {
	cache = value;
	try {
		if (value) localStorage.setItem(KEY, value);
		else localStorage.removeItem(KEY);
	} catch {
		/* private mode */
	}
	const prefs = nativePrefs();
	if (prefs) {
		const op = value ? prefs.set({ key: KEY, value }) : prefs.remove({ key: KEY });
		Promise.resolve(op).catch(() => {});
	}
}
