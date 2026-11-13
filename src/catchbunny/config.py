"""Process-wide settings. Every knob is an env var so the same build runs on a
laptop, in CI and on a server without code changes."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit


def _env(name: str, default: str) -> str:
    return os.environ.get(f"CATCHBUNNY_{name}", default)


def _ollama_host() -> str:
    """CATCHBUNNY_OLLAMA_HOST, else Ollama's own OLLAMA_HOST (which may omit the scheme and port), else localhost."""
    raw = _env("OLLAMA_HOST", "") or os.environ.get("OLLAMA_HOST", "") or "127.0.0.1:11434"
    url = urlsplit(raw if "://" in raw else f"http://{raw}")
    host = "127.0.0.1" if url.hostname in (None, "", "0.0.0.0") else url.hostname  # a bind address is not a destination
    return f"{url.scheme}://{host}:{url.port or 11434}"


@dataclass(slots=True)
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(_env("DATA_DIR", ".catchbunny")))

    # ---- browser fleet
    browser: str = field(default_factory=lambda: _env("BROWSER", "chromium"))  # the engine a run uses unless it names one: chromium | firefox | webkit
    headless: bool = field(default_factory=lambda: _env("HEADLESS", "1") != "0")
    max_contexts: int = field(default_factory=lambda: int(_env("MAX_CONTEXTS", "12")))
    # Contexts share one browser process (cheap) but also its browser-process
    # main thread, so the pool spreads load over several processes past this.
    contexts_per_browser: int = field(default_factory=lambda: int(_env("CONTEXTS_PER_BROWSER", "6")))
    action_timeout_ms: int = field(default_factory=lambda: int(_env("ACTION_TIMEOUT_MS", "2500")))
    nav_timeout_ms: int = field(default_factory=lambda: int(_env("NAV_TIMEOUT_MS", "15000")))
    settle_quiet_ms: int = field(default_factory=lambda: int(_env("SETTLE_QUIET_MS", "40")))  # DOM must be still this long
    settle_max_ms: int = field(default_factory=lambda: int(_env("SETTLE_MAX_MS", "3000")))
    max_states_per_template: int = field(default_factory=lambda: int(_env("MAX_STATES_PER_TEMPLATE", "8")))

    # ---- scheduling
    max_concurrent_runs: int = field(default_factory=lambda: int(_env("MAX_CONCURRENT_RUNS", "4")))
    minimize_max_replays: int = field(default_factory=lambda: int(_env("MINIMIZE_MAX_REPLAYS", "96")))
    learned_ttl_days: float = field(default_factory=lambda: float(_env("LEARNED_TTL_DAYS", "14")))

    # ---- model
    model: str = field(default_factory=lambda: _env("MODEL", "claude-opus-5-5"))
    planner_model: str = field(default_factory=lambda: _env("PLANNER_MODEL", "") or _env("MODEL", "claude-opus-5-5"))
    effort: str = field(default_factory=lambda: _env("EFFORT", "low"))  # per-step tester turns
    planner_effort: str = field(default_factory=lambda: _env("PLANNER_EFFORT", "medium"))
    llm_concurrency: int = field(default_factory=lambda: int(_env("LLM_CONCURRENCY", "8")))
    llm_fallbacks: bool = field(default_factory=lambda: _env("LLM_FALLBACKS", "1") != "0")

    # ---- local model (Ollama)
    ollama_host: str = field(default_factory=_ollama_host)
    ollama_model: str = field(default_factory=lambda: _env("OLLAMA_MODEL", ""))  # e.g. qwen3:8b; a run may override it
    ollama_num_ctx: int = field(default_factory=lambda: int(_env("OLLAMA_NUM_CTX", "16384")))  # one value for every request: changing it reloads the model
    ollama_max_output: int = field(default_factory=lambda: int(_env("OLLAMA_MAX_OUTPUT", "2048")))
    ollama_temperature: float = field(default_factory=lambda: float(_env("OLLAMA_TEMPERATURE", "0.2")))
    ollama_think: str = field(default_factory=lambda: _env("OLLAMA_THINK", "off"))  # off | on | low | medium | high (thinking models only)
    ollama_keep_alive: str = field(default_factory=lambda: _env("OLLAMA_KEEP_ALIVE", "10m"))
    ollama_concurrency: int = field(default_factory=lambda: int(_env("OLLAMA_CONCURRENCY", "4")))  # the server queues past its own OLLAMA_NUM_PARALLEL
    ollama_timeout_s: float = field(default_factory=lambda: float(_env("OLLAMA_TIMEOUT_S", "600")))

    @property
    def db_path(self) -> Path:
        return self.data_dir / "catchbunny.db"

    @property
    def artifact_dir(self) -> Path:
        return self.data_dir / "artifacts"


def llm_credentials_present() -> bool:
    """True when the Anthropic SDK can resolve a credential without prompting."""
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return True
    return (Path.home() / ".config" / "anthropic").is_dir()
