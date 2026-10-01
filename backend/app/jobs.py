"""Bounded PostgreSQL jobs with atomic step ownership and generation fencing.

Every public function closes its transaction before returning. Callers perform
network work ONLY between claim_step and complete_step/fail_step.
"""

import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import Database
from app.models import Audit, Job, JobStep, Lead, Message, utcnow
from app.settings import Settings

ACTIVE_JOB_STATES = ("dispatching", "dispatched", "running")
UNSAFE_STEPS = frozenset({"send", "notify"})
JOB_STEPS = {
    "lead_processing": ("analyze", "draft", "finish"),
    "notification": ("notify", "finish"),
    "email_send": ("send", "finish"),
    "followup_prepare": ("draft", "finish"),
}
ApplyResult = Callable[[Session, Job], None]


class JobConflict(RuntimeError):
    def __init__(self, code: str, status_code: int = 409):
        self.code = code
        self.status_code = status_code
        super().__init__(code)


@dataclass(frozen=True)
class StepClaim:
    status: Literal["claimed", "completed"]
    token: str | None
    result: dict[str, Any]


def _lease(settings: Settings, now: datetime) -> datetime:
    return now + timedelta(seconds=settings.job_lease_seconds)


def _retry_at(job: Job, now: datetime) -> datetime:
    delay = min(60, 2 ** min(job.attempts, 6))
    jitter = secrets.randbelow(401) / 1000 + 0.8
    return now + timedelta(seconds=delay * jitter)


def _reconcile_recovery(db: Session, job: Job) -> None:
    """Keep user-visible states consistent when the worker exhausts delivery.

    Already persisted analyses remain valid if a later draft/finish fails.
    An obsolete send job never changes a newer approved version's state.
    """
    if not job.lead_id:
        return
    lead = db.scalar(select(Lead).where(Lead.id == job.lead_id).with_for_update())
    if not lead:
        return
    if job.kind == "lead_processing" and lead.processing_status in {"pending", "processing"}:
        lead.processing_status = "failed" if job.status == "failed" else "pending"
        lead.version += 1
        lead.updated_at = utcnow()
    elif job.kind == "followup_prepare" and job.status == "failed":
        if lead.followup_status not in {"cancelled", "completed"}:
            lead.followup_status = "needs_review"
    elif job.kind == "email_send" and job.message_id and job.status in {"failed", "needs_review"}:
        message = db.scalar(select(Message).where(Message.id == job.message_id).with_for_update())
        if (
            message
            and job.dedup_key == f"send:{message.id}:{message.current_version_id}"
            and message.state in {"queued", "sending"}
        ):
            message.state = "delivery_unknown" if job.status == "needs_review" else "failed"
    if job.status in {"failed", "needs_review"}:
        db.add(
            Audit(
                lead_id=lead.id,
                kind="job.attention_required",
                actor="worker",
                detail={"job_id": job.id, "kind": job.kind, "code": job.error_code},
            )
        )


def dispatch_claim(
    database: Database, settings: Settings, now: datetime | None = None
) -> Job | None:
    now = now or utcnow()
    with database.session.begin() as db:
        job = db.scalar(
            select(Job)
            .where(
                Job.status.in_(("pending", "retry_wait")),
                Job.next_attempt_at <= now,
                Job.attempts < settings.max_job_attempts,
            )
            .order_by(Job.next_attempt_at, Job.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if job is None:
            return None
        job.status = "dispatching"
        job.generation += 1
        job.attempts += 1
        job.lease_until = _lease(settings, now)
        job.updated_at = now
        db.flush()
        db.expunge(job)
        return job


def acknowledge_dispatch(
    database: Database,
    job_id: str,
    generation: int,
    accepted: bool,
    settings: Settings,
    error_code: str | None = None,
    now: datetime | None = None,
) -> bool:
    """An HTTP acknowledgement cannot rewind a fast callback's running state."""
    now = now or utcnow()
    with database.session.begin() as db:
        job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
        if job is None or job.generation != generation or job.status != "dispatching":
            return False
        if accepted:
            job.status = "dispatched"
            job.error_code = None
        else:
            job.status = "retry_wait" if job.attempts < settings.max_job_attempts else "failed"
            job.error_code = error_code or "n8n_unavailable"
            job.next_attempt_at = _retry_at(job, now)
            job.lease_until = None
            _reconcile_recovery(db, job)
        job.updated_at = now
        return True


def _locked_job(db: Session, job_id: str, generation: int) -> Job:
    job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
    if job is None:
        raise JobConflict("job_not_found", 404)
    if job.generation != generation:
        raise JobConflict("stale_generation")
    return job


def claim_step(
    database: Database,
    job_id: str,
    generation: int,
    step: str,
    settings: Settings,
    now: datetime | None = None,
) -> StepClaim:
    if not step or len(step) > 40:
        raise JobConflict("invalid_step")
    now = now or utcnow()
    with database.session.begin() as db:
        job = _locked_job(db, job_id, generation)
        sequence = JOB_STEPS.get(job.kind, ())
        if step not in sequence:
            raise JobConflict("step_not_allowed")
        current = db.scalar(
            select(JobStep).where(
                JobStep.job_id == job_id,
                JobStep.generation == generation,
                JobStep.step == step,
            )
        )
        if current is not None and current.status == "completed":
            return StepClaim("completed", None, current.result)
        if current is not None and current.status == "unknown":
            raise JobConflict("step_result_unknown")
        if current is not None and current.status == "running":
            raise JobConflict("step_running")
        if current is not None and current.status == "failed":
            raise JobConflict("step_failed")
        if job.status not in ACTIVE_JOB_STATES:
            raise JobConflict("job_not_ready")
        if job.lease_until is None or job.lease_until <= now:
            raise JobConflict("job_lease_expired")
        for predecessor in sequence[: sequence.index(step)]:
            completed = db.scalar(
                select(JobStep.id).where(
                    JobStep.job_id == job_id,
                    JobStep.generation == generation,
                    JobStep.step == predecessor,
                    JobStep.status == "completed",
                )
            )
            if completed is None:
                raise JobConflict("step_prerequisite_missing")
        # Completed work is reused across dispatch generations of the SAME job.
        # A re-analysis of changed data must be a new job with its own snapshot.
        prior = db.scalar(
            select(JobStep)
            .where(
                JobStep.job_id == job_id,
                JobStep.step == step,
                JobStep.generation < generation,
                JobStep.status.in_(("completed", "unknown", "running")),
            )
            .order_by(JobStep.generation.desc())
            .limit(1)
        )
        if prior is not None and prior.status in ("unknown", "running"):
            raise JobConflict("prior_step_unresolved")
        if current is None:
            current = JobStep(job_id=job_id, generation=generation, step=step)
            db.add(current)
        if prior is not None:
            current.status = "completed"
            current.result = prior.result
            return StepClaim("completed", None, prior.result)
        token = secrets.token_hex(32)
        current.status = "running"
        current.claim_token = token
        current.lease_until = _lease(settings, now)
        current.error_code = None
        job.status = "running"
        job.updated_at = now
        job.lease_until = current.lease_until
        return StepClaim("claimed", token, {})


def _owned_step(
    db: Session, job: Job, generation: int, step: str, token: str, now: datetime
) -> JobStep:
    current = db.scalar(
        select(JobStep).where(
            JobStep.job_id == job.id,
            JobStep.generation == generation,
            JobStep.step == step,
        )
    )
    if current is None or not secrets.compare_digest(current.claim_token or "", token):
        raise JobConflict("step_not_owned")
    if current.status == "completed":
        return current
    if current.status != "running" or job.status not in ACTIVE_JOB_STATES:
        raise JobConflict("step_not_running")
    if current.lease_until is None or current.lease_until <= now:
        raise JobConflict("step_lease_expired")
    return current


def complete_step(
    database: Database,
    job_id: str,
    generation: int,
    step: str,
    token: str,
    result: dict,
    apply: ApplyResult | None = None,
    final: bool = False,
    now: datetime | None = None,
) -> dict:
    now = now or utcnow()
    with database.session.begin() as db:
        job = _locked_job(db, job_id, generation)
        current = _owned_step(db, job, generation, step, token, now)
        if current.status == "completed":
            return current.result
        if apply is not None:
            apply(db, job)
        current.status = "completed"
        current.result = result
        current.error_code = None
        current.lease_until = None
        job.updated_at = now
        job.error_code = None
        if final:
            job.status = "succeeded"
            job.lease_until = None
        return result


def fail_step(
    database: Database,
    job_id: str,
    generation: int,
    step: str,
    token: str,
    code: str,
    settings: Settings,
    *,
    retryable: bool = False,
    unknown: bool = False,
    apply: ApplyResult | None = None,
    now: datetime | None = None,
) -> None:
    now = now or utcnow()
    with database.session.begin() as db:
        job = _locked_job(db, job_id, generation)
        current = _owned_step(db, job, generation, step, token, now)
        if current.status == "completed":
            return
        if apply is not None:
            apply(db, job)
        current.status = "unknown" if unknown else ("retryable" if retryable else "failed")
        current.error_code = code[:80]
        current.lease_until = None
        job.status = (
            "needs_review"
            if unknown
            else (
                "retry_wait" if retryable and job.attempts < settings.max_job_attempts else "failed"
            )
        )
        job.error_code = code[:80]
        job.next_attempt_at = _retry_at(job, now)
        job.lease_until = None
        job.updated_at = now


def recover(database: Database, settings: Settings, now: datetime | None = None) -> dict[str, int]:
    now = now or utcnow()
    counts = {"retry_wait": 0, "needs_review": 0, "failed": 0}
    with database.session.begin() as db:
        jobs = db.scalars(
            select(Job)
            .where(
                Job.status.in_(ACTIVE_JOB_STATES),
                Job.lease_until <= now,
            )
            .with_for_update(skip_locked=True)
            .limit(100)
        ).all()
        for job in jobs:
            running = db.scalars(
                select(JobStep).where(
                    JobStep.job_id == job.id,
                    JobStep.status == "running",
                )
            ).all()
            unknown = any(item.step in UNSAFE_STEPS for item in running)
            for item in running:
                item.status = "unknown" if item.step in UNSAFE_STEPS else "retryable"
                item.error_code = (
                    "lease_expired_unknown" if item.step in UNSAFE_STEPS else "lease_expired"
                )
                item.lease_until = None
            if unknown:
                job.status = "needs_review"
                job.error_code = "delivery_unknown"
            else:
                job.status = "retry_wait" if job.attempts < settings.max_job_attempts else "failed"
                job.error_code = "lease_expired"
                job.next_attempt_at = _retry_at(job, now)
            job.lease_until = None
            job.updated_at = now
            _reconcile_recovery(db, job)
            counts[job.status] += 1
    return counts
