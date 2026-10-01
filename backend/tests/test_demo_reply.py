from concurrent.futures import ThreadPoolExecutor

import pytest
from app.demo import demo_reply
from app.models import IntegrationState, Lead, Message, new_id, utcnow
from sqlalchemy import func, select
from test_communications import outgoing


def accepted_outbound(database):
    lead, message, _, _ = outgoing(database)
    with database.session.begin() as db:
        db.add(IntegrationState(name="deployment", state="ready", detail={"mode": "demo"}))
        row = db.get(Message, message.id)
        row.state = "provider_accepted"
        row.rfc_message_id = f"<{message.id}@demo.invalid>"
        row.provider_accepted_at = utcnow()
        db.get(Lead, lead.id).sales_stage = "contacted"
    return lead, message


@pytest.mark.parametrize(
    "mode, marker, address, expected",
    [
        ("test", "demo", "client@example.com", "requires_demo_mode"),
        ("live", "demo", "client@example.com", "requires_demo_mode"),
        ("demo", None, "client@example.com", "requires_demo_database"),
        ("demo", "live", "client@example.com", "requires_demo_database"),
        ("demo", "demo", "client@example.org", "requires_synthetic_address"),
        ("demo", "demo", "client@example.com.invalid", "requires_synthetic_address"),
    ],
)
def test_demo_reply_requires_isolated_demo_and_synthetic_address(
    database, settings, mode, marker, address, expected
):
    lead, _ = accepted_outbound(database)
    with database.session.begin() as db:
        deployment = db.get(IntegrationState, "deployment")
        if marker is None:
            db.delete(deployment)
        else:
            deployment.detail = {"mode": marker}
        db.get(Lead, lead.id).email = address
    with pytest.raises(ValueError, match=expected):
        demo_reply(database, settings.model_copy(update={"mode": mode}), lead.id)
    with database.session() as db:
        assert (
            db.scalar(
                select(func.count()).select_from(Message).where(Message.direction == "inbound")
            )
            == 0
        )
        assert db.get(Lead, lead.id).replied_at is None


def test_demo_reply_concurrent_repeat_ingests_once_and_cancels_followup(database, settings):
    lead, outbound = accepted_outbound(database)
    followup_id = new_id()
    with database.session.begin() as db:
        db.add(
            Message(
                id=followup_id,
                lead_id=lead.id,
                direction="outbound",
                kind="followup",
                state="pending_approval",
                purpose_key=f"followup:{lead.id}",
            )
        )
    demo_settings = settings.model_copy(update={"mode": "demo"})
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: demo_reply(database, demo_settings, lead.id), range(2)))
    assert results[0] == results[1]
    with database.session() as db:
        messages = db.scalars(select(Message).where(Message.direction == "inbound")).all()
        assert len(messages) == 1
        assert messages[0].state == "received"
        assert messages[0].lead_id == lead.id
        assert messages[0].reply_headers["sender"] == lead.email
        assert set(messages[0].reply_headers["references"]) == {f"<{outbound.id}@demo.invalid>"}
        assert db.get(Lead, lead.id).sales_stage == "replied"
        assert db.get(Lead, lead.id).replied_at is not None
        assert db.get(Lead, lead.id).followup_status == "cancelled"
        assert db.get(Message, followup_id).state == "cancelled"
        assert db.get(Message, outbound.id).state == "provider_accepted"


@pytest.mark.parametrize(
    "state, rfc_id", [("queued", "<queued@demo.invalid>"), ("provider_accepted", None)]
)
def test_demo_reply_requires_accepted_outbound_with_thread_id(database, settings, state, rfc_id):
    lead, message = accepted_outbound(database)
    with database.session.begin() as db:
        row = db.get(Message, message.id)
        row.state, row.rfc_message_id = state, rfc_id
    with pytest.raises(ValueError, match="requires_accepted_outbound"):
        demo_reply(database, settings.model_copy(update={"mode": "demo"}), lead.id)
