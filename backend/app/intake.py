import json
import secrets

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.models import Audit, Job, Lead, new_id
from app.security import digest


def canonical_hash(value: dict) -> str:
    return digest(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")))


def key_hash(key: str | None) -> str:
    if not key or not 16 <= len(key) <= 128 or not key.isascii():
        raise HTTPException(422, "Нужен Idempotency-Key длиной 16–128 символов")
    return digest(key)


def accept_lead(database, config, payload, key: str) -> dict:
    hashed_key = key_hash(key)
    values = payload.model_dump(mode="json")
    values["email"] = values["email"].lower()
    hashed_payload = canonical_hash(values)
    lead_id = new_id()
    with database.session.begin() as db:
        created = db.execute(
            insert(Lead)
            .values(
                id=lead_id,
                reference=secrets.token_urlsafe(16),
                intake_key=hashed_key,
                payload_hash=hashed_payload,
                name=values["name"],
                email=values["email"],
                original_message=values["message"],
                phone=values["phone"],
                company=values["company"],
                utm=values["utm"],
                source="website",
            )
            .on_conflict_do_nothing(index_elements=["intake_key"])
            .returning(Lead.id)
        ).scalar_one_or_none()
        if created:
            db.add(
                Job(
                    kind="lead_processing",
                    lead_id=lead_id,
                    dedup_key=f"intake:{lead_id}",
                    config_snapshot=config.model_dump(mode="json"),
                )
            )
            db.add(Audit(lead_id=lead_id, kind="lead.accepted", actor="public", detail={}))
        lead = db.scalar(select(Lead).where(Lead.intake_key == hashed_key))
        if lead.payload_hash != hashed_payload:
            raise HTTPException(409, "Этот ключ уже использован для другого обращения")
        return {"reference": lead.reference, "status": "accepted"}
