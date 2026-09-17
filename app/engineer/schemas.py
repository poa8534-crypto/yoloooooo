"""What the engineer is asked to build, and the only shape of answer it accepts."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EngineeringTask(_Strict):
    """One system to build, tied to the Venture Scout audit it serves.

    `audit_id` is required and must name a stored audit that produced a design:
    the engineer builds Venture Scout ideas and nothing else.
    """

    audit_id: str = Field(min_length=1, max_length=64)
    system: str = Field(pattern=r"^[A-Z][A-Za-z0-9]{2,48}$", description="e.g. DataService")
    goal: str = Field(min_length=10, max_length=4000)
    acceptance_criteria: list[str] = Field(min_length=1, max_length=20)
    notes: list[str] = Field(default_factory=list, max_length=20)


class GeneratedFile(_Strict):
    path: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=200_000)


class EngineerOutput(_Strict):
    files: list[GeneratedFile] = Field(min_length=1, max_length=16)
    services: list[str] = Field(default_factory=list, max_length=60)
    summary: str = Field(default="", max_length=4000)
