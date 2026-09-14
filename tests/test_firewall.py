from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas import ProposalPayload


def valid_payload(**updates):
    payload = {
        "concept_title": "Clockwork Orchard",
        "core_loop": "Plant unusual seeds, combine traits, and reveal surprising harvests.",
        "differentiator": "The proposal emphasizes visible transformations and cooperative discovery.",
        "build_steps": ["Create one planting interaction", "Add a compact harvest loop"],
        "risks": ["The loop may become repetitive without varied outcomes"],
        "questions": ["Which interaction should receive the earliest playtest?"],
        "supporting_fact_ids": ["00000000-0000-0000-0000-000000000001"],
    }
    payload.update(updates)
    return payload


@pytest.mark.parametrize("text", [
    "See https://fake.example/source for proof.",
    "This has 99% success odds.",
    "The game has 12000 players.",
    "Revenue will reach 2m views.",
])
def test_proposal_rejects_urls_and_metric_claims(text):
    with pytest.raises(ValidationError):
        ProposalPayload.model_validate(valid_payload(core_loop=text))


def test_proposal_schema_rejects_decision_and_score_fields():
    with pytest.raises(ValidationError):
        ProposalPayload.model_validate(valid_payload(score=98, verdict="recommend"))


def test_creative_nonfactual_proposal_is_accepted():
    proposal = ProposalPayload.model_validate(valid_payload())
    assert proposal.concept_title == "Clockwork Orchard"
