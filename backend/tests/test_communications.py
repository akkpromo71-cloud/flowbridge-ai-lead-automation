from datetime import timedelta
from types import SimpleNamespace

import pytest
from app.communication import (
    CommunicationError,
    check_followups,
    ingest_inbound,
    parse_inbound,
    prepare_send,
    smtp_send,
    sync_mailbox,
)
from app.domain.config import load_business_config
from app.models import (
    IntegrationState,
    Job,
    Lead,
    Message,
    MessageVersion,
    Suppression,
    new_id,
    utcnow,
)
from app.settings import ROOT
from sqlalchemy import func, select


def outgoing(database, kind="initial"):
    with database.session.begin() as db:
        lead = Lead(
            id=new_id(),
            reference=new_id().replace("-", ""),
            intake_key=new_id(),
            payload_hash="x",
            name="Synthetic",
            email="client@example.com",
            original_message="Synthetic enquiry text",
            followup_status="pending_approval",
        )
        db.add(lead)
        db.flush()
        msg = Message(
            id=new_id(),
            lead_id=lead.id,
            direction="outbound",
            kind=kind,
            state="queued",
            purpose_key=new_id(),
        )
        db.add(msg)
        db.flush()
        version = MessageVersion(
            id=new_id(),
            message_id=msg.id,
            revision=1,
            subject="Synthetic",
            body="Only synthetic text",
            recipient=lead.email,
            checksum="x",
        )
        db.add(version)
        db.flush()
        msg.current_version_id = msg.approved_version_id = version.id
        job = Job(
            id=new_id(),
            lead_id=lead.id,
            message_id=msg.id,
            kind="email_send",
            dedup_key=f"send:{msg.id}:{version.id}",
            status="running",
            generation=1,
            lease_until=utcnow() + timedelta(minutes=3),
        )
        db.add(job)
    return lead, msg, version, job


@pytest.mark.parametrize("reason", ["reply", "closed", "paused", "optout"])
def test_followup_final_gate_checks_after_approval(database, settings, reason):
    lead, msg, version, job = outgoing(database, "followup")
    with database.session.begin() as db:
        row = db.get(Lead, lead.id)
        if reason == "reply":
            row.replied_at = utcnow()
        elif reason == "closed":
            row.sales_stage = "won"
        elif reason == "paused":
            row.communication_state = "paused"
        else:
            db.add(Suppression(email=lead.email, reason="opt_out"))
    with pytest.raises(CommunicationError):
        prepare_send(database, settings, load_business_config(ROOT / "config/automation.yaml"), job)
    with database.session() as db:
        assert db.get(Message, msg.id).state != "sending"


def test_stale_attempt_cannot_enter_send_gate(database, settings):
    lead, msg, version, job = outgoing(database)
    with database.session.begin() as db:
        db.get(Job, job.id).generation += 1
    with pytest.raises(CommunicationError, match="stale_send_attempt"):
        prepare_send(database, settings, load_business_config(ROOT / "config/automation.yaml"), job)


def test_old_approval_after_edit_cannot_send(database, settings):
    lead, msg, version, job = outgoing(database)
    with database.session.begin() as db:
        db.get(Message, msg.id).approved_version_id = None
    with pytest.raises(CommunicationError, match="approval_not_current"):
        prepare_send(database, settings, load_business_config(ROOT / "config/automation.yaml"), job)


def test_unknown_delivery_does_not_send_again(database, settings):
    lead, msg, version, job = outgoing(database)
    with database.session.begin() as db:
        db.get(Message, msg.id).state = "delivery_unknown"
    with pytest.raises(CommunicationError) as exc:
        prepare_send(database, settings, load_business_config(ROOT / "config/automation.yaml"), job)
    assert exc.value.unknown


def test_smtp_lost_confirmation_is_unknown(monkeypatch):
    class SMTP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def ehlo(self):
            pass

        def starttls(self, **kwargs):
            pass

        def login(self, *args):
            pass

        def send_message(self, *args, **kwargs):
            raise TimeoutError()

    monkeypatch.setattr("app.communication.smtplib.SMTP", SMTP)
    settings = SimpleNamespace(
        mode="live",
        allow_external_sends=True,
        smtp_host="synthetic.invalid",
        smtp_port=587,
        smtp_from="sender@example.com",
        smtp_username="synthetic",
        smtp_password=SimpleNamespace(get_secret_value=lambda: "test-only"),
    )
    with pytest.raises(CommunicationError) as exc:
        smtp_send(
            settings,
            {
                "recipient": "client@example.com",
                "subject": "Test",
                "body": "Synthetic",
                "message_id": "<id@example.com>",
            },
        )
    assert exc.value.unknown and not exc.value.retryable


def test_inbound_idempotent_threading_cancels_followup(database):
    lead, msg, version, job = outgoing(database, "followup")
    with database.session.begin() as db:
        db.get(Message, msg.id).rfc_message_id = "<original@example.com>"
    content = parse_inbound(
        b"From: client@example.com\r\nSubject: RE\r\nMessage-ID: <reply@example.com>\r\nIn-Reply-To: <original@example.com>\r\n\r\nThank you!"
    )
    for _ in range(2):
        with database.session.begin() as db:
            ingest_inbound(db, "imap:default:1:2", content)
    with database.session() as db:
        assert (
            db.scalar(
                select(func.count()).select_from(Message).where(Message.direction == "inbound")
            )
            == 1
        )
        assert db.get(Lead, lead.id).replied_at
        assert db.get(Lead, lead.id).followup_status == "cancelled"
        assert db.get(Message, msg.id).state == "cancelled"


def test_subject_alone_is_not_threading(database):
    lead, msg, version, job = outgoing(database, "followup")
    content = parse_inbound(b"From: client@example.com\r\nSubject: Synthetic\r\n\r\nThanks")
    with database.session.begin() as db:
        ingest_inbound(db, "imap:default:1:3", content)
    with database.session() as db:
        assert db.get(Lead, lead.id).replied_at is None
        assert db.get(Lead, lead.id).followup_status == "needs_review"


def test_due_with_stale_inbox_requires_review(database):
    lead, msg, version, job = outgoing(database)
    with database.session.begin() as db:
        row = db.get(Lead, lead.id)
        row.followup_status = "scheduled"
        row.followup_due_at = utcnow() - timedelta(hours=1)
    result = check_followups(database, load_business_config(ROOT / "config/automation.yaml"))
    assert result == {"prepared": 0, "needs_review": 1}


def test_due_only_one_followup_job(database, settings):
    lead, msg, version, job = outgoing(database)
    sync_mailbox(database, settings)
    now = utcnow()
    with database.session.begin() as db:
        row = db.get(Lead, lead.id)
        row.followup_status, row.followup_due_at = "scheduled", now + timedelta(hours=48)
    config = load_business_config(ROOT / "config/automation.yaml")
    assert check_followups(database, config, now)["prepared"] == 0
    with database.session.begin() as db:
        db.get(IntegrationState, "imap").last_success_at = now + timedelta(hours=49)
    assert check_followups(database, config, now + timedelta(hours=49))["prepared"] == 1
    assert check_followups(database, config, now + timedelta(hours=49))["prepared"] == 0
