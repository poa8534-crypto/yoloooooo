from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from .association.service import usable_association_for_subject
from .association.subjects import SUBJECT_YOUTUBE_VIDEO
from .config import get_settings
from .connectors import ConnectorError, Connectors
from .db import SessionLocal
from .evidence import add_json_observation, create_fact, record_artifact
from .models import Candidate, SystemState, TrackedVideo
from .market_pulse import sample_market


async def snapshot_all(connectors: Connectors | None = None) -> dict[str, int]:
    own = connectors is None
    from .quotas import QuotaMeter
    connectors = connectors or Connectors(quota_meter=QuotaMeter(SessionLocal))
    counts = {"roblox": 0, "youtube": 0, "errors": 0}
    try:
        with SessionLocal() as db:
            candidates = list(db.scalars(select(Candidate)))
            videos = list(db.scalars(select(TrackedVideo)))
        # Stable representative; readers join all same-universe candidate IDs.
        by_universe = {}
        for candidate in sorted(candidates, key=lambda c: (c.created_at, c.id)):
            by_universe.setdefault(candidate.external_id, candidate.id)
        ids = list(by_universe)
        for start in range(0, len(ids), 50):
            try:
                result = await connectors.roblox_games(ids[start:start + 50])
                with SessionLocal() as db:
                    artifact = record_artifact(
                        db, url=result.url, retrieval_method="scheduled_roblox_snapshot",
                        content_type=result.content_type, payload=result.payload,
                        source_tier="primary", owner="roblox.com",
                    )
                    for index, item in enumerate(result.payload.get("data", [])):
                        candidate_id = by_universe.get(str(item.get("id")))
                        if not candidate_id:
                            continue
                        for field, metric, template, unit in (
                            ("playing", "roblox_playing", "roblox_playing", "players"),
                            ("visits", "roblox_visits", "roblox_visits", "visits"),
                            ("favoritedCount", "roblox_favorites", "roblox_favorites", "favorites"),
                        ):
                            try:
                                obs = add_json_observation(
                                    db, artifact=artifact, candidate_id=candidate_id,
                                    metric=metric, pointer=f"/data/{index}/{field}", unit=unit,
                                )
                                create_fact(db, template_id=template, slots={"value": obs})
                            except (KeyError, IndexError, ValueError):
                                continue
                        counts["roblox"] += 1
                    db.commit()
            except ConnectorError:
                counts["errors"] += 1
        # A repeat measurement is only admissible through the same approved
        # association that let the video in. A video whose association was
        # never approved, or was later rejected, stops being measured; it is
        # not silently attributed to the candidate it was once tracked under.
        by_video: dict[str, tuple[str, str]] = {}
        with SessionLocal() as db:
            for video in videos:
                resolved = usable_association_for_subject(
                    db, SUBJECT_YOUTUBE_VIDEO, video.video_id
                )
                if resolved is None:
                    continue
                record, candidate_row_id = resolved
                by_video[video.video_id] = (candidate_row_id, record.id)
        video_ids = list(by_video)
        for start in range(0, len(video_ids), 50):
            try:
                result = await connectors.youtube_videos(video_ids[start:start + 50])
                with SessionLocal() as db:
                    artifact = record_artifact(
                        db, url=result.url, retrieval_method="scheduled_youtube_snapshot",
                        content_type=result.content_type, payload=result.payload,
                        source_tier="primary", owner="googleapis.com",
                    )
                    for index, item in enumerate(result.payload.get("items", [])):
                        resolved = by_video.get(str(item.get("id")))
                        if resolved is None:
                            continue
                        candidate_id, association_id = resolved
                        try:
                            obs = add_json_observation(
                                db, artifact=artifact, candidate_id=candidate_id,
                                metric="youtube_views", pointer=f"/items/{index}/statistics/viewCount", unit="views",
                                association_id=association_id,
                            )
                            create_fact(db, template_id="youtube_views", slots={"value": obs})
                            counts["youtube"] += 1
                        except (KeyError, IndexError, ValueError):
                            continue
                    db.commit()
            except ConnectorError:
                counts["errors"] += 1
        with SessionLocal() as db:
            state = db.get(SystemState, "last_snapshot")
            value = {"completed_at": datetime.now(UTC).isoformat(), "counts": counts}
            if state:
                state.value_json = value
                state.updated_at = datetime.now(UTC)
            else:
                db.add(SystemState(key="last_snapshot", value_json=value))
            db.commit()
        return counts
    finally:
        if own:
            await connectors.close()


def start_scheduler() -> AsyncIOScheduler:
    settings = get_settings()
    scheduler = AsyncIOScheduler(timezone=settings.tz)
    scheduler.add_job(
        snapshot_all,
        trigger="cron",
        hour=settings.snapshot_hour,
        minute=settings.snapshot_minute,
        id="daily_metric_snapshot",
        replace_existing=True,
        coalesce=True,
        misfire_grace_time=86_400,
    )
    if settings.roblox_charts_enabled:
        # Far more often than the daily snapshot: a shelf called Up-and-Coming
        # turns over within a day, so sampling it daily would record the
        # survivors and miss the rise that made them interesting.
        scheduler.add_job(
            sample_market,
            trigger="interval",
            minutes=settings.market_sample_minutes,
            id="market_pulse_sample",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
            misfire_grace_time=settings.market_sample_minutes * 60,
        )
    scheduler.start()
    return scheduler


def snapshot_is_due(local_now: datetime, last: datetime | None, hour: int, minute: int) -> bool:
    """Catch the latest scheduled slot, including a restart before today's slot."""
    due = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if local_now < due:
        due -= timedelta(days=1)
    return last is None or last < due


async def catch_up_if_needed() -> None:
    settings = get_settings()
    local_now = datetime.now(settings.tz)
    with SessionLocal() as db:
        state = db.get(SystemState, "last_snapshot")
        last = None
        if state and state.value_json.get("completed_at"):
            last = datetime.fromisoformat(state.value_json["completed_at"]).astimezone(settings.tz)
    if snapshot_is_due(local_now, last, settings.snapshot_hour, settings.snapshot_minute):
        asyncio.create_task(snapshot_all())

