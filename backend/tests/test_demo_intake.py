"""Server-side demo permissions on the explicitly isolated PostgreSQL test DB."""

from datetime import timedelta
from uuid import uuid4

import pytest
from app.main import create_app
from app.models import Job, Lead, Operator, Session, utcnow
from app.security import password_hasher
from fastapi.testclient import TestClient
from sqlalchemy import func, select

PAYLOAD = {
    "name": "Demo auth regression",
    "email": "demo-auth-regression@example.com",
    "message": "Synthetic enquiry for an isolated authorization regression.",
}


@pytest.fixture
def demo_client(database, settings):
    # Only the trusted server mode changes; DB stays the guarded test DB.
    with TestClient(create_app(settings.model_copy(update={"mode": "demo"}))) as client:
        yield client


def lead_counts(database):
    with database.session() as db:
        return (
            db.scalar(select(func.count()).select_from(Lead)),
            db.scalar(select(func.count()).select_from(Job)),
        )


def login(client, database):
    with database.session.begin() as db:
        db.add(
            Operator(
                email="operator@example.com",
                display_name="Synthetic operator",
                password_hash=password_hasher.hash("synthetic-demo-auth-password"),
            )
        )
    result = client.post(
        "/api/v1/auth/login",
        json={"email": "operator@example.com", "password": "synthetic-demo-auth-password"},
    )
    assert result.status_code == 200
    return result.json()["csrf_token"]


def test_public_demo_scenarios_remain_readonly_without_login(demo_client, database):
    response = demo_client.get("/api/v1/public/demo")
    assert response.status_code == 200
    assert response.json()["readonly"] is True
    assert response.json()["scenarios"]
    assert demo_client.post("/api/v1/public/demo", json={}).status_code == 405
    assert lead_counts(database) == (0, 0)


@pytest.mark.parametrize("suffix", ["", "?mode=live"])
def test_direct_unauthenticated_demo_intake_cannot_create_lead_or_job(
    demo_client, database, suffix
):
    response = demo_client.post(
        "/api/v1/public/leads" + suffix,
        json=PAYLOAD,
        headers={"Idempotency-Key": str(uuid4()), "X-Mode": "live"},
    )
    assert response.status_code == 401
    assert lead_counts(database) == (0, 0)


def test_request_body_cannot_choose_server_mode(demo_client, database):
    response = demo_client.post(
        "/api/v1/public/leads",
        json={**PAYLOAD, "mode": "live"},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert response.status_code == 422
    assert lead_counts(database) == (0, 0)


def test_authorized_demo_intake_preserves_csrf_origin_and_acceptance(demo_client, database):
    csrf = login(demo_client, database)
    headers = {"Idempotency-Key": str(uuid4())}
    assert (
        demo_client.post("/api/v1/public/leads", json=PAYLOAD, headers=headers).status_code == 403
    )
    assert lead_counts(database) == (0, 0)
    headers["X-CSRF-Token"] = csrf
    assert (
        demo_client.post(
            "/api/v1/public/leads",
            json=PAYLOAD,
            headers={**headers, "Origin": "https://untrusted.example"},
        ).status_code
        == 403
    )
    assert lead_counts(database) == (0, 0)
    response = demo_client.post(
        "/api/v1/public/leads",
        json=PAYLOAD,
        headers={**headers, "Origin": "http://127.0.0.1:5173"},
    )
    assert response.status_code == 202
    assert set(response.json()) == {"reference", "status"}
    assert response.json()["status"] == "accepted"
    assert lead_counts(database) == (1, 1)


def test_expired_operator_session_cannot_create_demo_lead(demo_client, database):
    csrf = login(demo_client, database)
    with database.session.begin() as db:
        db.scalar(select(Session)).expires_at = utcnow() - timedelta(seconds=1)
    response = demo_client.post(
        "/api/v1/public/leads",
        json=PAYLOAD,
        headers={"Idempotency-Key": str(uuid4()), "X-CSRF-Token": csrf},
    )
    assert response.status_code == 401
    assert lead_counts(database) == (0, 0)


def test_demo_cabinet_stays_closed_without_login(demo_client):
    for route in ("leads", "analytics", "integrations"):
        assert demo_client.get(f"/api/v1/admin/{route}").status_code == 401


def test_public_live_intake_contract_uses_only_isolated_db(database, settings, monkeypatch):
    def forbidden_provider(*args, **kwargs):
        pytest.fail("Intake must not call an external provider")

    for target in (
        "app.processing.OpenAIAdapter",
        "app.processing.smtp_send",
        "app.processing.telegram_notify",
        "app.communication.imaplib.IMAP4_SSL",
    ):
        monkeypatch.setattr(target, forbidden_provider)
    # Exercise the live route branch in-process, without provisioning a live
    # deployment or credentials. Only the existing isolated test DB is used.
    live_settings = settings.model_copy(update={"mode": "live"})
    assert live_settings.database_url == settings.database_url
    assert not live_settings.allow_external_sends
    with TestClient(create_app(live_settings)) as client:
        headers = {"Idempotency-Key": str(uuid4()), "Origin": "http://127.0.0.1:5173"}
        first = client.post("/api/v1/public/leads", json=PAYLOAD, headers=headers)
        repeated = client.post("/api/v1/public/leads", json=PAYLOAD, headers=headers)
        assert first.status_code == repeated.status_code == 202
        assert first.json() == repeated.json()
        assert set(first.json()) == {"reference", "status"}
        assert client.get("/api/v1/admin/leads").status_code == 401
    assert lead_counts(database) == (1, 1)
