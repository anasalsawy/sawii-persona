"""All runtime configuration, read from the environment (prefix SAWII_).

Every model is a plain OpenAI-compatible endpoint, so any lobe can be moved to
another provider (Featherless, Fireworks, Together, a local vLLM) by changing
three variables and nothing else.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


_QUOTES = chr(34) + chr(39)


def load_dotenv(path: str = "") -> None:
    """Populate os.environ from a .env file. Existing env vars always win.

    Minimal on purpose: no dependency, no interpolation, no shell semantics.
    Lines are ``KEY=value``; ``#`` starts a comment, surrounding quotes are
    stripped, and an ``export `` prefix is tolerated.
    """
    if not path:
        here = os.path.dirname(os.path.abspath(__file__))
        for candidate in (
            os.path.join(os.getcwd(), ".env"),
            os.path.abspath(os.path.join(here, "..", "..", ".env")),
        ):
            if os.path.isfile(candidate):
                path = candidate
                break
        else:
            return
    try:
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.split(" #", 1)[0].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in _QUOTES:
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value

def _env(name: str, default: str = "") -> str:
    return os.environ.get(f"SAWII_{name}", default)


def _bool(name: str, default: bool) -> bool:
    raw = _env(name, "")
    if not raw:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    raw = _env(name, "")
    return int(raw) if raw.strip() else default


def _float(name: str, default: float) -> float:
    raw = _env(name, "")
    return float(raw) if raw.strip() else default


FEATHERLESS = "https://api.featherless.ai/v1"


@dataclass
class ModelEndpoint:
    """One OpenAI-compatible model: base URL, key, model id."""

    base_url: str
    api_key: str
    model: str
    timeout: float = 60.0
    # Reasoning models (Qwen3, DeepSeek-R1, ...) spend tens of seconds emitting a
    # hidden "thinking" stream (delta.reasoning) before the answer. That thinking
    # is the single biggest latency cost in a turn - measured at 26-74s per reply
    # vs ~3s with it off. We ask the provider to skip it; if the provider does not
    # support the flag we silently fall back (see llm.py).
    no_think: bool = True

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.model)


def _endpoint(prefix: str, *, model: str, base: str = FEATHERLESS, timeout: float = 60.0,
              inherit: "ModelEndpoint | None" = None) -> ModelEndpoint:
    base_url = _env(f"{prefix}_BASE_URL", "") or (inherit.base_url if inherit else base)
    api_key = _env(f"{prefix}_API_KEY", "") or (inherit.api_key if inherit else _env("FEATHERLESS_API_KEY", ""))
    return ModelEndpoint(
        base_url=base_url.rstrip("/"),
        api_key=api_key,
        model=_env(f"{prefix}_MODEL", model),
        timeout=_float(f"{prefix}_TIMEOUT", timeout),
        no_think=_bool("DISABLE_THINKING", True),
    )


@dataclass
class Settings:
    # --- storage -----------------------------------------------------------
    data_dir: str = field(default_factory=lambda: _env("DATA_DIR", "./data"))
    db_path: str = ""

    # --- security ----------------------------------------------------------
    admin_token: str = field(default_factory=lambda: _env("ADMIN_TOKEN", ""))
    public_base_url: str = field(default_factory=lambda: _env("PUBLIC_BASE_URL", "http://localhost:8800"))
    # Comma-separated allowed origins for the native shells (Capacitor/Tauri
    # load from capacitor://localhost, tauri://localhost, http://localhost) and
    # any separately hosted web build. "*" is safe here because auth is always a
    # bearer persona key - never a cookie. Empty string = no CORS middleware.
    cors_origins: str = field(default_factory=lambda: _env("CORS_ORIGINS", "*"))

    # --- the three lobes + helpers ----------------------------------------
    # A: the voice. Persona-vacant base prompt; plug-and-play fine-tuned model.
    a: ModelEndpoint = field(default_factory=lambda: _endpoint("A", model="Qwen/Qwen3.8-Flash-Next", timeout=90))
    # B: the adjutant (private verifier, loops keeper, conscience).
    b: ModelEndpoint = field(default_factory=lambda: _endpoint("B", model="openai/gpt-oss-120b", timeout=90))
    # C: the psych observer.
    c: ModelEndpoint = field(default_factory=lambda: _endpoint("C", model="Compumacy/Psych_Qwen_32B", timeout=120))
    # Embedder (context broadening + juice novelty).
    embed: ModelEndpoint = field(default_factory=lambda: _endpoint("EMBED", model="Qwen/Qwen3-Embedding-8B", timeout=20))
    # Consultants: comma separated model ids on the consult endpoint (default: B's provider).
    consult_models: list[str] = field(default_factory=lambda: [m.strip() for m in _env("CONSULT_MODELS", "").split(",") if m.strip()])
    consult_base_url: str = field(default_factory=lambda: _env("CONSULT_BASE_URL", ""))
    consult_api_key: str = field(default_factory=lambda: _env("CONSULT_API_KEY", ""))
    consult_wait_ms: int = field(default_factory=lambda: _int("CONSULT_WAIT_MS", 300))

    # --- behaviour ---------------------------------------------------------
    a_tools: bool = field(default_factory=lambda: _bool("A_TOOLS", True))
    show_verdict_to_user: bool = field(default_factory=lambda: _bool("SHOW_VERDICT", False))
    # Publish the lobes' actual words - not just `from`/`kind` - on the live
    # channel, so the companion UI can show the inter-lobe dialogue in the open.
    # This deliberately reverses the old "they cannot see this" rule: the owner
    # wants the minds visible. Set SAWII_SHOW_INNER=false to go back to kinds-only.
    show_inner_to_user: bool = field(default_factory=lambda: _bool("SHOW_INNER", True))
    tail_turns: int = field(default_factory=lambda: _int("TAIL_TURNS", 8))
    recall_budget_chars: int = field(default_factory=lambda: _int("RECALL_BUDGET_CHARS", 3500))
    recall_band: float = field(default_factory=lambda: _float("RECALL_BAND", 0.30))  # loose = associative
    unaddressed_days: float = field(default_factory=lambda: _float("UNADDRESSED_DAYS", 3.0))
    live_checks_max: int = field(default_factory=lambda: _int("LIVE_CHECKS_MAX", 3))  # B checks per reply, live
    psyche_half_life_days: float = field(default_factory=lambda: _float("PSYCHE_HALF_LIFE_DAYS", 21.0))

    # --- initiative ---------------------------------------------------------
    heartbeat_seconds: int = field(default_factory=lambda: _int("HEARTBEAT_SECONDS", 60))
    reflect_every_ticks: int = field(default_factory=lambda: _int("REFLECT_EVERY_TICKS", 30))
    quiet_hours: str = field(default_factory=lambda: _env("QUIET_HOURS", "22:30-08:00"))
    daily_outreach_cap: int = field(default_factory=lambda: _int("DAILY_OUTREACH_CAP", 4))
    min_gap_between_outreach_minutes: int = field(default_factory=lambda: _int("OUTREACH_GAP_MIN", 90))
    background_enabled: bool = field(default_factory=lambda: _bool("BACKGROUND", True))
    # The persona service also ships a developer console at /app. In production
    # the user-facing app is the gateway (companion-app), and two UIs on two
    # domains is how people end up editing the wrong one. Set SERVE_CONSOLE=false
    # on the deployed backend so exactly one interface exists.
    serve_console: bool = field(default_factory=lambda: _bool("SERVE_CONSOLE", True))

    # --- sleep / subconscious ----------------------------------------------
    sleep_hour: int = field(default_factory=lambda: _int("SLEEP_HOUR", 3))
    juice_floor: float = field(default_factory=lambda: _float("JUICE_FLOOR", 0.55))
    buffer_rows: int = field(default_factory=lambda: _int("BUFFER_ROWS", 2000))
    bowl_ratio: float = field(default_factory=lambda: _float("BOWL_RATIO", 0.9))

    # --- the overflowing cup --------------------------------------------------
    # The raw-record budget per persona. Nothing evaporates until the cup is over
    # it, and only records that are already consolidated (see life/evaporate.py).
    cup_chars: int = field(default_factory=lambda: _int("CUP_CHARS", 400_000))
    cup_protect_days: float = field(default_factory=lambda: _float("CUP_PROTECT_DAYS", 14.0))
    replay_ratio: float = field(default_factory=lambda: _float("REPLAY_RATIO", 0.2))
    finetune_min_examples: int = field(default_factory=lambda: _int("FINETUNE_MIN_EXAMPLES", 40))
    finetune_provider: str = field(default_factory=lambda: _env("FINETUNE_PROVIDER", "none"))  # none|fireworks|command
    fireworks_api_key: str = field(default_factory=lambda: _env("FIREWORKS_API_KEY", ""))
    fireworks_account: str = field(default_factory=lambda: _env("FIREWORKS_ACCOUNT", ""))
    fireworks_base_model: str = field(default_factory=lambda: _env("FIREWORKS_BASE_MODEL", "accounts/fireworks/models/qwen3-8b"))
    finetune_command: str = field(default_factory=lambda: _env("FINETUNE_COMMAND", ""))

    # --- aging: the per-turn weight update ---------------------------------
    # Where A's weights actually live. When A runs on a hosted/frozen endpoint
    # this stays empty and the update module is an honest no-op. Point it at a
    # self-hosted server (vLLM + LoRA, or the transformers/PEFT wrapper) that
    # accepts POST {persona_id, turn_id, level, reward, prompt, response}.
    update_url: str = field(default_factory=lambda: _env("UPDATE_URL", ""))
    update_timeout: float = field(default_factory=lambda: _float("UPDATE_TIMEOUT", 20.0))
    update_max_chars: int = field(default_factory=lambda: _int("UPDATE_MAX_CHARS", 6000))

    # --- voice -------------------------------------------------------------
    asr: ModelEndpoint = field(default_factory=lambda: ModelEndpoint(
        _env("ASR_BASE_URL", ""), _env("ASR_API_KEY", ""), _env("ASR_MODEL", "whisper-1")))
    tts: ModelEndpoint = field(default_factory=lambda: ModelEndpoint(
        _env("TTS_BASE_URL", ""), _env("TTS_API_KEY", ""), _env("TTS_MODEL", "tts-1")))
    tts_voice: str = field(default_factory=lambda: _env("TTS_VOICE", "alloy"))

    # --- channels ----------------------------------------------------------
    twilio_sid: str = field(default_factory=lambda: _env("TWILIO_ACCOUNT_SID", ""))
    twilio_token: str = field(default_factory=lambda: _env("TWILIO_AUTH_TOKEN", ""))
    twilio_from: str = field(default_factory=lambda: _env("TWILIO_FROM", ""))
    twilio_whatsapp_from: str = field(default_factory=lambda: _env("TWILIO_WHATSAPP_FROM", ""))
    telegram_token: str = field(default_factory=lambda: _env("TELEGRAM_BOT_TOKEN", ""))
    telegram_secret: str = field(default_factory=lambda: _env("TELEGRAM_WEBHOOK_SECRET", ""))
    smtp_host: str = field(default_factory=lambda: _env("SMTP_HOST", ""))
    smtp_port: int = field(default_factory=lambda: _int("SMTP_PORT", 587))
    smtp_user: str = field(default_factory=lambda: _env("SMTP_USER", ""))
    smtp_password: str = field(default_factory=lambda: _env("SMTP_PASSWORD", ""))
    smtp_from: str = field(default_factory=lambda: _env("SMTP_FROM", ""))
    email_inbound_secret: str = field(default_factory=lambda: _env("EMAIL_INBOUND_SECRET", ""))

    # --- deep lane ---------------------------------------------------------
    deep_max_lanes: int = field(default_factory=lambda: _int("DEEP_MAX_LANES", 4))
    workspace_root: str = ""

    def __post_init__(self) -> None:
        os.makedirs(self.data_dir, exist_ok=True)
        if not self.db_path:
            self.db_path = _env("DB_PATH", os.path.join(self.data_dir, "persona.db"))
        if not self.workspace_root:
            self.workspace_root = _env("WORKSPACE_ROOT", os.path.join(self.data_dir, "workspaces"))
        if not self.consult_base_url:
            self.consult_base_url = self.b.base_url
        if not self.consult_api_key:
            self.consult_api_key = self.b.api_key


load_dotenv()


def load_settings(**overrides) -> Settings:
    s = Settings()
    for k, v in overrides.items():
        setattr(s, k, v)
    s.__post_init__()
    return s
