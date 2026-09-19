"""Where blueprints live between requests.

`system_state` is the project's existing document store -- audit jobs
(app/audit_jobs.py) and engineer runs (app/engineer/runs.py) both keep their
records there under a key prefix. A blueprint is the same shape of thing: one
document, read and written whole, belonging to one idea. So it goes in the same
place rather than in six new tables, and no migration is needed.

Every write bumps `revision` and keeps the previous version. A build is traced
to the blueprint revision it was compiled from, and "what did I actually
approve?" has to stay answerable after the blueprint has moved on.
"""

from __future__ import annotations

import json
import logging
import secrets
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

from ..models import SystemState
from .schemas import Blueprint, BuildStatus
from .transitions import check

PREFIX = "blueprint:"
HISTORY_PREFIX = "blueprint-revision:"
MAX_HISTORY = 50


class BlueprintNotFound(KeyError):
    pass


def new_id() -> str:
    return secrets.token_hex(8)


def _key(blueprint_id: str) -> str:
    return PREFIX + blueprint_id


class BlueprintStore:
    def __init__(self, factory):
        self.factory = factory

    def create(self, *, audit_id: str, title: str, project_id: str | None = None) -> Blueprint:
        blueprint = Blueprint(
            id=new_id(), project_id=project_id or new_id(), audit_id=audit_id,
            title=title, status=BuildStatus.DRAFT)
        with self.factory() as db:
            db.add(SystemState(key=_key(blueprint.id), value_json=blueprint.model_dump(mode="json")))
            db.commit()
        return blueprint

    def get(self, blueprint_id: str) -> Blueprint:
        with self.factory() as db:
            row = db.get(SystemState, _key(blueprint_id))
        if row is None:
            raise BlueprintNotFound(blueprint_id)
        return Blueprint.model_validate(row.value_json)

    def save(self, blueprint: Blueprint, *, status: BuildStatus | None = None) -> Blueprint:
        """Persist a change, bumping the revision and keeping the old one.

        `status` goes through the state machine rather than being assigned, so
        a caller cannot move a build somewhere it could not legally go.
        """
        previous = self.get(blueprint.id)
        if status is not None and status is not previous.status:
            check(previous.status, status)
        updated = blueprint.model_copy(update={
            "revision": previous.revision + 1,
            "status": status or blueprint.status,
            "updated_at": datetime.now(UTC).isoformat(),
        })
        with self.factory() as db:
            row = db.get(SystemState, _key(updated.id))
            if row is None:
                raise BlueprintNotFound(updated.id)
            db.add(SystemState(key=f"{HISTORY_PREFIX}{updated.id}:{previous.revision}",
                               value_json=previous.model_dump(mode="json")))
            row.value_json = updated.model_dump(mode="json")
            row.updated_at = datetime.now(UTC)
            db.commit()
        return updated

    def revision(self, blueprint_id: str, revision: int) -> Blueprint:
        """An earlier version, so "what did I approve?" survives later edits."""
        with self.factory() as db:
            row = db.get(SystemState, f"{HISTORY_PREFIX}{blueprint_id}:{revision}")
        if row is None:
            current = self.get(blueprint_id)
            if current.revision == revision:
                return current
            raise BlueprintNotFound(f"{blueprint_id}@{revision}")
        return Blueprint.model_validate(row.value_json)

    def for_audit(self, audit_id: str) -> Blueprint | None:
        """The blueprint already started for this idea, if there is one.

        Proceeding twice on the same idea returns to the work in progress
        rather than silently starting again beside it.
        """
        with self.factory() as db:
            rows = [row for row in db.query(SystemState).all() if row.key.startswith(PREFIX)]
        matching: list[Blueprint] = []
        for row in rows:
            data = row.value_json if isinstance(row.value_json, dict) else json.loads(row.value_json)
            if data.get("audit_id") == audit_id:
                try:
                    matching.append(Blueprint.model_validate(data))
                except Exception as exc:
                    logger.warning("Skipping unparseable blueprint row %s: %s", row.key, exc)
        if not matching:
            return None
        return max(matching, key=lambda blueprint: blueprint.updated_at)

    def list(self, limit: int = 50) -> list[Blueprint]:
        with self.factory() as db:
            rows = [row for row in db.query(SystemState).all() if row.key.startswith(PREFIX)]
        blueprints: list[Blueprint] = []
        for row in rows:
            try:
                blueprints.append(Blueprint.model_validate(row.value_json))
            except Exception as exc:
                logger.warning("Skipping unparseable blueprint row %s: %s", row.key, exc)
        blueprints.sort(key=lambda blueprint: blueprint.updated_at, reverse=True)
        return blueprints[:limit]
