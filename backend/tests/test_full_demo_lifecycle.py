"""Full backend lifecycle on PostgreSQL with explicit clock and fake providers.

Real n8n/browser dispatch is covered separately by processing-smoke.ts. This
test executes the same protected step functions, not a second business engine.
"""

from datetime import timedelta
from uuid import uuid4

import pytest
from app.communication import check_followups, ingest_inbound, parse_inbound, sync_mailbox
from app.jobs import dispatch_claim
from app.models import Approval, IntegrationState, Lead, Message
from app.processing import execute_step
from sqlalchemy import func, select
from test_processing import create_lead_job, process


@pytest.mark.postgres
def test_full_lifecycle_separate_followup_approval_reply_and_no_second_followup(
    authenticated, database, settings
):
    config, initial_job = create_lead_job(database, settings)
    process(database, settings, config, initial_job)

    def approve(message):
        result = authenticated.post(
            f"/api/v1/admin/messages/{message.id}/decisions",
            json={"version_id": message.current_version_id, "decision": "approve"},
            headers={"Idempotency-Key": str(uuid4())},
        )
        assert result.status_code == 200

    def drain():
        for _ in range(5):
            job = dispatch_claim(database, settings)
            if not job:
                return
            for step in {
                "notification": ["notify", "finish"],
                "email_send": ["send", "finish"],
                "followup_prepare": ["draft", "finish"],
            }[job.kind]:
                execute_step(database, settings, config, job.id, job.generation, step)
        pytest.fail("Unexpected unbounded work in isolated synthetic test")

    with database.session() as db:
        initial = db.scalar(select(Message).where(Message.kind == "initial"))
    approve(initial)
    drain()
    sync_mailbox(database, settings)
    with database.session.begin() as db:
        lead = db.get(Lead, initial_job.lead_id)
        initial = db.get(Message, initial.id)
        assert initial.state == "provider_accepted"
        assert lead.followup_due_at == initial.provider_accepted_at + timedelta(
            hours=config.followup.delay_hours
        )
        future = lead.followup_due_at + timedelta(minutes=1)
        db.get(IntegrationState, "imap").last_success_at = future
    assert check_followups(database, config, now=future)["prepared"] == 1
    drain()
    with database.session() as db:
        followup = db.scalar(select(Message).where(Message.kind == "followup"))
        assert followup.state == "pending_approval" and followup.approved_version_id is None
        assert db.scalar(select(func.count()).select_from(Approval)) == 1
    approve(followup)
    drain()
    with database.session.begin() as db:
        followup = db.get(Message, followup.id)
        assert followup.state == "provider_accepted"
        assert db.get(Lead, initial_job.lead_id).followup_status == "completed"
        reply = parse_inbound(
            (
                "From: demo@example.com\r\nSubject: Synthetic reply\r\n"
                f"In-Reply-To: {followup.rfc_message_id}\r\n\r\nThank you."
            ).encode()
        )
        ingest_inbound(db, "synthetic-lifecycle:1:1", reply)
    assert check_followups(database, config, now=future + timedelta(days=3))["prepared"] == 0
    with database.session() as db:
        assert db.get(Lead, initial_job.lead_id).sales_stage == "replied"
        assert db.scalar(select(func.count()).select_from(Approval)) == 2
        assert (
            db.scalar(select(func.count()).select_from(Message).where(Message.kind == "followup"))
            == 1
        )
