from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest.mock import Mock

import httpx
import pytest
from app.jobs import (
    JobConflict,
    acknowledge_dispatch,
    claim_step,
    complete_step,
    dispatch_claim,
    fail_step,
    recover,
)
from app.models import Audit, Job, JobStep, Message, utcnow
from app.worker import run_once
from sqlalchemy import func, select

pytestmark = pytest.mark.postgres


def add_job(database, kind="lead_processing", message_id=None):
    with database.session.begin() as db:
        job = Job(kind=kind, dedup_key="synthetic-job", message_id=message_id)
        db.add(job)
        db.flush()
        return job.id


def test_concurrent_dispatch_only_one_owns_job(database, settings):
    add_job(database)
    barrier = Barrier(2)

    def dispatch():
        barrier.wait()
        return dispatch_claim(database, settings)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: dispatch(), range(2)))
    claimed = [item for item in results if item is not None]
    assert len(claimed) == 1
    assert claimed[0].status == "dispatching"
    assert claimed[0].generation == 1
    assert claimed[0].attempts == 1


def test_concurrent_step_only_one_external_action_can_start(database, settings):
    add_job(database)
    job = dispatch_claim(database, settings)
    barrier = Barrier(2)

    def start():
        barrier.wait()
        try:
            return claim_step(database, job.id, job.generation, "analyze", settings)
        except JobConflict as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: start(), range(2)))
    assert sum(not isinstance(item, JobConflict) for item in results) == 1
    busy = next(item for item in results if isinstance(item, JobConflict))
    assert busy.code == "step_running" and busy.status_code == 409
    with database.session() as db:
        assert db.scalar(select(func.count()).select_from(JobStep)) == 1


def test_late_ack_never_rewinds_fast_callback(database, settings):
    add_job(database)
    job = dispatch_claim(database, settings)
    claim_step(database, job.id, job.generation, "analyze", settings)
    assert acknowledge_dispatch(database, job.id, job.generation, True, settings) is False
    assert acknowledge_dispatch(database, job.id, job.generation, False, settings) is False
    with database.session() as db:
        assert db.get(Job, job.id).status == "running"


def test_completed_step_replays_without_second_apply(database, settings):
    add_job(database)
    job = dispatch_claim(database, settings)
    claim = claim_step(database, job.id, job.generation, "analyze", settings)
    calls = []

    def apply(db, locked_job):
        calls.append(locked_job.id)
        db.add(Audit(kind="analysis.completed", actor="test", detail={}))

    first = complete_step(
        database,
        job.id,
        job.generation,
        "analyze",
        claim.token,
        {"analysis_id": "synthetic"},
        apply=apply,
    )
    second = complete_step(
        database,
        job.id,
        job.generation,
        "analyze",
        claim.token,
        {"analysis_id": "different"},
        apply=apply,
    )
    replay = claim_step(database, job.id, job.generation, "analyze", settings)
    assert calls == [job.id]
    assert first == second == replay.result == {"analysis_id": "synthetic"}
    assert replay.status == "completed" and replay.token is None


def test_step_order_and_job_kind_restrict_callbacks(database, settings):
    add_job(database)
    job = dispatch_claim(database, settings)
    for step, code in [
        ("draft", "step_prerequisite_missing"),
        ("send", "step_not_allowed"),
        ("finish", "step_prerequisite_missing"),
    ]:
        with pytest.raises(JobConflict, match=code):
            claim_step(database, job.id, job.generation, step, settings)


def test_claim_transaction_is_closed_before_external_action(database, settings):
    add_job(database)
    job = dispatch_claim(database, settings)
    claim = claim_step(database, job.id, job.generation, "analyze", settings)
    assert database.engine.pool.checkedout() == 0
    # A second connection can lock the same job while external work is happening.
    with database.session.begin() as db:
        assert db.scalar(select(Job).where(Job.id == job.id).with_for_update(nowait=True))
    complete_step(database, job.id, job.generation, "analyze", claim.token, {})


def test_stale_completion_cannot_apply_after_generation_change(database, settings):
    add_job(database)
    now = utcnow()
    job = dispatch_claim(database, settings, now)
    claim = claim_step(database, job.id, job.generation, "analyze", settings, now)
    later = now + timedelta(seconds=settings.job_lease_seconds + 1)
    assert recover(database, settings, later)["retry_wait"] == 1
    newer = dispatch_claim(database, settings, later + timedelta(minutes=2))
    apply = Mock()
    with pytest.raises(JobConflict, match="stale_generation"):
        complete_step(database, job.id, job.generation, "analyze", claim.token, {}, apply=apply)
    assert newer.generation == job.generation + 1
    apply.assert_not_called()


def test_cancellation_blocks_late_result(database, settings):
    add_job(database)
    job = dispatch_claim(database, settings)
    claim = claim_step(database, job.id, job.generation, "analyze", settings)
    with database.session.begin() as db:
        db.get(Job, job.id).status = "cancelled"
    with pytest.raises(JobConflict, match="step_not_running"):
        complete_step(database, job.id, job.generation, "analyze", claim.token, {})


def test_wrong_claim_token_cannot_complete(database, settings):
    add_job(database)
    job = dispatch_claim(database, settings)
    claim_step(database, job.id, job.generation, "analyze", settings)
    with pytest.raises(JobConflict, match="step_not_owned"):
        complete_step(database, job.id, job.generation, "analyze", "wrong-token", {})


def test_completed_step_reused_after_safe_later_failure(database, settings):
    add_job(database)
    job = dispatch_claim(database, settings)
    claim = claim_step(database, job.id, job.generation, "analyze", settings)
    complete_step(database, job.id, job.generation, "analyze", claim.token, {"score": 92})
    draft = claim_step(database, job.id, job.generation, "draft", settings)
    fail_step(
        database,
        job.id,
        job.generation,
        "draft",
        draft.token,
        "temporary",
        settings,
        retryable=True,
    )
    now = utcnow() + timedelta(minutes=2)
    newer = dispatch_claim(database, settings, now)
    cached = claim_step(database, newer.id, newer.generation, "analyze", settings, now)
    assert cached.status == "completed" and cached.result == {"score": 92}
    assert (
        claim_step(database, newer.id, newer.generation, "draft", settings, now).status == "claimed"
    )


def test_expired_smtp_is_unknown_never_auto_retry(database, settings):
    from test_communications import outgoing

    _lead, message, _version, job = outgoing(database)
    with database.session.begin() as db:
        db.get(Message, message.id).state = "sending"
    message_id = message.id
    now = utcnow()
    claim_step(database, job.id, job.generation, "send", settings, now)
    later = now + timedelta(seconds=settings.job_lease_seconds + 1)
    assert recover(database, settings, later)["needs_review"] == 1
    assert dispatch_claim(database, settings, later + timedelta(days=1)) is None
    with database.session() as db:
        assert db.get(Message, message_id).state == "delivery_unknown"
        assert db.get(Job, job.id).status == "needs_review"
        assert db.scalar(select(JobStep)).status == "unknown"


def test_expired_notification_is_unknown(database, settings):
    add_job(database, kind="notification")
    now = utcnow()
    job = dispatch_claim(database, settings, now)
    claim_step(database, job.id, job.generation, "notify", settings, now)
    result = recover(database, settings, now + timedelta(seconds=settings.job_lease_seconds + 1))
    assert result["needs_review"] == 1


def test_definite_and_ambiguous_failures_remain_distinct(database, settings):
    add_job(database)
    job = dispatch_claim(database, settings)
    claim = claim_step(database, job.id, job.generation, "analyze", settings)
    fail_step(
        database,
        job.id,
        job.generation,
        "analyze",
        claim.token,
        "ai_timeout",
        settings,
        retryable=True,
    )
    with database.session() as db:
        assert db.get(Job, job.id).status == "retry_wait"
        assert db.scalar(select(JobStep)).status == "retryable"


def test_final_completion_survives_late_delivery_ack(database, settings):
    add_job(database, kind="notification")
    job = dispatch_claim(database, settings)
    for step in ["notify", "finish"]:
        claim = claim_step(database, job.id, job.generation, step, settings)
        complete_step(
            database, job.id, job.generation, step, claim.token, {}, final=step == "finish"
        )
    assert acknowledge_dispatch(database, job.id, job.generation, True, settings) is False
    with database.session() as db:
        assert db.get(Job, job.id).status == "succeeded"


def test_failed_apply_rolls_back_result_and_domain_mutation(database, settings):
    add_job(database)
    job = dispatch_claim(database, settings)
    claim = claim_step(database, job.id, job.generation, "analyze", settings)

    def apply(db, _job):
        db.add(Audit(kind="should.rollback", actor="test", detail={}))
        raise RuntimeError("synthetic")

    with pytest.raises(RuntimeError, match="synthetic"):
        complete_step(database, job.id, job.generation, "analyze", claim.token, {}, apply=apply)
    with database.session() as db:
        assert db.scalar(select(func.count()).select_from(Audit)) == 0
        assert db.scalar(select(JobStep)).status == "running"


def test_worker_unavailable_n8n_keeps_saved_job(database, settings):
    job_id = add_job(database)
    client = Mock()
    client.post.side_effect = httpx.ConnectError("synthetic")
    assert run_once(database, settings, client=client)
    with database.session() as db:
        job = db.get(Job, job_id)
        assert job.status == "retry_wait"
        assert job.error_code == "n8n_unavailable"
    payload = client.post.call_args.kwargs["json"]
    assert set(payload) == {"contract_version", "job_id", "generation", "kind"}
    assert client.post.call_args.args[0].endswith("/webhook/ai-lead-processing")


def test_retry_budget_eventually_stops(database, settings):
    add_job(database)
    now = utcnow()
    for attempt in range(settings.max_job_attempts):
        now += timedelta(minutes=2)
        job = dispatch_claim(database, settings, now)
        assert job.attempts == attempt + 1
        acknowledge_dispatch(database, job.id, job.generation, False, settings, now=now)
    assert dispatch_claim(database, settings, now + timedelta(days=1)) is None
    with database.session() as db:
        assert db.get(Job, job.id).status == "failed"
