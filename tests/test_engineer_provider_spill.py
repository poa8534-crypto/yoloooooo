"""A busy provider hands the system sideways instead of making it queue.

Five systems, three Antigravity slots and an idle backup used to mean two
systems waiting their turn for no reason but tidiness. The chain already has
a word for "ask someone else" -- unavailable -- so a full provider says it,
and the last provider in the chain still waits, because after it there is
nobody to ask.
"""

from __future__ import annotations

import asyncio

import pytest

from app.engineer.gemini import GeminiUnavailable
from app.engineer.runs import _held


async def answer(_system: str, _prompt: str, _deadline: float) -> tuple[str, str]:
    return "{}", "model"


@pytest.mark.anyio
async def test_a_full_provider_spills_to_the_next():
    slots = asyncio.Semaphore(1)
    call = _held(answer, slots, "antigravity", spill=True)
    async with slots:  # the one slot is taken
        with pytest.raises(GeminiUnavailable) as raised:
            await call("AnySystem", "prompt", 1e12)
    assert "busy" in str(raised.value)


@pytest.mark.anyio
async def test_a_free_slot_is_taken_as_before():
    call = _held(answer, asyncio.Semaphore(1), "antigravity", spill=True)
    text, _model = await call("AnySystem", "prompt", 1e12)
    assert text == "{}"


@pytest.mark.anyio
async def test_the_last_provider_waits_rather_than_dropping_the_work():
    """Nobody comes after it, so a refusal here loses the system."""
    slots = asyncio.Semaphore(1)
    call = _held(answer, slots, "ollama", spill=False)
    holder = asyncio.Event()

    async def hold() -> None:
        async with slots:
            holder.set()
            await asyncio.sleep(0.05)

    task = asyncio.create_task(hold())
    await holder.wait()
    text, _model = await call("AnySystem", "prompt", 1e12)  # waits, does not raise
    assert text == "{}"
    await task


def test_antigravity_has_a_measured_limit_and_the_build_runs_wider_than_it():
    """Three is Antigravity's measured limit; the systems beyond it are meant
    to land on a backup, which only happens if the build runs more at once."""
    from app.config import Settings
    from app.engineer.runs import provider_limits

    settings = Settings(_env_file=None)
    assert provider_limits(settings)["antigravity"] == 3
    assert settings.engineer_parallel_systems > 3
