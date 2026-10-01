from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from app.models import Approval, Job, Message, MessageVersion
from sqlalchemy import func, select
from test_communications import outgoing


def test_admin_api_is_protected(client):
    for route in ("leads", "analytics", "integrations", f"leads/{uuid4()}"):
        assert client.get(f"/api/v1/admin/{route}").status_code == 401


def test_public_demo_is_read_only(client):
    result = client.get("/api/v1/public/demo")
    assert result.status_code == 200 and result.json()["readonly"] is True
    assert result.json()["scenarios"][0]["analysis"]["facts"]
    assert client.post("/api/v1/public/demo", json={}).status_code == 405


def test_concurrent_approval_one_send_job(authenticated, database):
    lead, msg, version, job = outgoing(database)
    with database.session.begin() as db:
        db.delete(db.get(Job, job.id))
        db.get(Message, msg.id).state = "pending_approval"
    key = str(uuid4())

    def approve(_):
        return authenticated.post(
            f"/api/v1/admin/messages/{msg.id}/decisions",
            json={"version_id": version.id, "decision": "approve"},
            headers={"Idempotency-Key": key},
        )

    with ThreadPoolExecutor(max_workers=5) as pool:
        responses = list(pool.map(approve, range(5)))
    assert all(response.status_code == 200 for response in responses)
    with database.session() as db:
        assert db.scalar(select(func.count()).select_from(Job)) == 1
        assert db.scalar(select(func.count()).select_from(Approval)) == 1


def test_edit_invalidates_approval(authenticated, database):
    lead, msg, version, job = outgoing(database)
    response = authenticated.post(
        f"/api/v1/admin/messages/{msg.id}/versions",
        json={
            "subject": "Edited",
            "body": "New synthetic text",
            "recipient": lead.email,
            "expected_version": 1,
        },
    )
    assert response.status_code == 201
    with database.session() as db:
        row = db.get(Message, msg.id)
        assert row.approved_version_id is None and row.state == "pending_approval"
        assert db.get(MessageVersion, version.id).body == "Only synthetic text"
    assert (
        authenticated.post(
            f"/api/v1/admin/messages/{msg.id}/decisions",
            json={"version_id": version.id, "decision": "approve"},
            headers={"Idempotency-Key": str(uuid4())},
        ).status_code
        == 409
    )


def test_analytics_counts_database(authenticated, database):
    outgoing(database)
    result = authenticated.get("/api/v1/admin/analytics").json()
    assert result["total"] == 1
    assert result["temperatures"]["unassessed"] == 1
    assert result["won_conversion"]["denominator"] == 1
