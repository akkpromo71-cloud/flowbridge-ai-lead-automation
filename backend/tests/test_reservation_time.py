from datetime import timedelta

import pytest
from app.adapters.ai import AIError
from app.jobs import claim_step
from app.metrics import technical_metrics
from app.models import Analysis, Job, JobStep, utcnow
from app.processing import reserve_ai
from sqlalchemy import select
from test_processing import create_lead_job

pytestmark = pytest.mark.postgres


def claimed_at(database, settings, when):
    config, job = create_lead_job(database, settings)
    with database.session.begin() as db:
        db.get(Job, job.id).lease_until = when + timedelta(minutes=3)
    claim = claim_step(database, job.id, job.generation, "analyze", settings, when)
    with database.session.begin() as db:
        step = db.scalar(select(JobStep).where(JobStep.job_id == job.id))
        step.created_at = when
    return config, job, claim


def test_budget_uses_reservation_day_when_claim_crosses_midnight(database, settings, monkeypatch):
    midnight = utcnow().replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    first_claim_time = midnight - timedelta(seconds=1)
    reservation_time = midnight + timedelta(seconds=1)
    config, first, first_claim = claimed_at(database, settings, first_claim_time)
    monkeypatch.setattr("app.processing.utcnow", lambda: reservation_time)
    reserved = reserve_ai(
        database, settings, first.id, first.generation, first_claim.token, "synthetic", config
    )
    with database.session() as db:
        step = db.scalar(select(JobStep).where(JobStep.job_id == first.id))
        assert step.created_at < midnight
        assert step.reserved_at == reservation_time
        metrics = technical_metrics(db, midnight, settings)
        assert metrics["ai_calls"] == 1 and metrics["unknown_usage_calls"] == 1
        assert metrics["estimated_cost"] is None
    config, second, second_claim = claimed_at(database, settings, reservation_time)
    limited = settings.model_copy(update={"ai_daily_token_budget": reserved})
    with pytest.raises(AIError, match="ai_budget_exhausted"):
        reserve_ai(
            database, limited, second.id, second.generation, second_claim.token, "synthetic", config
        )


def test_metrics_keep_known_usage_in_call_period_not_completion_period(database, settings):
    midnight = utcnow().replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    _config, job, _claim = claimed_at(database, settings, midnight - timedelta(seconds=1))
    with database.session.begin() as db:
        step = db.scalar(select(JobStep).where(JobStep.job_id == job.id))
        step.reserved_tokens = 1100
        step.reserved_at = midnight - timedelta(seconds=1)
        step.status = "completed"
        db.add(
            Analysis(
                lead_id=job.lead_id,
                operation_key=f"{job.id}:analyze",
                facts={},
                result={},
                config_version="test",
                config_snapshot={},
                prompt_version="test",
                schema_version="test",
                model="synthetic",
                created_at=midnight + timedelta(seconds=2),
                usage={"provider": "openai", "input_tokens": 1000, "output_tokens": 100},
            )
        )
    with database.session() as db:
        today = technical_metrics(db, midnight, settings)
        assert today["ai_calls"] == today["input_tokens"] == today["output_tokens"] == 0
        full = technical_metrics(db, midnight - timedelta(days=1), settings)
        assert full["ai_calls"] == 1 and full["input_tokens"] == 1000
        assert full["output_tokens"] == 100 and full["unknown_usage_calls"] == 0
