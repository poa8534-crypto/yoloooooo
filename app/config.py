from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_name: str = "Roblox Venture Agents"
    host: str = "127.0.0.1"
    port: int = 8742
    database_url: str = f"sqlite:///{(ROOT / 'data' / 'venture_agents.db').as_posix()}"
    artifact_dir: Path = ROOT / "data" / "artifacts"
    model_dir: Path = ROOT / "data" / "models"
    tavily_api_key: str = ""
    # A local SearxNG instance. No key and no daily allowance, so discovery is
    # not rationed by a third party. See docs/LOCAL_SEARCH.md.
    searxng_url: str = "http://127.0.0.1:8888"
    searxng_enabled: bool = True
    # Roblox's own search. First-party, no key, and it returns universe IDs
    # directly, so a resolution call per result is not needed.
    roblox_search_enabled: bool = True
    # Required before the service will bind anything but loopback. See
    # app/access.py; there is no other way to unlock a wider interface.
    dashboard_token: str = ""
    # Roblox's own front page, sampled on a schedule. Every rate-of-change
    # measurement in the system is a derivative, and a derivative needs two
    # observations of the same game; this is what produces the second one.
    roblox_charts_enabled: bool = True
    market_sample_minutes: int = Field(default=30, ge=5)
    # Public engines suspend an instance that queries them in bursts, so
    # searches are spaced and repeated questions are answered from the cache.
    search_min_interval_seconds: float = Field(default=4.0, ge=0)
    search_cache_seconds: float = Field(default=86_400, ge=0)
    youtube_api_key: str = ""
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_primary_model: str = "qwen3:14b"
    ollama_fallback_model: str = "qwen3:8b"
    ollama_context: int = 16_384
    # A Venture Scout audit reasons, drafts, critiques its own draft and
    # revises it. That is several minutes of local inference per pass, so the
    # per-request timeout is generous on purpose; the run budget, not this
    # value, is what stops a run from overspending.
    ollama_timeout_seconds: float = 600.0
    ollama_think: bool = True
    # Which backend answers a Scout audit. "ollama" keeps every prompt on this
    # machine; "gemini" sends it to Google. That is a change in where the
    # evidence goes, not only in which model reads it, so it is an explicit
    # setting rather than an inference from whether a key happens to be set.
    llm_provider: str = "ollama"
    # Google AI Studio, through its OpenAI-compatible surface, which is the
    # only one that accepts a JSON schema the way Ollama's `format` does.
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai"
    # Key 1. Key 2 belongs to Hermes, so a runaway loop in one cannot exhaust
    # the other -- though both keys were measured to share a single project
    # quota, so that separation is bookkeeping, not protection.
    gemini_api_key_1: str = ""
    # Flash, not Pro, and measured rather than assumed: `gemini-3.1-pro-preview`
    # answers 429 RESOURCE_EXHAUSTED on both keys today, while
    # `gemini-3.8-flash` answers normally and carries the same 1M context.
    gemini_primary_model: str = "gemini-3.8-flash"
    gemini_fallback_model: str = "gemini-3.6-flash"
    gemini_timeout_seconds: float = 180.0
    # OpenRouter, which is how Union Alpha is reachable. Kept separate from
    # Gemini because the operator is different in a way that matters: Union
    # Alpha is a stealth model whose provider is anonymous, so a prompt sent
    # there goes to a party nobody can name. Nothing carrying captured evidence
    # should default to it, which is why it is never in the default chain.
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_api_key: str = ""
    openrouter_model: str = "stealth/union-alpha"
    openrouter_timeout_seconds: float = 180.0
    scout_deliberation_passes: int = Field(default=3, ge=1, le=3)
    snapshot_hour: int = 2
    snapshot_minute: int = 0
    timezone: str = "Asia/Kolkata"
    max_search_results: int = Field(default=15, ge=1, le=50)
    tavily_daily_allowance: int = Field(default=50, ge=0)
    youtube_daily_allowance: int = Field(default=10000, ge=0)

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.artifact_dir.mkdir(parents=True, exist_ok=True)
    settings.model_dir.mkdir(parents=True, exist_ok=True)
    return settings
