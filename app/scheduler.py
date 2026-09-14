from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from .config import get_settings
from .connectors import ConnectorError, Connectors
from .db import SessionLocal
from .evidence import add_json_observation, create_fact, record_artifact
from .models import Candidate, SystemState, TrackedVideo


async def snapshot_all(connectors: Connectors | None = None) -> dict[str, int]:
    own = connectors is None
    connectors = connectors or Connectors()
    counts = {"roblox": 0, "youtube": 0, "errors": 0}
    try:
        with SessionLocal() as db:
            candidates = list(db.scalars(select(Candidate)))
            videos = list(db.scalars(select(TrackedVideo)))
        by_universe = {candidate.external_id: candidate.id for candidate in candidates}
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
        by_video = {video.video_id: video.candidate_id for video in videos}
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
                        candidate_id = by_video.get(str(item.get("id")))
                        if not candidate_id:
                            continue
                        try:
                            obs = add_json_observation(
                                db, artifact=artifact, candidate_id=candidate_id,
                                metric="youtube_views", pointer=f"/items/{index}/statistics/viewCount", unit="views",
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
    scheduler.start()
    return scheduler


async def catch_up_if_needed() -> None:
    settings = get_settings()
    local_now = datetime.now(settings.tz)
    with SessionLocal() as db:
        state = db.get(SystemState, "last_snapshot")
        last = None
        if state and state.value_json.get("completed_at"):
            last = datetime.fromisoformat(state.value_json["completed_at"]).astimezone(settings.tz)
    scheduled_has_passed = (local_now.hour, local_now.minute) >= (settings.snapshot_hour, settings.snapshot_minute)
    if scheduled_has_passed and (last is None or last.date() < local_now.date()):
        asyncio.create_task(snapshot_all())

