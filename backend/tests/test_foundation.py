from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from app.models import Job, Lead
from app.settings import Settings
from pydantic import ValidationError
from sqlalchemy import func, select

PAYLOAD = {
    "name": "Анна Тестовая",
    "email": "anna@example.com",
    "message": "Нужна автоматизация обработки заявок.",
}


def send(client, key=None, payload=None):
    return client.post(
        "/api/v1/public/leads",
        json=payload or PAYLOAD,
        headers={"Idempotency-Key": key or str(uuid4())},
    )


def test_atomic_safe_acceptance(client, database):
    response = send(client)
    assert response.status_code == 202
    assert set(response.json()) == {"reference", "status"}
    with database.session() as db:
        lead, job = db.scalar(select(Lead)), db.scalar(select(Job))
        assert lead.id == job.lead_id and lead.processing_status == "pending"


def test_concurrent_intake_same_key(client, database):
    key = str(uuid4())
    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(lambda _: send(client, key), range(8)))
    assert all(r.status_code == 202 for r in responses)
    assert len({r.json()["reference"] for r in responses}) == 1
    with database.session() as db:
        assert db.scalar(select(func.count()).select_from(Lead)) == 1
        assert db.scalar(select(func.count()).select_from(Job)) == 1


def test_conflicting_key_and_same_email_new_request(client, database):
    key = str(uuid4())
    assert send(client, key).status_code == 202
    assert (
        send(client, key, {**PAYLOAD, "message": "Теперь нужна другая интеграция."}).status_code
        == 409
    )
    assert (
        send(client, payload={**PAYLOAD, "message": "Теперь нужна другая интеграция."}).status_code
        == 202
    )
    with database.session() as db:
        assert db.scalar(select(func.count()).select_from(Lead)) == 2


def test_invalid_never_creates_job(client, database):
    assert send(client, payload={**PAYLOAD, "email": "bad-email"}).status_code == 422
    assert send(client, payload={**PAYLOAD, "utm": {"secret": "invalid"}}).status_code == 422
    assert client.post("/api/v1/public/leads", json=PAYLOAD).status_code == 422
    with database.session() as db:
        assert db.scalar(select(func.count()).select_from(Job)) == 0


def test_body_limits_and_control_characters(client):
    assert client.post("/api/v1/public/leads", content=b"x" * 32769).status_code == 413
    assert send(client, payload={**PAYLOAD, "name": "Anne\nBcc: stolen"}).status_code == 422


def test_demo_still_requires_auth(client):
    assert client.get("/api/v1/auth/me").status_code == 401
    assert client.post("/api/v1/auth/logout").status_code == 401


def test_sessions_csrf_and_logout(authenticated):
    assert authenticated.get("/api/v1/auth/me").status_code == 200
    csrf = authenticated.headers.pop("X-CSRF-Token")
    assert authenticated.post("/api/v1/auth/logout").status_code == 403
    authenticated.headers["X-CSRF-Token"] = csrf
    assert (
        authenticated.post(
            "/api/v1/auth/logout", headers={"Origin": "https://evil.example"}
        ).status_code
        == 403
    )
    assert authenticated.post("/api/v1/auth/logout").status_code == 204
    assert authenticated.get("/api/v1/auth/me").status_code == 401


def test_login_limit_and_no_password_echo(client, monkeypatch):
    from app.models import utcnow

    fixed_time = utcnow()
    monkeypatch.setattr("app.security.utcnow", lambda: fixed_time)
    payload = {"email": "absent@example.com", "password": "private-value"}
    for _ in range(5):
        response = client.post("/api/v1/auth/login", json=payload)
        assert response.status_code == 401 and "private-value" not in response.text
    assert client.post("/api/v1/auth/login", json=payload).status_code == 429


def test_mode_guards():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, mode="demo", openai_api_key="not-a-real-key")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, mode="live")


def test_public_config_does_not_leak_business_rules(client):
    result = client.get("/api/v1/public/config").json()
    assert not ({"scoring", "approved_facts", "ai", "internal_token"} & set(result))
