"""Conservative local daily reservations shared by research and snapshots."""
from datetime import UTC, datetime

from sqlalchemy import text

from .config import get_settings
from .models import SystemState


class QuotaExceeded(RuntimeError):
    pass


class QuotaMeter:
    def __init__(self, factory):
        self.factory = factory

    def reserve(self, provider, cost):
        settings = get_settings()
        limit = settings.tavily_daily_allowance if provider == "tavily" else settings.youtube_daily_allowance
        key = f"quota:{provider}:{datetime.now(UTC).date()}"
        with self.factory() as db:
            # Atomic read/modify/write even if multiple local workers start.
            db.execute(text("BEGIN IMMEDIATE"))
            row = db.get(SystemState, key)
            used = row.value_json["reserved"] if row else 0
            if used + cost > limit:
                raise QuotaExceeded(provider + " configured daily allowance exhausted")
            if row is None:
                row = SystemState(key=key)
                db.add(row)
            row.value_json = {"reserved": used + cost, "allowance": limit, "remote_remaining": "unknown"}
            db.commit()
