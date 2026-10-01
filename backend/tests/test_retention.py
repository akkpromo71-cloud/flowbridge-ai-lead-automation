import pytest
from app.models import Audit, Job, Lead, Message, MessageVersion
from app.retention import delete_lead
from sqlalchemy import select
from test_communications import outgoing


def test_preview_and_wrong_reference_do_not_delete(database):
    lead, message, version, job = outgoing(database)
    assert delete_lead(database, lead.id)["status"] == "preview"
    with pytest.raises(ValueError, match="confirmation"):
        delete_lead(database, lead.id, "wrong")
    with database.session() as db:
        assert db.get(Lead, lead.id)


def test_active_job_blocks_deletion(database):
    lead, message, version, job = outgoing(database)
    with pytest.raises(ValueError, match="Active jobs"):
        delete_lead(database, lead.id, lead.reference)


def test_delete_cascades_owned_data_without_pii_audit(database):
    lead, message, version, job = outgoing(database)
    with database.session.begin() as db:
        db.get(Job, job.id).status = "succeeded"
    assert delete_lead(database, lead.id, lead.reference)["status"] == "deleted"
    with database.session() as db:
        assert db.get(Lead, lead.id) is None
        assert db.get(Message, message.id) is None
        assert db.get(MessageVersion, version.id) is None
        assert db.get(Job, job.id) is None
        event = db.scalar(select(Audit).where(Audit.kind == "data.lead_deleted"))
        assert event.lead_id is None
        assert lead.id not in str(event.detail) and lead.email not in str(event.detail)
