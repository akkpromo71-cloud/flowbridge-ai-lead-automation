"""Free isolated PostgreSQL/MockTransport tests; never call OpenAI or mail."""

import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import httpx
import pytest
from app import controlled_draft_smoke as smoke
from app.adapters.ai import PreparedDraft
from app.adapters.draft import FakeDraftAdapter
from app.domain.analysis import AnalysisFacts
from app.domain.config import load_business_config
from app.domain.scoring import score
from app.models import (
    Analysis,
    Approval,
    IntegrationState,
    Job,
    JobStep,
    Lead,
    Message,
    MessageVersion,
    utcnow,
)
from app.settings import ROOT
from sqlalchemy import func, select


@pytest.fixture
def prepared(database, settings, monkeypatch):
    monkeypatch.setattr(
        smoke, "controlled_settings", lambda: SimpleNamespace(database_url=settings.database_url)
    )
    fixture = smoke._fixture()
    config = load_business_config(ROOT / "config/automation.yaml")
    facts = AnalysisFacts.model_validate(
        {
            "service_fit": "fit",
            "service_id": "lead_automation",
            "service_evidence": ["Нужна автоматизация приёма и анализа входящих заявок"],
            "intent": "proposal",
            "intent_evidence": ["Пришлите предложение по такому проекту"],
            "urgency": "within_90_days",
            "urgency_evidence": ["Начать работу хотим через 45 дней"],
            "budgets": [
                {
                    "minimum": "460000",
                    "maximum": "460000",
                    "currency": "KZT",
                    "period": "one_time",
                    "purpose": "services",
                    "service_id": "lead_automation",
                    "evidence": ["На ваши услуги выделяем ровно 460000 KZT разово за проект"],
                }
            ],
            "business_context": {
                "value": "Мастерская печати постеров, заявки из сайта вручную переносят в таблицу",
                "evidence": [
                    "Мы — мастерская печати постеров",
                    "координатор вручную переносит обращения в таблицу",
                ],
            },
            "desired_outcome": {
                "value": "Автоматизировать входящие заявки и сократить повторный ввод",
                "evidence": [
                    "Нужна автоматизация приёма и анализа входящих заявок",
                    "Хотим сократить повторный ввод и быстрее разбирать обращения",
                ],
            },
            "summary": "Мастерская просит автоматизировать входящие заявки с одобрением ответов.",
            "missing_information": [],
            "contradictions": [],
            "suggested_next_action": "discuss_project",
            "language": "ru",
        }
    )
    assert score(facts, config).score == 95
    fake = FakeDraftAdapter()
    draft = fake.draft(facts, config)
    lead_id, message_id, version_id, job_id = map(lambda _: str(uuid4()), range(4))
    with database.session.begin() as db:
        db.add(IntegrationState(name="deployment", state="ready", detail={"mode": "controlled"}))
        lead = Lead(
            id=lead_id,
            reference="synthetic-draft-source",
            intake_key="synthetic-draft-source",
            payload_hash="synthetic",
            name="Synthetic Draft Client",
            email="synthetic-draft@example.com",
            original_message=fixture["source"],
            score=95,
            temperature="HOT",
            processing_status="completed",
        )
        db.add(lead)
        db.flush()
        analysis = Analysis(
            lead_id=lead.id,
            operation_key=f"{job_id}:analyze",
            facts=facts.model_dump(mode="json"),
            result=score(facts, config).model_dump(mode="json"),
            config_version=config.version,
            config_snapshot=config.model_dump(mode="json"),
            prompt_version=config.ai.prompt_version,
            schema_version="synthetic",
            model=smoke.MODEL,
            usage={"provider": "openai", "input_tokens": 10, "output_tokens": 10},
        )
        db.add(analysis)
        db.flush()
        lead.analysis_id = analysis.id
        db.add(
            Job(
                id=job_id,
                kind="lead_processing",
                lead_id=lead.id,
                dedup_key=f"intake:{lead.id}",
                status="succeeded",
                generation=1,
            )
        )
        db.add(
            JobStep(
                job_id=job_id,
                generation=1,
                step="analyze",
                status="completed",
                reserved_tokens=1000,
                reserved_at=utcnow(),
            )
        )
        message = Message(
            id=message_id,
            lead_id=lead.id,
            direction="outbound",
            kind="initial",
            state="pending_approval",
            purpose_key=f"initial:{lead.id}",
        )
        db.add(message)
        db.flush()
        db.add(
            MessageVersion(
                id=version_id,
                message_id=message.id,
                revision=1,
                recipient=lead.email,
                subject=draft.subject,
                body=draft.body,
                checksum="synthetic",
                generation=fake.last_usage,
            )
        )
        message.current_version_id = version_id
    return database, config, facts, lead_id, message_id, version_id


def _response(
    subject="Следующие шаги по проекту", body=None, *, status="completed", model=smoke.MODEL
):
    if body is None:
        body = "Здравствуйте! Спасибо за описание процесса обработки заявок. Учли обозначенный бюджет. Предлагаем обсудить текущий процесс и следующие шаги. Flowbridge"
    return httpx.Response(
        200,
        headers={"x-request-id": "req_synthetic_draft"},
        json={
            "id": "resp_synthetic_draft",
            "object": "response",
            "created_at": 1,
            "status": status,
            "error": None,
            "incomplete_details": None,
            "model": model,
            "output": [
                {
                    "id": "msg_synthetic_draft",
                    "type": "message",
                    "status": "completed",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "output_text",
                            "text": json.dumps({"subject": subject, "body": body}),
                            "annotations": [],
                        }
                    ],
                }
            ],
            "usage": {"input_tokens": 300, "output_tokens": 80, "total_tokens": 380},
        },
    )


@pytest.mark.postgres
def test_plan_and_reservation_are_separate_from_completed_analysis(prepared, monkeypatch):
    database, _config, _facts, lead_id, _message_id, _version_id = prepared
    hidden = Mock(side_effect=AssertionError("plan cannot read key"))
    monkeypatch.setattr(smoke, "_hidden_key", hidden)
    output = ROOT / ".local" / f"draft-plan-{uuid4()}.json"
    proposal = smoke.preflight(output)
    assert proposal["lead_id"] == lead_id
    assert proposal["cost_ceiling"] == Decimal("0.01728")
    hidden.assert_not_called()
    smoke.reserve(database, proposal)
    with pytest.raises(smoke.DraftSmokeError, match="draft_attempt_already_reserved"):
        smoke.preflight(output)
    with database.session() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(JobStep)
                .where(JobStep.step == "analyze", JobStep.reserved_tokens > 0)
            )
            == 1
        )
        assert db.get(IntegrationState, smoke.RESERVATION).state == "reserved"


@pytest.mark.postgres
def test_empty_controlled_source_is_blocked_without_key_or_new_reservation(
    database, settings, monkeypatch
):
    monkeypatch.setattr(
        smoke, "controlled_settings", lambda: SimpleNamespace(database_url=settings.database_url)
    )
    hidden = Mock(side_effect=AssertionError("Preflight cannot read key"))
    monkeypatch.setattr(smoke, "_hidden_key", hidden)
    with database.session.begin() as db:
        db.add(IntegrationState(name="deployment", state="ready", detail={"mode": "controlled"}))
    with pytest.raises(
        smoke.DraftSmokeError, match="expected_single_completed_analysis_reservation"
    ):
        smoke.preflight(ROOT / ".local" / f"draft-empty-{uuid4()}.json")
    hidden.assert_not_called()
    with database.session() as db:
        assert db.get(IntegrationState, smoke.RESERVATION) is None
        assert db.scalar(select(func.count()).select_from(Lead)) == 0
        assert db.scalar(select(func.count()).select_from(JobStep)) == 0


@pytest.mark.postgres
def test_real_adapter_mock_one_request_new_immutable_draft_no_messages_sent(
    prepared, monkeypatch, tmp_path
):
    database, _config, _facts, lead_id, message_id, source_id = prepared
    before = smoke.preflight(ROOT / ".local" / f"draft-mock-{uuid4()}.json")
    monkeypatch.setattr(smoke, "preflight", lambda path: before)
    calls = []

    def handler(request):
        calls.append(request)
        payload = json.loads(request.content)
        assert payload["model"] == smoke.MODEL
        assert payload["max_output_tokens"] == 800 and payload["store"] is False
        assert "tools" not in payload and "recipient" not in payload
        assert "460000" in request.content.decode()
        return _response()

    blocked = Mock(side_effect=AssertionError("No communication allowed"))
    for name in ["smtp_send", "telegram_notify", "sync_mailbox"]:
        monkeypatch.setattr(f"app.communication.{name}", blocked)
    result = smoke.run(
        tmp_path / "draft.json",
        max_cost_usd="0.02",
        transport=httpx.MockTransport(handler),
        key_provider=lambda: "sk-synthetic-secret-letters",
    )
    assert result["status"] == "completed" and result["requests_attempted"] == 1
    assert len(calls) == 1 and result["model"] == smoke.MODEL
    assert result["api"]["request_id"] == "req_synthetic_draft"
    assert result["api"]["response_id"] == "resp_synthetic_draft"
    assert result["estimated_cost_usd"] == "0.000248"
    assert result["content_validation"] == "passed"
    assert result["generated_body"] and result["human_approval_required"] is True
    assert result["sent"] is False and result["message_state"] == "draft"
    assert "sk-synthetic-secret-letters" not in (tmp_path / "draft.json").read_text(
        encoding="utf-8"
    )
    assert "Authorization" not in (tmp_path / "draft.json").read_text(encoding="utf-8")
    blocked.assert_not_called()
    with database.session() as db:
        message = db.get(Message, message_id)
        assert message.lead_id == lead_id and message.state == "draft"
        assert message.approved_version_id is None
        newer = db.get(MessageVersion, message.current_version_id)
        assert newer.revision == 2 and newer.generation["provider"] == "openai"
        assert newer.generation["origin_version_id"] == source_id
        assert newer.generation["model_output"] == {"subject": newer.subject, "body": newer.body}
        assert db.get(MessageVersion, source_id).generation["provider"] == "fake"
        assert db.scalar(select(func.count()).select_from(Job)) == 1
        assert db.scalar(select(func.count()).select_from(Approval)) == 0
        assert (
            db.scalar(select(func.count()).select_from(JobStep).where(JobStep.reserved_tokens > 0))
            == 1
        )


@pytest.mark.postgres
@pytest.mark.parametrize(
    "reply,expected",
    [
        (
            lambda: _response(body="Мы уже отправили ответ. Предлагаем обсудить детали."),
            "draft_content_checks_failed",
        ),
        (
            lambda: _response(
                body="Здравствуйте! Скидка гарантирована. Предлагаем обсудить задачу."
            ),
            "draft_unsupported_promise",
        ),
        (
            lambda: _response(
                body="sk-synthetic-secret-letters Предлагаем обсудить проект. Здравствуйте!"
            ),
            "draft_content_checks_failed",
        ),
        (lambda: _response(body=""), "draft_schema"),
        (
            lambda: _response(subject="Предложение по автоматизации заявок для Flowbridge"),
            "draft_sender_as_customer",
        ),
        (
            lambda: _response(
                body="Здравствуйте! Стоимость будет определена. Предлагаем обсудить задачу."
            ),
            "draft_content_checks_failed",
        ),
        (lambda: _response(model="unexpected-model"), "unexpected_model"),
        (
            lambda: httpx.Response(
                401, json={"error": {"message": "synthetic", "type": "authentication_error"}}
            ),
            "draft_provider",
        ),
        (
            lambda: httpx.Response(
                429, json={"error": {"message": "synthetic", "type": "rate_limit_error"}}
            ),
            "draft_rate_limit",
        ),
        (
            lambda: httpx.Response(
                200,
                json={
                    "id": "resp_synthetic_refusal",
                    "object": "response",
                    "created_at": 1,
                    "status": "completed",
                    "model": smoke.MODEL,
                    "output": [
                        {
                            "id": "msg_synthetic_refusal",
                            "type": "message",
                            "role": "assistant",
                            "status": "completed",
                            "content": [{"type": "refusal", "refusal": "synthetic refusal"}],
                        }
                    ],
                    "usage": {"input_tokens": 40, "output_tokens": 10, "total_tokens": 50},
                },
            ),
            "draft_refusal",
        ),
    ],
)
def test_failure_consumes_attempt_no_retry_no_unsafe_output(
    prepared, monkeypatch, tmp_path, reply, expected
):
    database, _config, _facts, _lead_id, message_id, source_id = prepared
    before = smoke.preflight(ROOT / ".local" / f"draft-fail-{uuid4()}.json")
    monkeypatch.setattr(smoke, "preflight", lambda path: before)
    calls = []

    def handler(request):
        calls.append(request)
        return reply()

    result = smoke.run(
        tmp_path / "fail.json",
        max_cost_usd="0.02",
        transport=httpx.MockTransport(handler),
        key_provider=lambda: "sk-synthetic-secret-letters",
    )
    assert result["status"] == "stopped" and result["error_code"] == expected
    assert result["requests_attempted"] == len(calls) == 1
    saved = (tmp_path / "fail.json").read_text(encoding="utf-8")
    assert "sk-synthetic-secret-letters" not in saved and "Authorization" not in saved
    assert "generated_body" not in result
    if expected == "draft_sender_as_customer":
        assert result["schema_validation"] == "passed"
        assert result["content_validation"] == "failed"
    with database.session() as db:
        assert db.get(IntegrationState, smoke.RESERVATION).state == "failed"
        assert db.get(Message, message_id).current_version_id == source_id
        assert db.scalar(select(func.count()).select_from(Approval)) == 0
    with pytest.raises(smoke.DraftSmokeError, match="draft_attempt_already_reserved"):
        smoke.reserve(database, before)


@pytest.mark.postgres
def test_ambiguous_network_timeout_never_retries(prepared, monkeypatch, tmp_path):
    database, _config, _facts, _lead_id, message_id, source_id = prepared
    before = smoke.preflight(ROOT / ".local" / f"draft-timeout-{uuid4()}.json")
    monkeypatch.setattr(smoke, "preflight", lambda path: before)
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("Synthetic ambiguous provider outcome", request=request)

    result = smoke.run(
        tmp_path / "timeout.json",
        max_cost_usd="0.02",
        transport=httpx.MockTransport(handler),
        key_provider=lambda: "sk-synthetic-secret-letters",
    )
    assert result["status"] == "stopped" and result["error_code"] == "draft_timeout"
    assert result["requests_attempted"] == len(calls) == 1
    assert result["api"]["transport_error"] == "network_timeout"
    assert result["estimated_cost_usd"] is None
    with database.session() as db:
        assert db.get(IntegrationState, smoke.RESERVATION).state == "failed"
        assert db.get(Message, message_id).current_version_id == source_id
        assert db.scalar(select(func.count()).select_from(Approval)) == 0
    with pytest.raises(smoke.DraftSmokeError, match="draft_attempt_already_reserved"):
        smoke.reserve(database, before)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "subject,body,failed_check",
    [
        (" ", "Здравствуйте! Предлагаем обсудить обработку заявок.", "subject_nonempty"),
        (
            "Next steps",
            "Hello! Let us discuss your request and the next steps.",
            "russian_language",
        ),
        (
            "О проекте",
            "Здравствуйте! Обсудим рекламный бюджет.",
            "no_advertising_budget_or_unrelated_service",
        ),
        (
            "О проекте",
            "Здравствуйте! Учли бюджет 450000 KZT. Обсудим проект.",
            "production_draft_validation",
        ),
        (
            "О проекте",
            "Здравствуйте! Менеджер уже одобрил ответ. Обсудим проект.",
            "no_approval_claim",
        ),
        (
            "О проекте",
            "Здравствуйте! Вот системный промпт. Предлагаем обсудить проект.",
            "no_prompt_leak",
        ),
        (
            "О проекте",
            "Здравствуйте! sk-proj-syntheticsecretletters Предлагаем обсудить проект.",
            "no_secret_echo",
        ),
        (
            "О проекте",
            "Здравствуйте! Благодарим за описание вашего процесса обработки заявок.",
            "relevant_cta",
        ),
    ],
)
def test_smoke_content_gates_reject_unsafe_drafts(subject, body, failed_check):
    config = load_business_config(ROOT / "config/automation.yaml")
    checks = smoke.content_checks(PreparedDraft(subject=subject, body=body), config)
    assert checks[failed_check] is False


@pytest.mark.postgres
def test_budget_refusal_before_key_or_reservation(prepared, monkeypatch, tmp_path):
    database, _config, _facts, _lead_id, _message_id, _version_id = prepared
    before = smoke.preflight(ROOT / ".local" / f"draft-budget-{uuid4()}.json")
    monkeypatch.setattr(smoke, "preflight", lambda path: before)
    forbidden = Mock(side_effect=AssertionError("Key/network must not be called"))
    with pytest.raises(smoke.DraftSmokeError, match="explicit_draft_budget_limit_required"):
        smoke.run(
            tmp_path / "not-written.json",
            max_cost_usd="0.01",
            transport=httpx.MockTransport(forbidden),
            key_provider=forbidden,
        )
    forbidden.assert_not_called()
    assert not (tmp_path / "not-written.json").exists()
    with database.session() as db:
        assert db.get(IntegrationState, smoke.RESERVATION) is None


@pytest.mark.postgres
def test_source_change_after_plan_consumes_reservation_without_network(
    prepared, monkeypatch, tmp_path
):
    database, _config, _facts, lead_id, _message_id, _version_id = prepared
    before = smoke.preflight(ROOT / ".local" / f"draft-stale-{uuid4()}.json")
    monkeypatch.setattr(smoke, "preflight", lambda path: before)
    with database.session.begin() as db:
        lead = db.get(Lead, lead_id)
        analysis = db.get(Analysis, lead.analysis_id)
        changed = dict(analysis.facts)
        changed["summary"] = "Synthetic changed summary"
        analysis.facts = changed
    calls = Mock(side_effect=AssertionError("No network after stale source"))
    result = smoke.run(
        tmp_path / "stale.json",
        max_cost_usd="0.02",
        transport=httpx.MockTransport(calls),
        key_provider=lambda: "sk-synthetic-secret-letters",
    )
    assert result["status"] == "stopped" and result["requests_attempted"] == 0
    assert result["error_code"] == "source_analysis_changed_after_reservation"
    calls.assert_not_called()
    with database.session() as db:
        assert db.get(IntegrationState, smoke.RESERVATION).state == "failed"


@pytest.mark.postgres
def test_report_redacts_key_even_from_provider_identifier(prepared, monkeypatch, tmp_path):
    _database, _config, _facts, _lead_id, _message_id, _version_id = prepared
    before = smoke.preflight(ROOT / ".local" / f"draft-redact-{uuid4()}.json")
    monkeypatch.setattr(smoke, "preflight", lambda path: before)

    def handler(_request):
        response = _response()
        response.headers["x-request-id"] = "sk-synthetic-secret-letters"
        return response

    result = smoke.run(
        tmp_path / "redact.json",
        max_cost_usd="0.02",
        transport=httpx.MockTransport(handler),
        key_provider=lambda: "sk-synthetic-secret-letters",
    )
    assert result["status"] == "completed"
    assert result["api"]["request_id"] == "[REDACTED]"
    saved = (tmp_path / "redact.json").read_text(encoding="utf-8")
    assert "sk-synthetic-secret-letters" not in saved


def test_fake_provider_path_and_content_review(prepared):
    _database, config, facts, *_ = prepared
    draft = FakeDraftAdapter().draft(facts, config)
    assert all(smoke.content_checks(draft, config).values())
    assert not smoke.content_checks(
        PreparedDraft(subject="Тема", body="Здравствуйте, менеджер одобрил ответ. Обсудим задачу."),
        config,
    )["no_approval_claim"]
