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
