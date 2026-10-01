"""Real PostgreSQL/HTTP/processing paths; external transports stay synthetic."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest.mock import MagicMock, Mock
from uuid import uuid4

import httpx
import pytest
from app.adapters.ai import AIError
from app.domain.config import load_business_config
from app.jobs import JobConflict, dispatch_claim
from app.models import Analysis, Approval, Audit, Job, JobStep, Lead, Message, utcnow
from app.processing import execute_step
from openai import OpenAI
from pydantic import SecretStr
from sqlalchemy import func, select
from test_communications import outgoing
from test_processing import create_lead_job

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize("concurrent", [False, True], ids=["sequential", "concurrent"])
def test_distinct_approval_keys_send_once_and_execution_replays(
    authenticated, database, settings, monkeypatch, concurrent
):
    lead, message, version, initial_job = outgoing(database)
    with database.session.begin() as db:
        db.delete(db.get(Job, initial_job.id))
        saved_message = db.get(Message, message.id)
        saved_message.state = "pending_approval"
        saved_message.approved_version_id = None

    endpoint = f"/api/v1/admin/messages/{message.id}/decisions"
    payload = {"version_id": version.id, "decision": "approve"}
    keys = [str(uuid4()) for _ in range(3)]
    barrier = Barrier(len(keys)) if concurrent else None

    def approve(key):
        if barrier:
            barrier.wait(timeout=10)
        return authenticated.post(endpoint, json=payload, headers={"Idempotency-Key": key})

    if concurrent:
        with ThreadPoolExecutor(max_workers=len(keys)) as executor:
            responses = list(executor.map(approve, keys))
    else:
        responses = [approve(key) for key in keys]
    assert [response.status_code for response in responses] == [200] * len(keys)
    assert len({response.json()["id"] for response in responses}) == len(keys)
    with database.session() as db:
        assert db.scalar(select(func.count()).select_from(Approval)) == len(keys)
        assert db.scalar(select(func.count()).select_from(Job)) == 1
        saved_message = db.get(Message, message.id)
        assert saved_message.state == "queued"
        assert saved_message.approved_version_id == version.id

    # Enter the real SMTP adapter branch, but replace its network transport completely.
    live = settings.model_copy(
        update={
            "mode": "live",
            "allow_external_sends": True,
            "smtp_host": "smtp.invalid",
            "smtp_from": "sender@example.com",
            "smtp_username": "synthetic-user",
            "smtp_password": SecretStr("synthetic-password"),
        }
    )
    smtp_constructor = MagicMock()
    server = smtp_constructor.return_value.__enter__.return_value

    def accept_once(email, *, from_addr, to_addrs):
        assert database.engine.pool.checkedout() == 0
        assert email["To"] == version.recipient == lead.email
        assert email["Subject"] == version.subject
        assert email.get_content().strip() == version.body
        assert from_addr == "sender@example.com"
        assert to_addrs == [version.recipient]
        return {}

    server.send_message.side_effect = accept_once
    monkeypatch.setattr("app.communication.smtplib.SMTP", smtp_constructor)
    config = load_business_config(settings.business_config)
    sending = dispatch_claim(database, live)
    assert sending is not None and sending.kind == "email_send"
    first = execute_step(database, live, config, sending.id, sending.generation, "send")
    assert first == {"status": "completed", "replayed": False, "provider": "smtp", "accepted": True}
    replay = execute_step(database, live, config, sending.id, sending.generation, "send")
    assert replay == {**first, "replayed": True}
    execute_step(database, live, config, sending.id, sending.generation, "finish")
    assert execute_step(database, live, config, sending.id, sending.generation, "send") == replay
    # An old decision key replays; a new approval after acceptance cannot queue another send.
    assert (
        authenticated.post(endpoint, json=payload, headers={"Idempotency-Key": keys[0]}).status_code
        == 200
    )
    assert (
        authenticated.post(
            endpoint, json=payload, headers={"Idempotency-Key": str(uuid4())}
        ).status_code
        == 409
    )
    assert dispatch_claim(database, live) is None
    smtp_constructor.assert_called_once()
    server.send_message.assert_called_once()
    with database.session() as db:
        assert db.scalar(select(func.count()).select_from(Job)) == 1
        assert db.get(Job, sending.id).status == "succeeded"
        assert db.get(Message, message.id).state == "provider_accepted"
        assert (
            db.scalar(
                select(func.count())
                .select_from(Audit)
                .where(Audit.kind == "message.provider_accepted")
            )
            == 1
        )


@pytest.mark.parametrize("job_limit,external_limit", [(5, 3), (2, 2)])
def test_processing_timeouts_use_real_sdk_once_per_attempt_then_fail_without_fake(
    database, settings, monkeypatch, job_limit, external_limit
):
    config, job = create_lead_job(database, settings, "minimal_fit")
    live = settings.model_copy(
        update={
            "mode": "live",
            "openai_api_key": SecretStr("synthetic-key-never-sent"),
            "max_job_attempts": job_limit,
            "ai_daily_token_budget": 1_000_000,
        }
    )
    clock = [utcnow()]
    monkeypatch.setattr("app.processing.utcnow", lambda: clock[0])
    monkeypatch.setattr("app.jobs.utcnow", lambda: clock[0])
    requests = []
    clients = []

    def timeout(request):
        assert request.method == "POST" and request.url.path == "/v1/responses"
        assert request.url.host == "provider.invalid"
        assert database.engine.pool.checkedout() == 0
        requests.append(request)
        raise httpx.ReadTimeout("Synthetic provider timeout", request=request)

    def provider_client(**kwargs):
        assert kwargs["max_retries"] == 0
        transport = httpx.MockTransport(timeout)
        sdk = OpenAI(
            **kwargs,
            base_url="https://provider.invalid/v1",
            http_client=httpx.Client(transport=transport),
        )
        clients.append(sdk)
        return sdk

    constructor = Mock(side_effect=provider_client)
    monkeypatch.setattr("app.adapters.ai.OpenAI", constructor)
    fake = Mock(side_effect=AssertionError("A live timeout must not fall back to fake"))
    monkeypatch.setattr("app.processing.FakeAIAdapter", fake)
    original_id = job.id
    try:
        for attempt in range(1, external_limit + 1):
            assert job.id == original_id and job.generation == attempt
            with pytest.raises(AIError, match="^ai_timeout$"):
                execute_step(database, live, config, job.id, job.generation, "analyze")
            assert len(requests) == constructor.call_count == attempt
            with database.session() as db:
                saved_job = db.get(Job, job.id)
                lead = db.get(Lead, job.lead_id)
                steps = db.scalars(select(JobStep).order_by(JobStep.generation)).all()
                assert saved_job.attempts == attempt
                assert saved_job.error_code == "ai_timeout"
                assert len(steps) == attempt
                assert all(step.reserved_tokens > 0 and step.reserved_at for step in steps)
                assert all(step.error_code == "ai_timeout" for step in steps)
                assert all(step.result == {} for step in steps)
                assert lead.score is None and lead.temperature is None and lead.analysis_id is None
                assert db.scalar(select(func.count()).select_from(Analysis)) == 0
                assert db.scalar(select(func.count()).select_from(Message)) == 0
                if attempt < external_limit:
                    assert saved_job.status == "retry_wait"
                    assert lead.processing_status == "pending"
                    assert steps[-1].status == "retryable"
                    assert dispatch_claim(database, live, now=clock[0]) is None
                    clock[0] = saved_job.next_attempt_at + timedelta(milliseconds=1)
                else:
                    assert saved_job.status == "failed"
                    assert lead.processing_status == "failed"
                    assert steps[-1].status == "failed"
            if attempt < external_limit:
                job = dispatch_claim(database, live, now=clock[0])
                assert job is not None
        assert dispatch_claim(database, live, now=clock[0] + timedelta(days=1)) is None
        with pytest.raises(JobConflict, match="step_failed"):
            execute_step(database, live, config, job.id, job.generation, "analyze")
        assert len(requests) == constructor.call_count == external_limit
        fake.assert_not_called()
    finally:
        for sdk in clients:
            sdk.close()


def test_authenticated_internal_extra_fields_rejected_before_claim(client, database, settings):
    config, job = create_lead_job(database, settings, "minimal_fit")
    endpoint = f"/internal/v1/jobs/{job.id}/steps/analyze"
    headers = {"X-Internal-Token": settings.internal_token.get_secret_value()}
    rejected = client.post(
        endpoint,
        headers=headers,
        json={"generation": job.generation, "approved": True, "score": 100},
    )
    assert rejected.status_code == 422
    assert {(tuple(error["loc"]), error["type"]) for error in rejected.json()["detail"]} == {
        (("body", "approved"), "extra_forbidden"),
        (("body", "score"), "extra_forbidden"),
    }
    with database.session() as db:
        assert db.scalar(select(func.count()).select_from(JobStep)) == 0
        assert db.scalar(select(func.count()).select_from(Analysis)) == 0
        assert db.get(Job, job.id).status == "dispatching"
        assert db.get(Lead, job.lead_id).processing_status == "pending"
    valid = client.post(endpoint, headers=headers, json={"generation": job.generation})
    assert valid.status_code == 200 and valid.json()["status"] == "completed"
    with database.session() as db:
        assert db.scalar(select(func.count()).select_from(JobStep)) == 1
