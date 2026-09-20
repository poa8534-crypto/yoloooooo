"""Ask every configured backup provider one small question, and report what it said.

This exists so a key that was just typed into .env can be checked before a
build depends on it, and so the answer is measured rather than assumed: each
provider is asked for a real completion through the same client the Engineer
uses, and what is printed is that provider's own reply or its own error.

It reads keys only from .env through Settings, and it never prints a key or
any fragment of one -- every error goes through the client's own scrub.

    python scripts/check_providers.py            # every provider in the chain
    python scripts/check_providers.py dahl glm   # only those named
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import Settings  # noqa: E402
from app.engineer.gemini import GeminiIncomplete, GeminiRefused, GeminiUnavailable  # noqa: E402
from app.engineer.openai_compat import OpenAICompatClient  # noqa: E402
from app.engineer.runs import (  # noqa: E402
    COMPAT_PROVIDERS,
    NotConfigured,
    build_one_client,
    compat_model_names,
    provider_names,
)

QUESTION = 'Answer with this exact JSON and nothing else: {"ok": true}'


async def check_compat(name: str, settings: Settings) -> int:
    """Every model the provider lists, so a wrong model id is told apart from
    a wrong key: a key that works answers on at least one of them."""
    try:
        client: OpenAICompatClient = build_one_client(name, settings)
    except NotConfigured as exc:
        print(f"{name}: skipped -- {exc}")
        return 0

    failures = 0
    try:
        print(f"{name}: {client.base_url}")
        for model in compat_model_names(name, settings):
            try:
                reply = await client.generate(model=model, system="You are a test.",
                                              prompt=QUESTION, json_output=False)
            except (GeminiUnavailable, GeminiRefused, GeminiIncomplete) as exc:
                failures += 1
                print(f"  {model}: FAILED -- {type(exc).__name__}: {exc}")
            else:
                answer = " ".join(reply.text.split())[:80]
                spent = reply.usage.get("output_tokens", 0)
                print(f"  {model}: answered ({spent} output tokens): {answer}")
    finally:
        await client.close()
    return failures


async def main(argv: list[str]) -> int:
    settings = Settings()
    wanted = [name.lower() for name in argv[1:]]
    chain = provider_names(settings)
    names = [name for name in chain if name in COMPAT_PROVIDERS and (not wanted or name in wanted)]
    unknown = [name for name in wanted if name not in COMPAT_PROVIDERS]
    if unknown:
        print(f"not an OpenAI-compatible provider: {', '.join(unknown)}")
        return 2
    if not names:
        print(f"the chain names no compatible provider to check: {', '.join(chain)}")
        return 1

    failures = 0
    for name in names:
        failures += await check_compat(name, settings)
        print()
    if failures:
        print(f"{failures} model(s) did not answer. A 401 is the key; a 404 is the model id.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv)))
