from datetime import timedelta
from uuid import uuid4

import pytest
from app.communication import CommunicationError, ingest_inbound, prepare_send, sync_mailbox
from app.domain.config import load_business_config
from app.jobs import acknowledge_dispatch, claim_step, dispatch_claim, recover
from app.models import Job, Lead, Message, MessageVersion, utcnow
from app.processing import execute_step
from app.settings import Settings
from pydantic import SecretStr, ValidationError
from sqlalchemy import select, text
from test_communications import outgoing
from test_processing import create_lead_job


@pytest.mark.postgres
def test_terminal_dispatch_failure_clears_pending_lead_state(database, settings):
    _config, job = create_lead_job(database, settings)
    with database.session.begin() as db:
        db.get(Job, job.id).attempts = settings.max_job_attempts
    acknowledge_dispatch(database, job.id, job.generation, False, settings)
    with database.session() as db:
        assert db.get(Job, job.id).status == "failed"
        assert db.get(Lead, job.lead_id).processing_status == "failed"


@pytest.mark.postgres
def test_expired_final_analysis_attempt_cannot_leave_processing_stuck(database, settings):
    _config, job = create_lead_job(database, settings)
    now = utcnow()
    claim_step(database, job.id, job.generation, "analyze", settings, now)
    with database.session.begin() as db:
        db.get(Job, job.id).attempts = settings.max_job_attempts
        db.get(Lead, job.lead_id).processing_status = "processing"
    recover(database, settings, now + timedelta(seconds=settings.job_lease_seconds + 1))
    with database.session() as db:
        assert db.get(Lead, job.lead_id).processing_status == "failed"


@pytest.mark.postgres
def test_failed_later_step_does_not_discard_completed_analysis(database, settings):
    config, job = create_lead_job(database, settings)
    execute_step(database, settings, config, job.id, job.generation, "analyze")
    now = utcnow()
    claim_step(database, job.id, job.generation, "draft", settings, now)
    with database.session.begin() as db:
        db.get(Job, job.id).attempts = settings.max_job_attempts
    recover(database, settings, now + timedelta(seconds=settings.job_lease_seconds + 1))
    with database.session() as db:
        lead = db.get(Lead, job.lead_id)
        assert lead.processing_status == "completed" and lead.score == 92
        assert db.get(Job, job.id).status == "failed"


@pytest.mark.postgres
def test_stale_send_failure_cannot_clobber_new_approved_version(authenticated, database, settings):
    lead, message, _version, old_job = outgoing(database)
    changed = authenticated.post(
        f"/api/v1/admin/messages/{message.id}/versions",
        json={
            "subject": "New version",
            "body": "Synthetic revised message",
            "recipient": lead.email,
            "expected_version": 1,
        },
    )
    assert changed.status_code == 201
    version_id = changed.json()["id"]
    approved = authenticated.post(
        f"/api/v1/admin/messages/{message.id}/decisions",
        json={"version_id": version_id, "decision": "approve"},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert approved.status_code == 200
    config = load_business_config(settings.business_config)
    with pytest.raises(CommunicationError, match="approval_not_current"):
        execute_step(database, settings, config, old_job.id, old_job.generation, "send")
    with database.session() as db:
        current = db.get(Message, message.id)
        assert current.state == "queued" and current.approved_version_id == version_id
    newer = dispatch_claim(database, settings)
    result = execute_step(database, settings, config, newer.id, newer.generation, "send")
    assert result["provider"] == "fake" and result["accepted"] is True


@pytest.mark.postgres
def test_recovery_of_old_send_does_not_mark_new_send_unknown(database, settings):
    lead, message, version, old_job = outgoing(database)
    now = utcnow()
    claim_step(database, old_job.id, old_job.generation, "send", settings, now)
    with database.session.begin() as db:
        # An edit occurred before the old attempt reached its final gate.
        revised = MessageVersion(
            message_id=message.id,
            revision=2,
            recipient=lead.email,
            subject=version.subject,
            body="New version",
            checksum="synthetic",
        )
        db.add(revised)
        db.flush()
        current = db.get(Message, message.id)
        current.current_version_id = current.approved_version_id = revised.id
        current.state = "sending"
    recover(database, settings, now + timedelta(seconds=settings.job_lease_seconds + 1))
    with database.session() as db:
        assert db.get(Message, message.id).state == "sending"
        assert db.get(Job, old_job.id).status == "needs_review"


@pytest.mark.postgres
def test_unmatched_mail_blocks_previously_approved_followup_after_fresh_sync(database, settings):
    lead, message, _version, job = outgoing(database, kind="followup")
    with database.session.begin() as db:
        ingest_inbound(
            db,
            "synthetic:unmatched",
            {
                "sender": lead.email,
                "subject": "Same subject",
                "body": "Unthreaded response",
                "message_id": "<unmatched@example.com>",
                "references": [],
                "automated": False,
            },
        )
    sync_mailbox(database, settings)
    with pytest.raises(CommunicationError, match="followup_stopped"):
        prepare_send(database, settings, load_business_config(settings.business_config), job)
    with database.session() as db:
        assert db.get(Lead, lead.id).followup_status == "needs_review"


@pytest.mark.postgres
def test_fake_adapter_executes_without_open_database_transaction(database, settings, monkeypatch):
    config, job = create_lead_job(database, settings)
    from app.adapters.ai import FakeAIAdapter

    original = FakeAIAdapter.analyze

    def analyze(adapter, source, business):
        assert database.engine.pool.checkedout() == 0
        return original(adapter, source, business)

    monkeypatch.setattr("app.processing.FakeAIAdapter.analyze", analyze)
    execute_step(database, settings, config, job.id, job.generation, "analyze")


@pytest.mark.parametrize(
    "secret", ["openai_api_key", "telegram_bot_token", "smtp_password", "imap_password"]
)
def test_demo_rejects_every_live_secret(secret):
    with pytest.raises(ValidationError, match="Demo/test must not receive live credentials"):
        Settings(_env_file=None, mode="demo", **{secret: "synthetic-secret"})


def test_live_requires_https_secure_cookie_and_distinct_service_secrets():
    safe = dict(
        _env_file=None,
        mode="live",
        public_url="https://app.example.com",
        secure_cookies=True,
        internal_token="a" * 40,
        n8n_webhook_token="b" * 40,
        database_url="postgresql+psycopg://synthetic:password@localhost/synthetic_live",
    )
    assert Settings(**safe).mode == "live"
    for update in [
        dict(public_url="http://app.example.com"),
        dict(secure_cookies=False),
        dict(n8n_webhook_token="a" * 40),
        dict(internal_token="short"),
    ]:
        with pytest.raises(ValidationError):
            Settings(**(safe | update))


@pytest.mark.postgres
def test_terminal_send_requires_new_version_instead_of_dead_queue(authenticated, database):
    _lead, message, version, job = outgoing(database)
    with database.session.begin() as db:
        db.get(Job, job.id).status = "failed"
        db.get(Message, message.id).state = "failed"
    response = authenticated.post(
        f"/api/v1/admin/messages/{message.id}/decisions",
        json={"version_id": version.id, "decision": "approve"},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert response.status_code == 409
    with database.session() as db:
        assert db.get(Message, message.id).state == "failed"


@pytest.mark.postgres
def test_manual_followup_review_requires_fresh_version_and_approval(
    authenticated, database, settings
):
    lead, message, version, job = outgoing(database, kind="followup")
    with database.session.begin() as db:
        db.get(Job, job.id).status = "failed"
        db.get(Lead, lead.id).followup_status = "needs_review"
    sync_mailbox(database, settings)
    reviewed = authenticated.post(
        f"/api/v1/admin/leads/{lead.id}/followup-review",
        json={"reason": "Checked synthetic mailbox manually"},
    )
    assert reviewed.status_code == 200
    with database.session() as db:
        current = db.get(Message, message.id)
        assert current.current_version_id != version.id and current.approved_version_id is None
        assert current.state == "pending_approval"
        revision_id = current.current_version_id
        revision = db.get(MessageVersion, revision_id)
        assert revision.revision == 2 and revision.body == version.body
    response = authenticated.post(
        f"/api/v1/admin/messages/{message.id}/decisions",
        json={"version_id": revision_id, "decision": "approve"},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert response.status_code == 200
    with database.session() as db:
        assert (
            db.scalar(select(Job).where(Job.dedup_key == f"send:{message.id}:{revision_id}")).status
            == "pending"
        )
        assert db.get(Job, job.id).status == "failed"


@pytest.mark.postgres
def test_manual_review_never_restarts_unknown_delivery(authenticated, database, settings):
    lead, message, _version, _job = outgoing(database, kind="followup")
    with database.session.begin() as db:
        db.get(Lead, lead.id).followup_status = "needs_review"
        db.get(Message, message.id).state = "delivery_unknown"
    sync_mailbox(database, settings)
    response = authenticated.post(
        f"/api/v1/admin/leads/{lead.id}/followup-review",
        json={"reason": "Synthetic review must not bypass delivery reconciliation"},
    )
    assert response.status_code == 409
    with database.session() as db:
        assert db.get(Message, message.id).state == "delivery_unknown"
        assert db.get(Lead, lead.id).followup_status == "needs_review"


@pytest.mark.postgres
def test_explicit_review_can_retry_failed_draft_preparation(authenticated, database, settings):
    _config, initial = create_lead_job(database, settings)
    with database.session.begin() as db:
        db.get(Lead, initial.lead_id).followup_status = "needs_review"
        failed = Job(
            kind="followup_prepare",
            lead_id=initial.lead_id,
            dedup_key=f"followup:{initial.lead_id}",
            status="failed",
            attempts=5,
        )
        db.add(failed)
        db.flush()
        failed_id = failed.id
    sync_mailbox(database, settings)
    response = authenticated.post(
        f"/api/v1/admin/leads/{initial.lead_id}/followup-review",
        json={"reason": "Explicitly retry synthetic draft preparation"},
    )
    assert response.status_code == 200
    with database.session() as db:
        saved = db.get(Job, failed_id)
        assert saved.status == "pending" and saved.attempts == 0
        assert db.get(Lead, initial.lead_id).followup_status == "pending_approval"


@pytest.mark.postgres
def test_imap_network_boundary_holds_no_open_database_transaction(database, settings, monkeypatch):
    class SyntheticMailbox:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def login(self, *_args):
            # The advisory-lock session is AUTOCOMMIT during the IMAP call.
            with database.session() as db:
                count = db.scalar(
                    text(
                        "SELECT count(*) FROM pg_stat_activity "
                        "WHERE datname=current_database() AND state='idle in transaction'"
                    )
                )
                assert count == 0

        def select(self, *_args, **_kwargs):
            return "OK", []

        def response(self, *_args):
            return "UIDVALIDITY", [b"123"]

        def uid(self, operation, *_args):
            assert operation == "search"
            return "OK", [b""]

    monkeypatch.setattr("app.communication.imaplib.IMAP4_SSL", SyntheticMailbox)
    synthetic_live = settings.model_copy(
        update={
            "mode": "live",
            "imap_host": "synthetic.invalid",
            "imap_username": "synthetic",
            "imap_password": SecretStr("synthetic-only"),
        }
    )
    assert sync_mailbox(database, synthetic_live) == {"state": "ready", "received": 0}
