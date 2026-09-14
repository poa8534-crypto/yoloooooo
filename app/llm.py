from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

import httpx
from pydantic import ValidationError

from .config import Settings, get_settings
from .schemas import ProposalPayload


class LLMUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class GeneratedProposal:
    payload: ProposalPayload
    model: str


class OllamaProposalClient:
    _gpu_gate = asyncio.Semaphore(1)

    def __init__(self, settings: Settings | None = None, client: httpx.AsyncClient | None = None):
        self.settings = settings or get_settings()
        self.client = client or httpx.AsyncClient(timeout=self.settings.ollama_timeout_seconds)
        self._owns_client = client is None

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def generate(
        self,
        *,
        agent: str,
        niche: str,
        sourced_name: str,
        fact_ids: list[str],
    ) -> GeneratedProposal:
        prompt = (
            f"Agent role: {agent}. Design a Roblox concept for the niche '{niche}'. "
            f"A sourced experience label available to the orchestrator is '{sourced_name}'. "
            f"You may reference only these opaque fact IDs: {fact_ids}. "
            "Return a creative proposal, not factual claims. Never include URLs, statistics, "
            "percentages, dates, player counts, view counts, revenue, success odds, or verdicts. "
            "Assume a solo beginner and a small three-day MVP."
        )
        errors: list[str] = []
        async with self._gpu_gate:
            for model in (self.settings.ollama_primary_model, self.settings.ollama_fallback_model):
                for _attempt in range(2):
                    try:
                        response = await self.client.post(
                            f"{self.settings.ollama_base_url}/api/chat",
                            json={
                                "model": model,
                                "stream": False,
                                "think": False,
                                "format": ProposalPayload.model_json_schema(),
                                "options": {
                                    "num_ctx": self.settings.ollama_context,
                                    "temperature": 0.2,
                                },
                                "messages": [
                                    {
                                        "role": "system",
                                        "content": "Return only schema-valid proposal JSON. Evidence and decisions belong to deterministic code.",
                                    },
                                    {"role": "user", "content": prompt},
                                ],
                            },
                        )
                        response.raise_for_status()
                        content = response.json()["message"]["content"]
                        payload = ProposalPayload.model_validate_json(content)
                        if not set(payload.supporting_fact_ids).issubset(set(fact_ids)):
                            raise ValueError("model returned an unknown evidence ID")
                        return GeneratedProposal(payload, model)
                    except (httpx.HTTPError, KeyError, json.JSONDecodeError, ValidationError, ValueError) as exc:
                        errors.append(f"{model}: {exc}")
        raise LLMUnavailable("proposal generation failed closed: " + " | ".join(errors[-4:]))
