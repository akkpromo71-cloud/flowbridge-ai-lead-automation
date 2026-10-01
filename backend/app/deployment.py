"""A shared startup fence for physically separated demo/live databases."""

from typing import Literal

from sqlalchemy.dialects.postgresql import insert

from app.db import Database
from app.models import IntegrationState


def ensure_database_mode(
    database: Database, mode: Literal["demo", "controlled", "test", "live"]
) -> None:
    with database.session.begin() as db:
        # A concurrent first startup must not overwrite the first process's mode.
        db.execute(
            insert(IntegrationState)
            .values(name="deployment", state="ready", detail={"mode": mode})
            .on_conflict_do_nothing(index_elements=["name"])
        )
        deployment = db.get(IntegrationState, "deployment")
        if deployment is None or deployment.detail.get("mode") != mode:
            raise RuntimeError("Database mode mismatch: demo/live data must be physically isolated")
