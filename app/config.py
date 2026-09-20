from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal
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
    # Roblox Engineer (app/engineer/). Its Gemini settings are its own: it calls
    # the native generateContent API rather than the OpenAI-compatible surface
    # the Scout uses above, and one answer can be a whole system, so it waits
    # longer. Every name here is prefixed so it cannot shadow a Scout setting.
    #
    # Key 2 is Hermes's, and both keys were measured to share one project
    # quota: a second key adds bookkeeping, not capacity. So the engineer uses
    # key 1 unless told to use both.
    gemini_api_key_2: str = ""
    engineer_use_both_gemini_keys: bool = False
    engineer_gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    # Tried in order for every attempt: Pro for the heavy work, then Flash.
    # Pro answered 429 RESOURCE_EXHAUSTED on both keys when measured, so a
    # rate-limited model hands the attempt on instead of waiting it out, and is
    # tried again on the next attempt. Pinned names, never a moving alias.
    engineer_gemini_models: str = "gemini-3.1-pro-preview,gemini-3.8-flash"
    # Which backend writes the code. "ollama" runs the model already on this
    # machine: no quota, no rate limit, and the five checks judge its output
    # exactly as they judge Gemini's, so a cheaper model costs attempts rather
    # than correctness. Gemini's free tier is 20 requests for Flash and zero
    # for Pro, which blocked two runs before a single file was written.
    # Tried in order, first that answers wins. A provider that is not installed
    # or not configured is skipped, so the chain is also how a machine without
    # `agy` keeps working. Only "unavailable" moves down the chain: a refusal
    # and a truncated answer belong to the attempt that got them.
    engineer_providers: str = "antigravity,glm,deepseek,gemini,ollama"
    # The single-provider setting the chain replaced. Still read, so an .env
    # that sets it keeps working: it becomes the whole chain.
    engineer_provider: Literal["gemini", "ollama", "antigravity", "glm", "deepseek"] | None = None
    engineer_antigravity_executable: str = "agy"
    # Tried in order. Flash 3.8 throughout, at descending effort: a rate-limited
    # high is a reason to think less about the same problem, not to change model
    # underneath a run. `agy models` names the effort inside the model, so these
    # are three efforts of one model rather than three models.
    engineer_antigravity_models: str = "gemini-3.8-flash-high,gemini-3.8-flash-medium"
    engineer_antigravity_effort: Literal["low", "medium", "high"] = "medium"
    engineer_antigravity_timeout_seconds: float = Field(default=900.0, gt=0)
    engineer_ollama_base_url: str = "http://127.0.0.1:11434"
    # Tried in order. `venture-coder:14b` is reserved for the fine-tune and is
    # skipped until it exists; `roblox-engineer:14b` is stock qwen2.5-coder
    # repackaged with a Roblox system prompt, which is what it was before the
    # name was corrected -- it had never been trained, and calling it
    # venture-coder made every attempt it produced look like tuned output.
    engineer_ollama_models: str = "venture-coder:14b,roblox-engineer:14b,qwen2.5-coder:14b"
    engineer_ollama_context: int = Field(default=16_384, ge=2048)
    # Sent with every request so an installed Modelfile cannot change how the
    # engineer generates. 1.0 is no repetition penalty, which is what code
    # wants: see app/engineer/ollama.py.
    engineer_ollama_repeat_penalty: float = Field(default=1.0, ge=1.0, le=2.0)
    engineer_ollama_top_p: float = Field(default=0.9, gt=0.0, le=1.0)
    engineer_ollama_timeout_seconds: float = Field(default=900.0, gt=0)
    engineer_gemini_timeout_seconds: float = Field(default=900.0, gt=0)
    engineer_gemini_max_output_tokens: int = Field(default=65_536, ge=1024)
    # Providers that speak the OpenAI chat-completions API, used as backups
    # when Antigravity is out of quota (app/engineer/openai_compat.py). Each is
    # skipped until its key is set. The owner types the keys into .env; they
    # are never asked for, printed or committed. Model names are tried in
    # order, so a provider's exact model id can be corrected here without code.
    glm_api_key: str = ""
    glm_base_url: str = "https://api.z.ai/api/paas/v4"
    engineer_glm_models: str = "glm-5.2"
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    engineer_deepseek_models: str = "deepseek-chat"
    engineer_compat_timeout_seconds: float = Field(default=900.0, gt=0)
    # 8192 because DeepSeek's chat model refuses a larger max_tokens outright.
    engineer_compat_max_output_tokens: int = Field(default=8192, ge=1024)
    # The game repository the engineer writes into. Unset means the engineer
    # refuses to run rather than guessing a path.
    # What the person's plan allows, in model calls. Nothing is assumed: no
    # provider reports a remaining quota, so with these unset the dashboard
    # shows what was spent and says no limit is set, rather than drawing a bar
    # against a number this process made up.
    usage_hourly_limit: int = 0
    usage_weekly_limit: int = 0
    game_project_dir: Path | None = None
    game_base_branch: str = "master"
    # Worktrees live outside both repositories: one per run, removed after it.
    engineer_worktree_dir: Path | None = None
    engineer_data_dir: Path = ROOT / "data" / "engineer"
    # Locks for a ledger that is not a SQLite file (app/db.py lock_home). A
    # SQLite ledger keeps its locks beside the database instead.
    lock_dir: Path = ROOT / "data" / "locks"
    # Every attempt kept as training material (app/engineer/capture.py). On by
    # default: a refused attempt is the only example that is specific to this
    # model, this toolchain and these rules, and five of every six are otherwise
    # thrown away when the run ends.
    engineer_capture_attempts: bool = True
    engineer_capture_dir: Path = ROOT / "data" / "training" / "attempts"
    # Written by scripts/refresh_roblox_services.py; the gate reads the same file.
    roblox_services_file: Path = ROOT / "data" / "roblox-services.json"
    # "verify-script" runs the game repo's scripts/verify.ps1, the one definition
    # of acceptable (docs/GATING.md). "builtin" runs the same checks command by
    # command, for machines without that script; it is not the default because
    # two definitions drift.
    engineer_gate: Literal["verify-script", "builtin"] = "verify-script"
    # How many systems the Engineer writes at once. A system still never starts
    # before everything it depends on has landed (app/engineer/schedule.py);
    # this only lets systems that do not depend on each other overlap. Three
    # because three is what was measured: three `agy` calls at once all
    # answered, in 34 s of wall time against 17 s for one alone. On ascent's
    # own timings three would take a build from 82 minutes to about 34.
    engineer_parallel_systems: int = Field(default=3, ge=1, le=16)
    # Per provider, how many calls may be in flight at once across the whole
    # build, as name=count pairs ("ollama=1,gemini=2"). A provider not named is
    # limited only by engineer_parallel_systems. A call waits for its provider
    # rather than skipping to the next one: which model writes a system is not
    # something to change because the first choice was busy. Ollama runs on
    # this machine's one GPU, where three at once would each go a third as fast.
    engineer_provider_concurrency: str = "ollama=1"
    engineer_max_attempts: int = Field(default=6, ge=1, le=20)
    engineer_run_seconds: float = Field(default=5400.0, gt=0)
    engineer_tool_timeout_seconds: float = Field(default=300.0, gt=0)
    luau_definitions_file: str = "globalTypes.None.d.luau"

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.artifact_dir.mkdir(parents=True, exist_ok=True)
    settings.model_dir.mkdir(parents=True, exist_ok=True)
    return settings
