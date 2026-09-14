"""The labeled dataset, and how it is split.

Two sources feed it: a hand-authored seed file that covers the adversarial
shapes the engine has to survive, and the operator reviews recorded through the
Matching Review page. Reviews are the real labels; the seed exists so the
benchmark is runnable before any reviews have accumulated.

Splitting is grouped by cluster *and* ordered by discovery time. A sequel, its
base game and every video about either one live in the same cluster, so no
closely related example can land on both sides of a split.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    AssociationRecord,
    AssociationReview,
    MatchCandidate,
    MatchSubject,
    ReviewVerdict,
)
from .subjects import MatchCandidateView, MatchSubjectView

SEED_PATH = Path(__file__).resolve().parent / "data" / "seed_dataset.json"

SPLIT_TRAIN = "train"
SPLIT_DEV = "dev"
SPLIT_TEST = "test"


@dataclass(frozen=True)
class LabeledExample:
    example_id: str
    cluster: str
    niche: str
    kind: str
    subject: MatchSubjectView
    pool: tuple[MatchCandidateView, ...]
    label_candidate_id: str | None
    discovered_at: datetime
    origin: str = "seed"

    @property
    def is_positive(self) -> bool:
        return self.label_candidate_id is not None

    def canonical(self) -> dict:
        return {
            "id": self.example_id,
            "cluster": self.cluster,
            "kind": self.kind,
            "origin": self.origin,
            "title": self.subject.raw_title,
            "description": self.subject.raw_description,
            "creator": self.subject.creator_name,
            "label": self.label_candidate_id,
            "pool": sorted(candidate.candidate_id for candidate in self.pool),
        }


@dataclass(frozen=True)
class Dataset:
    examples: tuple[LabeledExample, ...] = ()
    splits: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def of(self, split: str) -> list[LabeledExample]:
        wanted = set(self.splits.get(split, ()))
        return [example for example in self.examples if example.example_id in wanted]

    def hash(self) -> str:
        payload = json.dumps(
            {
                "examples": [example.canonical() for example in self.examples],
                "splits": {key: list(value) for key, value in sorted(self.splits.items())},
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def counts(self) -> dict[str, int]:
        return {split: len(self.of(split)) for split in (SPLIT_TRAIN, SPLIT_DEV, SPLIT_TEST)}


def _month_start(month: str) -> datetime:
    year, month_number = month.split("-")
    return datetime(int(year), int(month_number), 1, tzinfo=UTC)


def load_seed(path: Path | None = None) -> list[LabeledExample]:
    raw = json.loads((path or SEED_PATH).read_text(encoding="utf-8"))
    clusters = {item["cluster"]: item for item in raw["clusters"]}
    by_cluster: dict[str, list[MatchCandidateView]] = {}
    for entry in raw["candidates"]:
        by_cluster.setdefault(entry["cluster"], []).append(MatchCandidateView(
            candidate_id=entry["candidate_id"],
            universe_id=entry.get("universe_id", ""),
            place_ids=tuple(entry.get("place_ids", ())),
            raw_name=entry["name"],
            raw_description=entry.get("description", ""),
            aliases=tuple(entry.get("aliases", ())),
            creator_name=entry.get("creator_name", ""),
        ))

    examples: list[LabeledExample] = []
    for index, entry in enumerate(raw["examples"]):
        cluster = entry["cluster"]
        meta = clusters[cluster]
        discovered = _month_start(meta["month"])
        subject = MatchSubjectView(
            subject_id=f"seed:{entry['id']}",
            subject_type="youtube_video",
            external_id=f"seed{index:04d}",
            raw_title=entry["title"],
            raw_description=entry.get("description", ""),
            raw_url=f"https://www.youtube.com/watch?v=seed{index:04d}",
            creator_name=entry.get("creator", ""),
            niche=meta["niche"],
            source_artifact_sha256=hashlib.sha256(
                json.dumps(entry, sort_keys=True).encode("utf-8")
            ).hexdigest(),
            extraction_method="youtube_videos_api",
            source_tier="primary",
            pointer_prefix=f"/items/{index}",
            discovered_at=discovered,
        )
        examples.append(LabeledExample(
            example_id=entry["id"],
            cluster=cluster,
            niche=meta["niche"],
            kind=entry.get("kind", "unspecified"),
            subject=subject,
            pool=tuple(by_cluster.get(cluster, ())),
            label_candidate_id=entry.get("label"),
            discovered_at=discovered,
        ))
    return examples


def load_review_labels(db: Session) -> list[LabeledExample]:
    """Convert recorded operator reviews into labeled examples.

    An approval or reassignment labels the chosen candidate; a rejection labels
    the example as having no correct candidate. The cluster is the canonical
    candidate the engine proposed, so a record and its competitors stay
    together when the dataset is split.
    """
    examples: list[LabeledExample] = []
    reviews = list(db.scalars(
        select(AssociationReview).order_by(AssociationReview.created_at, AssociationReview.id)
    ))
    seen: set[str] = set()
    for review in reversed(reviews):  # newest review per record wins
        if review.association_id in seen:
            continue
        seen.add(review.association_id)
        record = db.get(AssociationRecord, review.association_id)
        if record is None:
            continue
        subject_row = db.get(MatchSubject, record.subject_id)
        if subject_row is None:
            continue
        pool_ids = [
            item.get("candidate_id") for item in (record.candidate_scoreboard or [])
            if item.get("candidate_id")
        ]
        pool = [
            _candidate_view(row) for candidate_id in pool_ids
            if (row := db.get(MatchCandidate, candidate_id)) is not None
        ]
        if not pool:
            continue
        label = (
            None if review.verdict == ReviewVerdict.REJECTED.value
            else (review.selected_candidate_id or record.candidate_id)
        )
        cluster = f"review:{record.candidate_id or record.subject_id}"
        examples.append(LabeledExample(
            example_id=f"review:{review.id}",
            cluster=cluster,
            niche=subject_row.niche or "",
            kind=f"review_{review.verdict}",
            subject=_subject_view(subject_row),
            pool=tuple(pool),
            label_candidate_id=label,
            discovered_at=_aware(subject_row.discovered_at or record.created_at),
            origin="review",
        ))
    return examples


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _candidate_view(row: MatchCandidate) -> MatchCandidateView:
    from .service import candidate_view_from_row

    return candidate_view_from_row(row)


def _subject_view(row: MatchSubject) -> MatchSubjectView:
    return MatchSubjectView(
        subject_id=row.id,
        subject_type=row.subject_type,
        external_id=row.external_id or "",
        raw_title=row.raw_title or "",
        raw_description=row.raw_description or "",
        raw_url=row.raw_url or "",
        creator_name=row.creator_name or "",
        creator_external_id=row.creator_external_id or "",
        niche=row.niche or "",
        source_artifact_id=row.source_artifact_id or "",
        source_artifact_sha256=row.source_artifact_sha256 or "",
        extraction_method=row.extraction_method or "",
        source_tier=row.source_tier or "",
        pointer_prefix=row.pointer_prefix or "",
        discovered_at=_aware(row.discovered_at),
    )


def split_by_cluster_and_time(
    examples: list[LabeledExample],
    *,
    train_fraction: float = 0.50,
    dev_fraction: float = 0.25,
) -> dict[str, tuple[str, ...]]:
    """Group by cluster, order clusters by first discovery, cut by time.

    A whole cluster always lands in exactly one split, so a sequel cannot leak
    into the held-out set through its base game, and a later discovery never
    trains a model that is then tested on an earlier one.
    """
    by_cluster: dict[str, list[LabeledExample]] = {}
    for example in examples:
        by_cluster.setdefault(example.cluster, []).append(example)
    ordered = sorted(
        by_cluster.items(),
        key=lambda item: (min(example.discovered_at for example in item[1]), item[0]),
    )
    total = len(examples)
    train_limit = total * train_fraction
    dev_limit = total * (train_fraction + dev_fraction)

    splits: dict[str, list[str]] = {SPLIT_TRAIN: [], SPLIT_DEV: [], SPLIT_TEST: []}
    seen = 0
    for _cluster, group in ordered:
        if seen < train_limit:
            target = SPLIT_TRAIN
        elif seen < dev_limit:
            target = SPLIT_DEV
        else:
            target = SPLIT_TEST
        splits[target].extend(
            example.example_id for example in sorted(group, key=lambda item: item.example_id)
        )
        seen += len(group)
    return {key: tuple(value) for key, value in splits.items()}


def build_dataset(db: Session | None = None, *, seed_path: Path | None = None) -> Dataset:
    """Seed examples plus any recorded operator reviews, split and hashed."""
    examples = load_seed(seed_path)
    if db is not None:
        examples.extend(load_review_labels(db))
    examples.sort(key=lambda item: (item.discovered_at, item.example_id))
    return Dataset(examples=tuple(examples), splits=split_by_cluster_and_time(examples))
