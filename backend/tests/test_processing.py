from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest.mock import Mock
from uuid import uuid4

import pytest
from app.adapters.ai import AIError
from app.communication import CommunicationError, check_followups, sync_mailbox
from app.domain.config import load_business_config
from app.domain.examples import labelled_examples
from app.intake import accept_lead
from app.jobs import JobConflict, claim_step, dispatch_claim
from app.models import Analysis, Audit, Job, JobStep, Lead, Message, MessageVersion, utcnow
from app.processing import _analyze, execute_step, reserve_ai, settle_usage
from app.schemas import LeadInput
from sqlalchemy import func, select

pytestmark = pytest.mark.postgres


def create_lead_job(database, settings, example_id="hot_ru"):
    config = load_business_config(settings.business_config)
    example = next(x for x in labelled_examples(config) if x.id == example_id)
    accept_lead(
        database,
        config,
        LeadInput(name="Demo Client", email="demo@example.com", message=example.message),
        str(uuid4()),
    )
    return config, dispatch_claim(database, settings)


def process(database, settings, config, job):
    for step in ["analyze", "draft", "finish"]:
        execute_step(database, settings, config, job.id, job.generation, step)


def test_synthetic_pipeline_persists_analysis_draft_history_and_hot_notification(
    database, settings
):
    config, job = create_lead_job(database, settings)
    process(database, settings, config, job)
    with database.session() as db:
        lead = db.get(Lead, job.lead_id)
        assert lead.score == 92 and lead.temperature == "HOT"
        assert lead.processing_status == "completed"
        assert lead.sales_stage == "new"
        analysis = db.get(Analysis, lead.analysis_id)
        assert analysis.config_snapshot == config.model_dump(mode="json")
        assert analysis.model == "fake-v1"
        assert analysis.usage["input_tokens"] == 0
        message = db.scalar(select(Message))
        assert message.state == "pending_approval"
        assert message.approved_version_id is None
        version = db.get(MessageVersion, message.current_version_id)
        assert version.recipient == "demo@example.com"
        assert "300000" not in version.body
        assert db.get(Job, job.id).status == "succeeded"
        assert (
            db.scalar(select(func.count()).select_from(Job).where(Job.kind == "notification")) == 1
        )
        assert db.scalar(select(func.count()).select_from(Audit)) >= 3


def test_unknown_fit_keeps_null_score_and_review_state(database, settings):
    config, job = create_lead_job(database, settings, "ambiguous_ru")
    process(database, settings, config, job)
    with database.session() as db:
        lead = db.get(Lead, job.lead_id)
        assert lead.processing_status == "needs_review"
        assert lead.score is None and lead.temperature is None
        assert db.scalar(select(Message)).state == "pending_approval"
        assert (
            db.scalar(select(func.count()).select_from(Job).where(Job.kind == "notification")) == 0
        )


def test_replay_analysis_does_not_duplicate_records_or_notification(database, settings):
    config, job = create_lead_job(database, settings)
    first = execute_step(database, settings, config, job.id, job.generation, "analyze")
    second = execute_step(database, settings, config, job.id, job.generation, "analyze")
    assert first["analysis_id"] == second["analysis_id"]
    assert second["replayed"] is True
    with database.session() as db:
        assert db.scalar(select(func.count()).select_from(Analysis)) == 1
        assert (
            db.scalar(select(func.count()).select_from(Job).where(Job.kind == "notification")) == 1
        )


def test_ai_failure_is_explicit_no_fake_success_no_cold(database, settings, monkeypatch):
    config, job = create_lead_job(database, settings)
    monkeypatch.setattr(
        "app.processing.FakeAIAdapter.analyze", Mock(side_effect=AIError("ai_schema"))
    )
    with pytest.raises(AIError, match="ai_schema"):
        execute_step(database, settings, config, job.id, job.generation, "analyze")
    with database.session() as db:
        lead = db.get(Lead, job.lead_id)
        assert lead.processing_status == "needs_review"
        assert lead.score is None and lead.temperature is None
        assert db.get(Job, job.id).status == "failed"
        assert db.scalar(select(func.count()).select_from(Analysis)) == 0


def test_draft_reanalysis_does_not_overwrite_existing_manager_text(database, settings):
    config, job = create_lead_job(database, settings, "minimal_fit")
    process(database, settings, config, job)
    with database.session.begin() as db:
        message = db.scalar(select(Message))
        version = db.get(MessageVersion, message.current_version_id)
        # The manager's edit is a new immutable revision, not a rewrite of v1.
        revision = MessageVersion(
            message_id=message.id,
            revision=2,
            recipient=version.recipient,
            subject=version.subject,
            body="Manager-approved synthetic wording",
            checksum="synthetic",
        )
        db.add(revision)
        db.flush()
        original_id = revision.id
        message.current_version_id = revision.id
        reanalysis = Job(
            kind="lead_processing",
            lead_id=job.lead_id,
            dedup_key="reanalyze:test",
            config_snapshot=config.model_dump(mode="json"),
        )
        db.add(reanalysis)
    newer = dispatch_claim(database, settings)
    process(database, settings, config, newer)
    with database.session() as db:
        assert db.scalar(select(func.count()).select_from(Message)) == 1
        assert db.get(MessageVersion, original_id).body == "Manager-approved synthetic wording"


def test_demo_outbound_is_simulated_and_recorded_only_after_send_gate(database, settings):
    config, job = create_lead_job(database, settings, "minimal_fit")
    process(database, settings, config, job)
    with database.session.begin() as db:
        message = db.scalar(select(Message))
        message.state = "queued"
        message.approved_version_id = message.current_version_id
        db.add(
            Job(
                kind="email_send",
                lead_id=job.lead_id,
                message_id=message.id,
                dedup_key=f"send:{message.id}:{message.current_version_id}",
                config_snapshot=config.model_dump(mode="json"),
            )
        )
        message_id = message.id
    sending = dispatch_claim(database, settings)
    result = execute_step(database, settings, config, sending.id, sending.generation, "send")
    assert result["provider"] == "fake"
    execute_step(database, settings, config, sending.id, sending.generation, "finish")
    with database.session() as db:
        assert db.get(Message, message_id).state == "provider_accepted"
        lead = db.get(Lead, job.lead_id)
        assert lead.sales_stage == "contacted"
        assert lead.followup_status == "scheduled"


def test_missing_approval_blocks_send_before_adapter(database, settings, monkeypatch):
    config, job = create_lead_job(database, settings, "minimal_fit")
    process(database, settings, config, job)
    with database.session.begin() as db:
        message = db.scalar(select(Message))
        db.add(
            Job(
                kind="email_send",
                lead_id=job.lead_id,
                message_id=message.id,
                dedup_key=f"send:{message.id}:{message.current_version_id}",
                config_snapshot=config.model_dump(mode="json"),
            )
        )
    sending = dispatch_claim(database, settings)
    adapter = Mock(side_effect=AssertionError("must not send"))
    monkeypatch.setattr("app.processing.smtp_send", adapter)
    with pytest.raises(CommunicationError, match="approval_not_current"):
        execute_step(database, settings, config, sending.id, sending.generation, "send")
    adapter.assert_not_called()


def test_budget_exhaustion_defers_without_call_or_attempt_consumption(database, settings):
    config, job = create_lead_job(database, settings)
    claim = claim_step(database, job.id, job.generation, "analyze", settings)
    restricted = settings.model_copy(update={"ai_daily_token_budget": 1})
    with pytest.raises(AIError, match="ai_budget_exhausted"):
        reserve_ai(database, restricted, job.id, job.generation, claim.token, "synthetic", config)
    with database.session() as db:
        saved = db.get(Job, job.id)
        assert saved.status == "retry_wait" and saved.attempts == 0
        assert saved.next_attempt_at.date() == (utcnow() + timedelta(days=1)).date()
        assert db.scalar(select(JobStep)).reserved_tokens == 0
        assert db.get(Lead, job.lead_id).processing_status == "pending"


def test_parallel_ai_reservation_allows_only_configured_count(database, settings):
    config, first = create_lead_job(database, settings)
    _, second = create_lead_job(database, settings)
    first_claim = claim_step(database, first.id, first.generation, "analyze", settings)
    second_claim = claim_step(database, second.id, second.generation, "analyze", settings)
    restricted = settings.model_copy(
        update={"ai_max_parallel": 1, "ai_daily_token_budget": 1_000_000}
    )
    barrier = Barrier(2)

    def reserve(pair):
        job, claim = pair
        barrier.wait()
        try:
            return reserve_ai(
                database, restricted, job.id, job.generation, claim.token, "synthetic", config
            )
        except AIError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(reserve, [(first, first_claim), (second, second_claim)]))
    assert sum(isinstance(item, int) for item in results) == 1
    assert "ai_parallel_limit" in results


def test_unknown_usage_retains_full_reservation_known_usage_settles(database, settings):
    config, job = create_lead_job(database, settings)
    claim = claim_step(database, job.id, job.generation, "analyze", settings)
    reserved = reserve_ai(
        database, settings, job.id, job.generation, claim.token, "synthetic", config
    )
    settle_usage(database, job.id, job.generation, {"input_tokens": None, "output_tokens": None})
    with database.session() as db:
        assert db.scalar(select(JobStep)).reserved_tokens == reserved
    settle_usage(database, job.id, job.generation, {"input_tokens": 100, "output_tokens": 50})
    with database.session() as db:
        assert db.scalar(select(JobStep)).reserved_tokens == 150


def test_internal_routes_reject_unauthenticated_requests(client):
    endpoint = f"/internal/v1/jobs/{uuid4()}/steps/analyze"
    assert client.post(endpoint, json={"generation": 1}).status_code == 401
    assert client.post("/internal/v1/mailboxes/default/sync").status_code == 401
    assert client.post("/internal/v1/followups/check").status_code == 401


def test_stale_analysis_start_cannot_reset_completed_lead(database, settings):
    config, job = create_lead_job(database, settings)
    claim = claim_step(database, job.id, job.generation, "analyze", settings)
    with database.session.begin() as db:
        db.get(Job, job.id).generation += 1
        db.get(Lead, job.lead_id).processing_status = "completed"
    with pytest.raises(JobConflict, match="stale_analysis_start"):
        _analyze(database, settings, job, config, claim)
    with database.session() as db:
        assert db.get(Lead, job.lead_id).processing_status == "completed"


def test_existing_followup_job_is_not_counted_as_new_or_resurrected(database, settings):
    config, job = create_lead_job(database, settings)
    sync_mailbox(database, settings)
    with database.session.begin() as db:
        lead = db.get(Lead, job.lead_id)
        lead.followup_status = "scheduled"
        lead.followup_due_at = utcnow() - timedelta(minutes=1)
        db.add(
            Job(
                kind="followup_prepare",
                lead_id=lead.id,
                dedup_key=f"followup:{lead.id}",
                status="cancelled",
                config_snapshot=config.model_dump(mode="json"),
            )
        )
    assert check_followups(database, config) == {"prepared": 0, "needs_review": 1}
    with database.session() as db:
        assert db.get(Lead, job.lead_id).followup_status == "needs_review"
        assert (
            db.scalar(select(func.count()).select_from(Job).where(Job.kind == "followup_prepare"))
            == 1
        )
