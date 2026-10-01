"""Explicit offline lead deletion. No scheduled or public destructive endpoint."""

import argparse
from uuid import UUID

from sqlalchemy import func, select

from app.db import Database
from app.models import Analysis, Audit, Job, Lead, Message
from app.settings import Settings


def delete_lead(database, lead_id, confirm_reference=None):
    lead_id = str(UUID(str(lead_id)))
    with database.session.begin() as db:
        jobs = db.scalars(
            select(Job).where(Job.lead_id == lead_id).order_by(Job.id).with_for_update()
        ).all()
        lead = db.scalar(select(Lead).where(Lead.id == lead_id).with_for_update())
        if not lead:
            raise ValueError("Lead not found")
        counts = {
            "analyses": db.scalar(
                select(func.count()).select_from(Analysis).where(Analysis.lead_id == lead_id)
            ),
            "messages": db.scalar(
                select(func.count()).select_from(Message).where(Message.lead_id == lead_id)
            ),
            "jobs": len(jobs),
        }
        if confirm_reference is None:
            return {"status": "preview", "reference": lead.reference, "counts": counts}
        if confirm_reference != lead.reference:
            raise ValueError("Reference confirmation does not match")
        if any(job.status in {"dispatching", "dispatched", "running"} for job in jobs):
            raise ValueError("Active jobs must finish or be recovered before deletion")
        if db.scalar(
            select(Message.id).where(Message.lead_id == lead_id, Message.state == "sending")
        ):
            raise ValueError("An in-flight send cannot be deleted")
        db.delete(lead)
        # No subject, address, original ID or free-form reason survives this action.
        db.add(Audit(kind="data.lead_deleted", actor="local_admin", detail={"counts": counts}))
        return {"status": "deleted", "counts": counts}


def main():
    parser = argparse.ArgumentParser(
        description="Preview/delete one lead during offline maintenance"
    )
    parser.add_argument("--lead-id", type=UUID, required=True)
    parser.add_argument("--confirm-reference")
    args = parser.parse_args()
    try:
        result = delete_lead(
            Database(Settings().database_url), args.lead_id, args.confirm_reference
        )
    except ValueError as error:
        raise SystemExit(str(error)) from None
    print(result)


if __name__ == "__main__":
    main()
