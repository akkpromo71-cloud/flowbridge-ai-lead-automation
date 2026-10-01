import smtplib
from datetime import timedelta
from types import SimpleNamespace

import pytest
from app import communication
from app.communication import (
    CommunicationError,
    check_followups,
    inbox_fresh,
    prepare_send,
    smtp_send,
    sync_mailbox,
)
from app.domain.config import load_business_config
from app.models import IntegrationState, Lead, Message, MessageVersion, utcnow
from pydantic import SecretStr
from sqlalchemy import func, select
from test_communications import outgoing


def smtp_settings():
    return SimpleNamespace(
        mode="live",
        allow_external_sends=True,
        smtp_host="synthetic.invalid",
        smtp_port=587,
        smtp_from="sender@example.com",
        smtp_username="synthetic",
        smtp_password=SimpleNamespace(get_secret_value=lambda: "synthetic-only"),
    )


def smtp_envelope():
    return {
        "recipient": "client@example.com",
        "subject": "Synthetic regression",
        "body": "No real message is sent.",
        "message_id": "<synthetic@example.com>",
    }


def mock_smtp(monkeypatch, error):
    calls = []

    class SyntheticSMTP:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def ehlo(self):
            pass

        def starttls(self, **_kwargs):
            pass

        def login(self, *_args):
            pass

        def send_message(self, *_args, **_kwargs):
            calls.append("submission")
            raise error

    monkeypatch.setattr("app.communication.smtplib.SMTP", SyntheticSMTP)
    return calls


@pytest.mark.parametrize("code,retryable", [(450, True), (451, True), (550, False), (553, False)])
def test_smtp_recipient_refusal_uses_actual_rcpt_code(monkeypatch, code, retryable):
    calls = mock_smtp(
        monkeypatch,
        smtplib.SMTPRecipientsRefused({"client@example.com": (code, b"Synthetic refusal")}),
    )
    with pytest.raises(CommunicationError) as caught:
        smtp_send(smtp_settings(), smtp_envelope())
    assert caught.value.code == "smtp_rejected"
    assert caught.value.retryable is retryable
    assert caught.value.unknown is False
    assert calls == ["submission"]


@pytest.mark.parametrize(
    "recipients",
    [
        None,
        {},
        {"client@example.com": ()},
        {"client@example.com": (450,)},
        {"client@example.com": ("450", b"Untrusted type")},
        {"client@example.com": (250, b"Unexpected success")},
        {"another@example.com": (450, b"Unexpected recipient")},
    ],
)
def test_smtp_malformed_recipient_refusal_is_unknown_without_retry(monkeypatch, recipients):
    mock_smtp(monkeypatch, smtplib.SMTPRecipientsRefused(recipients))
    with pytest.raises(CommunicationError) as caught:
        smtp_send(smtp_settings(), smtp_envelope())
    assert caught.value.code == "smtp_delivery_unknown"
    assert caught.value.unknown is True
    assert caught.value.retryable is False


@pytest.mark.parametrize("error", [TimeoutError(), smtplib.SMTPServerDisconnected("Synthetic")])
def test_smtp_possible_acceptance_remains_unknown_without_retry(monkeypatch, error):
    calls = mock_smtp(monkeypatch, error)
    with pytest.raises(CommunicationError) as caught:
        smtp_send(smtp_settings(), smtp_envelope())
    assert caught.value.code == "smtp_delivery_unknown"
    assert caught.value.unknown is True
    assert caught.value.retryable is False
    assert calls == ["submission"]


MAIL_LIMIT = 2_000_000
VALIDITY = 777


def normal_mail(uid):
    return (
        f"From: unrelated@example.com\r\nSubject: Synthetic {uid}\r\n"
        f"Message-ID: <inbound-{uid}@example.com>\r\n\r\nNormal synthetic message {uid}."
    ).encode()


def mailbox_settings(settings):
    # Only the protocol object is live-shaped; IMAP is replaced before use.
    return settings.model_copy(
        update={
            "mode": "live",
            "imap_host": "synthetic.invalid",
            "imap_username": "synthetic",
            "imap_password": SecretStr("synthetic-only"),
        }
    )


def mock_mailbox(monkeypatch, messages, reported_sizes=None):
    fetches = []
    reported_sizes = reported_sizes or {}

    class SyntheticMailbox:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def login(self, *_args):
            pass

        def select(self, *_args, **_kwargs):
            return "OK", []

        def response(self, *_args):
            return "UIDVALIDITY", [str(VALIDITY).encode()]

        def uid(self, operation, *args):
            if operation == "search":
                # Returning earlier UIDs also exercises the client's cursor filter.
                return "OK", [b" ".join(str(uid).encode() for uid in messages)]
            assert operation == "fetch"
            uid, fields = int(args[0]), args[1]
            fetches.append((uid, fields))
            size = reported_sizes.get(uid, len(messages[uid]))
            if fields == "(RFC822.SIZE)":
                return "OK", [f"{uid} (UID {uid} RFC822.SIZE {size})".encode()]
            assert "BODY.PEEK[]" in fields
            raw = messages[uid]
            if "<0." in fields:
                bound = int(fields.split("<0.", 1)[1].split(">", 1)[0])
                raw = raw[:bound]
            return "OK", [(f"{uid} (UID {uid} BODY[] {{{len(raw)}}}".encode(), raw), b")"]

    monkeypatch.setattr("app.communication.imaplib.IMAP4_SSL", SyntheticMailbox)
    return fetches


@pytest.mark.postgres
def test_imap_large_message_is_recorded_then_later_messages_continue_and_replay_is_safe(
    database, settings, monkeypatch
):
    messages = {1: normal_mail(1), 2: b"PRIVATE-ORIGINAL-" + b"x" * MAIL_LIMIT, 3: normal_mail(3)}
    fetches = mock_mailbox(monkeypatch, messages)
    live = mailbox_settings(settings)
    try:
        result = sync_mailbox(database, live)
    except CommunicationError as first_error:
        try:
            sync_mailbox(database, live)
        except CommunicationError as repeated_error:
            with database.session() as db:
                checkpoint = db.get(IntegrationState, "imap").detail["last_uid"]
            pytest.fail(
                f"Sync repeated {first_error.code}/{repeated_error.code}; "
                f"last_uid={checkpoint}; later_uid_fetched={any(uid == 3 for uid, _ in fetches)}"
            )
        raise
    assert result == {"state": "needs_review", "received": 3}
    with database.session() as db:
        assert db.scalar(select(func.count()).select_from(Message)) == 3
        saved = db.get(IntegrationState, "imap")
        assert saved.detail == {"uidvalidity": VALIDITY, "last_uid": 3}
        assert saved.state == "needs_review" and saved.last_success_at is None
        problem = db.scalar(
            select(Message).where(Message.purpose_key == f"imap:default:{VALIDITY}:2")
        )
        assert problem.state == "unmatched" and problem.lead_id is None
        assert problem.reply_headers["problem"] == "imap_message_too_large"
        assert problem.reply_headers["size"] == len(messages[2])
        version = db.get(MessageVersion, problem.current_version_id)
        assert "PRIVATE-ORIGINAL" not in version.body
        assert len(version.body) < 1000
        for expected in ("imap_message_too_large", "default", str(VALIDITY), str(len(messages[2]))):
            assert expected in version.body
        assert not inbox_fresh(db, load_business_config(settings.business_config))
    assert not any(uid == 2 and "BODY" in fields for uid, fields in fetches)
    assert all(
        fields == f"(BODY.PEEK[]<0.{MAIL_LIMIT + 1}>)" for _, fields in fetches if "BODY" in fields
    )
    count = len(fetches)
    assert sync_mailbox(database, live) == {"state": "needs_review", "received": 0}
    assert len(fetches) == count
    with database.session() as db:
        assert db.scalar(select(func.count()).select_from(Message)) == 3


@pytest.mark.postgres
def test_imap_problem_and_checkpoint_roll_back_together_then_retry_continues(
    database, settings, monkeypatch
):
    messages = {1: normal_mail(1), 2: b"x" * (MAIL_LIMIT + 1), 3: normal_mail(3)}
    mock_mailbox(monkeypatch, messages)
    original = communication.ingest_inbound

    def fail_after_result(db, event_key, content):
        result = original(db, event_key, content)
        if event_key == f"imap:default:{VALIDITY}:2":
            # Ingestion flushed the result; checkpoint has not been written yet.
            raise CommunicationError("synthetic_checkpoint_interruption")
        return result

    monkeypatch.setattr(communication, "ingest_inbound", fail_after_result)
    with pytest.raises(CommunicationError, match="synthetic_checkpoint_interruption"):
        sync_mailbox(database, mailbox_settings(settings))
    with database.session() as db:
        assert db.scalar(select(func.count()).select_from(Message)) == 1
        saved = db.get(IntegrationState, "imap")
        assert saved.detail["last_uid"] == 1 and saved.state == "error"
        assert saved.last_success_at is None
    monkeypatch.setattr(communication, "ingest_inbound", original)
    assert sync_mailbox(database, mailbox_settings(settings)) == {
        "state": "needs_review",
        "received": 2,
    }
    with database.session() as db:
        assert db.scalar(select(func.count()).select_from(Message)) == 3
        assert db.get(IntegrationState, "imap").detail["last_uid"] == 3


@pytest.mark.postgres
@pytest.mark.parametrize("size", ["unknown", "-1", "1 RFC822.SIZE 2"])
def test_imap_invalid_reported_size_records_problem_without_loading_body(
    database, settings, monkeypatch, size
):
    fetches = mock_mailbox(monkeypatch, {1: normal_mail(1)}, {1: size})
    assert sync_mailbox(database, mailbox_settings(settings)) == {
        "state": "needs_review",
        "received": 1,
    }
    assert fetches == [(1, "(RFC822.SIZE)")]
    with database.session() as db:
        problem = db.scalar(select(Message))
        assert problem.reply_headers["problem"] == "imap_size_unverified"
        assert problem.reply_headers["size"] is None
        assert db.get(IntegrationState, "imap").detail["last_uid"] == 1


@pytest.mark.postgres
def test_imap_body_is_bounded_when_reported_size_is_false(database, settings, monkeypatch):
    fetches = mock_mailbox(monkeypatch, {1: b"x" * (MAIL_LIMIT + 100)}, {1: 500})
    assert sync_mailbox(database, mailbox_settings(settings)) == {
        "state": "needs_review",
        "received": 1,
    }
    assert fetches[-1] == (1, f"(BODY.PEEK[]<0.{MAIL_LIMIT + 1}>)")
    with database.session() as db:
        problem = db.scalar(select(Message))
        assert problem.reply_headers["problem"] == "imap_message_too_large"
        assert len(db.get(MessageVersion, problem.current_version_id).body) < 1000


@pytest.mark.postgres
def test_imap_unordered_search_preserves_checkpoint_across_bounded_batches(
    database, settings, monkeypatch
):
    messages = {101: normal_mail(101), **{uid: normal_mail(uid) for uid in range(1, 101)}}
    mock_mailbox(monkeypatch, messages)
    live = mailbox_settings(settings)
    first = sync_mailbox(database, live)
    with database.session() as db:
        first_checkpoint = db.get(IntegrationState, "imap").detail["last_uid"]
        first_count = db.scalar(select(func.count()).select_from(Message))
    second = sync_mailbox(database, live)
    with database.session() as db:
        second_checkpoint = db.get(IntegrationState, "imap").detail["last_uid"]
        second_count = db.scalar(select(func.count()).select_from(Message))
    assert (first_checkpoint, second_checkpoint, first_count, second_count) == (100, 101, 100, 101)
    assert first == {"state": "syncing", "received": 100}
    assert second == {"state": "ready", "received": 1}


@pytest.mark.postgres
def test_imap_unresolved_problem_blocks_all_followups_until_explicit_existing_review(
    authenticated, database, settings, monkeypatch
):
    first, first_message, _, _ = outgoing(database, "followup")
    other, other_message, _, other_job = outgoing(database, "followup")
    with database.session.begin() as db:
        for lead_id in (first.id, other.id):
            lead = db.get(Lead, lead_id)
            lead.followup_status = "scheduled"
            lead.followup_due_at = utcnow() - timedelta(hours=1)
    mock_mailbox(monkeypatch, {1: b"x" * (MAIL_LIMIT + 1)})
    live = mailbox_settings(settings)
    config = load_business_config(settings.business_config)
    assert sync_mailbox(database, live)["state"] == "needs_review"
    with database.session() as db:
        problem_id = db.scalar(select(Message.id).where(Message.direction == "inbound"))
        # Suspension is immediate: matching before the next scheduled check
        # must not silently restore any earlier follow-up approval.
        assert db.get(Lead, first.id).followup_status == "needs_review"
        assert db.get(Lead, other.id).followup_status == "needs_review"
        assert not inbox_fresh(db, config)
    assert check_followups(database, config) == {"prepared": 0, "needs_review": 0}
    matched = authenticated.post(
        f"/api/v1/admin/inbox/{problem_id}/match",
        json={
            "lead_id": first.id,
            "reason": "Synthetic manual mailbox inspection identified this reply",
        },
    )
    assert matched.status_code == 200
    assert sync_mailbox(database, live) == {"state": "ready", "received": 0}
    with database.session() as db:
        assert inbox_fresh(db, config)
        assert db.get(Lead, first.id).followup_status == "cancelled"
        assert db.get(Message, first_message.id).state == "cancelled"
        assert db.get(Lead, other.id).followup_status == "needs_review"
    assert check_followups(database, config) == {"prepared": 0, "needs_review": 0}
    with pytest.raises(CommunicationError, match="followup_stopped"):
        prepare_send(database, live, config, other_job)
    reviewed = authenticated.post(
        f"/api/v1/admin/leads/{other.id}/followup-review",
        json={"reason": "Synthetic mailbox review confirmed no reply for this lead"},
    )
    assert reviewed.status_code == 200
    with database.session() as db:
        current = db.get(Message, other_message.id)
        assert current.state == "pending_approval" and current.approved_version_id is None
