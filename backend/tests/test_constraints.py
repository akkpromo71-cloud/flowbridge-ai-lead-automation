import pytest
from app.models import Analysis, Job, Lead, Message, MessageVersion, new_id
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.postgres


def owned_records(database):
    result = []
    with database.session.begin() as db:
        for index in range(2):
            lead = Lead(
                id=new_id(),
                reference=new_id().replace("-", ""),
                intake_key=new_id(),
                payload_hash="synthetic",
                name="Synthetic",
                email=f"demo{index}@example.com",
                original_message="Synthetic inquiry only",
            )
            db.add(lead)
            db.flush()
            analysis = Analysis(
                id=new_id(),
                lead_id=lead.id,
                operation_key=new_id(),
                facts={},
                result={},
                config_version="test",
                config_snapshot={},
                prompt_version="test",
                schema_version="test",
                model="fake",
            )
            db.add(analysis)
            message = Message(
                id=new_id(),
                lead_id=lead.id,
                direction="outbound",
                kind="initial",
                purpose_key=new_id(),
                state="queued",
            )
            db.add(message)
            db.flush()
            version = MessageVersion(
                id=new_id(),
                message_id=message.id,
                revision=1,
                recipient=lead.email,
                subject="Synthetic",
                body="Synthetic",
                checksum="synthetic",
            )
            db.add(version)
            db.flush()
            lead.analysis_id = analysis.id
            message.current_version_id = message.approved_version_id = version.id
            job = Job(
                id=new_id(),
                lead_id=lead.id,
                message_id=message.id,
                kind="email_send",
                dedup_key=new_id(),
            )
            db.add(job)
            result.append(
                dict(
                    lead=lead.id,
                    analysis=analysis.id,
                    message=message.id,
                    version=version.id,
                    job=job.id,
                )
            )
    return result


@pytest.mark.parametrize("field", ["current_version_id", "approved_version_id"])
def test_database_rejects_version_owned_by_another_message(database, field):
    first, other = owned_records(database)
    with pytest.raises(IntegrityError, match="fk_message_owned"):
        with database.session.begin() as db:
            message = db.get(Message, first["message"])
            setattr(message, field, other["version"])
    with database.session() as db:
        assert getattr(db.get(Message, first["message"]), field) == first["version"]


def test_database_rejects_analysis_owned_by_another_lead(database):
    first, other = owned_records(database)
    with pytest.raises(IntegrityError, match="fk_lead_owned_analysis"):
        with database.session.begin() as db:
            db.get(Lead, first["lead"]).analysis_id = other["analysis"]


def test_delete_lead_cascades_its_own_graph_with_deferred_owner_constraints(database):
    first, other = owned_records(database)
    with database.session.begin() as db:
        db.execute(delete(Lead).where(Lead.id == first["lead"]))
    with database.session() as db:
        for model, key in [
            (Lead, "lead"),
            (Analysis, "analysis"),
            (Message, "message"),
            (MessageVersion, "version"),
            (Job, "job"),
        ]:
            assert db.get(model, first[key]) is None
            assert db.scalar(select(model).where(model.id == other[key])) is not None
