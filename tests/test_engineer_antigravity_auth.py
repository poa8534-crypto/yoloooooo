"""A sign-in that failed is not the model declining the work.

While the owner swapped Antigravity accounts, six systems were asked for and
six came back "was refused: authentication failed or timed out". A refusal
stops the chain -- correctly, when a model declines a prompt. An
authentication failure says nothing about the prompt, so it cost six systems
all six attempts each, in about a second, and the backups were never asked.
"""

from __future__ import annotations

import json

import pytest

from app.engineer.antigravity import AntigravityClient
from app.engineer.gemini import GeminiRefused, GeminiUnavailable


def client_answering(error: str) -> AntigravityClient:
    envelope = {"event": "result",
                "result": {"status": "ERROR", "response": "", "error": error}}

    class Completed:
        returncode = 0
        stdout = json.dumps(envelope).encode()
        stderr = b""

    return AntigravityClient(executable="agy", timeout=30, effort="medium",
                             runner=lambda *args, **kwargs: Completed())


@pytest.mark.anyio
@pytest.mark.parametrize("error", [
    "authentication failed or timed out",
    "not authenticated: please sign in",
    "unauthorized",
    "connection refused",
    "credentials expired",
])
async def test_a_sign_in_problem_is_unavailable_so_the_chain_moves_on(error):
    with pytest.raises(GeminiUnavailable):
        await client_answering(error).generate(model="gemini-3.8-flash-high", system="s",
                                               prompt="p", deadline=None)


@pytest.mark.anyio
async def test_the_model_declining_is_still_a_refusal():
    """The distinction this rests on: a verdict about the request stops the
    chain, a fact about the tool does not."""
    with pytest.raises(GeminiRefused):
        await client_answering("I cannot help with that request").generate(
            model="gemini-3.8-flash-high", system="s", prompt="p", deadline=None)
