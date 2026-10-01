"""Authenticated n8n operations. PostgreSQL owns claims and all business results."""

import json
from datetime import timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert

from app.adapters.ai import AIError, FakeAIAdapter, OpenAIAdapter, PreparedDraft
from app.adapters.draft import FakeDraftAdapter, OpenAIDraftAdapter
from app.admin import locked_lead
from app.communication import (
    CommunicationError,
    blocked_reason,
    check_followups,
    inbox_fresh,
    message_failure,
    prepare_send,
    record_accepted,
    smtp_send,
    sync_mailbox,
    telegram_notify,
)
from app.domain.analysis import SCHEMA_VERSION, AnalysisFacts
from app.domain.config import BusinessConfig
from app.domain.scoring import score
from app.intake import canonical_hash
from app.jobs import JobConflict, claim_step, complete_step, fail_step
from app.models import (
    CONTROLLED_ANALYSIS_HISTORY,
    Analysis,
    Audit,
    IntegrationState,
    Job,
    JobStep,
    Lead,
    Message,
    MessageVersion,
    new_id,
    utcnow,
)
from app.schemas import StepInput
from app.security import service_auth

router = APIRouter(prefix="/internal/v1", dependencies=[Depends(service_auth)])


def _job_snapshot(database, job_id, generation):
    with database.session() as db:
        job = db.get(Job, job_id)
        if not job:
            raise JobConflict("job_not_found", 404)
        if (
            job.generation != generation
            or job.status != "running"
            or not job.lease_until
            or job.lease_until <= utcnow()
        ):
            raise JobConflict("stale_job_snapshot")
        # expire_on_commit=False and explicit close leave no open transaction.
        db.expunge(job)
        return job


def reserve_ai(database, settings, job_id, generation, token, source, config, operation="analyze"):
    """Conservative UTF-8 upper bound, not a quoted bill or token estimate.

    A request without returned usage keeps its full reservation. This intentionally
    prefers pausing processing over an unbounded or unknown provider spend.
    """
    now = utcnow()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    output_limit = (
        config.ai.max_output_tokens if operation == "analyze" else config.draft.max_output_tokens
    )
    reservation = (
        len(source.encode("utf-8"))
        + len(
            json.dumps(
                (AnalysisFacts if operation == "analyze" else PreparedDraft).model_json_schema()
            ).encode("utf-8")
        )
        + len(config.model_dump_json().encode("utf-8"))
        + output_limit
        + 4096
    )
    with database.session.begin() as db:
        db.execute(text("SELECT pg_advisory_xact_lock(817246)"))
        job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
        step = db.scalar(
            select(JobStep).where(
                JobStep.job_id == job_id,
                JobStep.generation == generation,
                JobStep.step == operation,
            )
        )
        if (
            not job
            or not step
            or job.generation != generation
            or job.status != "running"
            or step.status != "running"
            or step.claim_token != token
            or step.lease_until <= now
        ):
            raise JobConflict("stale_ai_reservation")
        previous_calls = db.scalar(
            select(func.count())
            .select_from(JobStep)
            .where(
                JobStep.job_id == job_id,
                JobStep.step == operation,
                JobStep.reserved_tokens > 0,
            )
        )
        if previous_calls >= 3:
            raise AIError("ai_attempt_limit")
        parallel = db.scalar(
            select(func.count())
            .select_from(JobStep)
            .where(
                JobStep.step.in_(["analyze", "draft"]),
                JobStep.status == "running",
                JobStep.reserved_tokens > 0,
                JobStep.lease_until > now,
            )
        )
        used = db.scalar(
            select(func.coalesce(func.sum(JobStep.reserved_tokens), 0)).where(
                JobStep.reserved_at >= start,
                JobStep.reserved_at < start + timedelta(days=1),
            )
        )
        if settings.mode == "controlled":
            if db.get(IntegrationState, CONTROLLED_ANALYSIS_HISTORY):
                raise AIError("controlled_historical_analysis_consumed_do_not_repeat")
            attempted = db.scalar(
                select(func.count()).select_from(JobStep).where(JobStep.reserved_tokens > 0)
            )
            if attempted >= settings.controlled_ai_request_limit:
                raise AIError("controlled_request_limit")
            from decimal import Decimal

            ceiling = (
                Decimal(40000) * Decimal("0.40") + Decimal(output_limit) * Decimal("1.60")
            ) / Decimal(1_000_000)
            if (
                config.ai.model != "gpt-4.1-mini-2025-04-14"
                or output_limit > 2400
                or ceiling > settings.controlled_ai_max_cost_usd
            ):
                raise AIError("controlled_budget_or_model")
            if len(source) > 4000 or len(source.encode("utf-8")) > 12000:
                raise AIError("controlled_input_limit")
        code = None
        if parallel >= settings.ai_max_parallel:
            code = "ai_parallel_limit"
            next_attempt = now + timedelta(seconds=15)
        elif used + reservation > settings.ai_daily_token_budget:
            code = "ai_budget_exhausted"
            next_attempt = start + timedelta(days=1)
        if code:
            step.status, step.error_code, step.lease_until = "retryable", code, None
            job.status, job.error_code, job.lease_until = "retry_wait", code, None
            job.next_attempt_at = next_attempt
            # No provider call happened; dispatch failures do not consume AI attempts.
            job.attempts = max(0, job.attempts - 1)
            lead = locked_lead(db, job.lead_id)
            lead.processing_status = "pending"
            db.add(
                Audit(
                    lead_id=job.lead_id,
                    kind="analysis.deferred",
                    actor="system",
                    detail={"code": code},
                )
            )
        else:
            step.reserved_tokens = reservation
            step.reserved_at = now
    if code:
        raise AIError(code, retryable=True)
    return reservation


def settle_usage(database, job_id, generation, usage, operation="analyze"):
    input_tokens, output_tokens = usage.get("input_tokens"), usage.get("output_tokens")
    if input_tokens is None or output_tokens is None:
        return
    # Keep a positive marker even on a provider-reported zero usage, so it still
    # counts as an attempted external call. Unknown usage is never changed to 0.
    with database.session.begin() as db:
        step = db.scalar(
            select(JobStep)
            .where(
                JobStep.job_id == job_id,
                JobStep.generation == generation,
                JobStep.step == operation,
            )
            .with_for_update()
        )
        if step and step.reserved_tokens:
            step.reserved_tokens = max(1, input_tokens + output_tokens)


def _analyze(database, settings, job, config, claim, supplied_adapter=None):
    with database.session.begin() as db:
        owned = db.scalar(select(Job).where(Job.id == job.id).with_for_update())
        if (
            not owned
            or owned.generation != job.generation
            or owned.status != "running"
            or not owned.lease_until
            or owned.lease_until <= utcnow()
        ):
            raise JobConflict("stale_analysis_start")
        lead = locked_lead(db, job.lead_id)
        source = lead.original_message
        lead.processing_status = "processing"
    adapter = supplied_adapter or (
        FakeAIAdapter()
        if settings.mode not in {"live", "controlled"}
        else OpenAIAdapter(api_key=settings.openai_api_key.get_secret_value())
    )
    if settings.mode in {"live", "controlled"}:
        reserve_ai(database, settings, job.id, job.generation, claim.token, source, config)
    try:
        facts = adapter.analyze(source, config)
    finally:
        if settings.mode in {"live", "controlled"}:
            settle_usage(database, job.id, job.generation, adapter.last_usage)
    result = score(facts, config)
    analysis_id = new_id()

    def apply(db, owned_job):
        lead = locked_lead(db, owned_job.lead_id)
        db.add(
            Analysis(
                id=analysis_id,
                lead_id=lead.id,
                operation_key=f"{job.id}:analyze",
                facts=facts.model_dump(mode="json"),
                result=result.model_dump(mode="json"),
                config_version=config.version,
                config_snapshot=config.model_dump(mode="json"),
                prompt_version=config.ai.prompt_version,
                schema_version=SCHEMA_VERSION,
                model=adapter.last_usage["model"],
                usage=adapter.last_usage,
                latency_ms=adapter.last_usage["latency_ms"],
            )
        )
        db.flush()
        if settings.mode in {"controlled", "live"}:
            db.execute(
                insert(IntegrationState)
                .values(
                    name="openai",
                    state="ready",
                    last_success_at=utcnow(),
                    detail={"model": adapter.last_usage["model"]},
                )
                .on_conflict_do_update(
                    index_elements=["name"],
                    set_={
                        "state": "ready",
                        "last_success_at": utcnow(),
                        "detail": {"model": adapter.last_usage["model"]},
                    },
                )
            )
        lead.analysis_id, lead.score, lead.temperature = (
            analysis_id,
            result.score,
            result.temperature,
        )
        lead.processing_status = result.status
        lead.updated_at, lead.version = utcnow(), lead.version + 1
        db.add(
            Audit(
                lead_id=lead.id,
                kind="analysis.completed",
                actor="system",
                detail={"analysis_id": analysis_id, "status": result.status},
            )
        )
        if result.temperature == "HOT" and config.notifications.hot_enabled:
            db.execute(
                insert(Job)
                .values(
                    kind="notification",
                    lead_id=lead.id,
                    dedup_key=f"hot:{analysis_id}",
                    config_snapshot=config.model_dump(mode="json"),
                )
                .on_conflict_do_nothing(index_elements=["dedup_key"])
            )

    return complete_step(
        database,
        job.id,
        job.generation,
        "analyze",
        claim.token,
        {
            "analysis_id": analysis_id,
            "analysis_status": result.status,
            "temperature": result.temperature,
        },
        apply=apply,
    )


def _draft(database, settings, job, config, claim):
    with database.session() as db:
        lead = db.get(Lead, job.lead_id)
        analysis = db.get(Analysis, lead.analysis_id) if lead and lead.analysis_id else None
        if not analysis:
            raise CommunicationError("analysis_missing")
        facts = AnalysisFacts.model_validate(analysis.facts)
    followup = job.kind == "followup_prepare"
    purpose = "followup" if followup else "initial"
    # Do not pay for a replacement of an immutable draft or for a stopped follow-up.
    with database.session() as db:
        existing = db.scalar(
            select(Message).where(Message.purpose_key == f"{purpose}:{job.lead_id}")
        )
        if existing:
            return complete_step(
                database,
                job.id,
                job.generation,
                "draft",
                claim.token,
                {"message_id": existing.id, "state": existing.state},
            )
        if followup:
            lead = db.get(Lead, job.lead_id)
            reason = blocked_reason(db, lead, Message(kind="followup"))
            if reason:
                raise CommunicationError(reason)
            if not inbox_fresh(db, config):
                raise CommunicationError("inbox_not_fresh")
    real = settings.mode in {"controlled", "live"} and settings.real_draft_enabled
    adapter = (
        OpenAIDraftAdapter(api_key=settings.openai_api_key.get_secret_value())
        if real
        else FakeDraftAdapter()
    )
    if real:
        reserve_ai(
            database,
            settings,
            job.id,
            job.generation,
            claim.token,
            facts.model_dump_json(),
            config,
            "draft",
        )
    try:
        draft = adapter.draft(facts, config, purpose)
    finally:
        if real:
            settle_usage(database, job.id, job.generation, adapter.last_usage, "draft")
    result = {"state": "pending_approval"}

    def apply(db, owned_job):
        lead = locked_lead(db, owned_job.lead_id)
        if followup:
            probe = Message(kind="followup")
            reason = blocked_reason(db, lead, probe)
            if reason:
                raise CommunicationError(reason)
            if not inbox_fresh(db, config):
                raise CommunicationError("inbox_not_fresh")
        existing = db.scalar(select(Message).where(Message.purpose_key == f"{purpose}:{lead.id}"))
        if existing:
            # Re-analysis must never replace a manager's edits or approval.
            result.update(message_id=existing.id, state=existing.state)
            return
        message = Message(
            id=new_id(),
            lead_id=lead.id,
            direction="outbound",
            kind=purpose,
            state="pending_approval",
            purpose_key=f"{purpose}:{lead.id}",
        )
        if followup:
            parent = db.scalar(
                select(Message).where(
                    Message.lead_id == lead.id,
                    Message.kind == "initial",
                    Message.state == "provider_accepted",
                )
            )
            if not parent:
                raise CommunicationError("initial_not_accepted")
            message.parent_message_id = parent.id
        db.add(message)
        db.flush()
        version = MessageVersion(
            id=new_id(),
            message_id=message.id,
            revision=1,
            recipient=lead.email,
            subject=draft.subject,
            body=draft.body,
            checksum=canonical_hash(
                {"recipient": lead.email, "subject": draft.subject, "body": draft.body}
            ),
            generation=adapter.last_usage,
        )
        db.add(version)
        db.flush()
        message.current_version_id = version.id
        if followup:
            lead.followup_status = "pending_approval"
        db.add(
            Audit(
                lead_id=lead.id,
                kind="message.draft_created",
                actor="system",
                detail={"message_id": message.id, "version_id": version.id, "kind": purpose},
            )
        )
        result["message_id"] = message.id

    return complete_step(
        database, job.id, job.generation, "draft", claim.token, result, apply=apply
    )


def execute_step(
    database, settings, default_config, job_id, generation, step, *, analysis_adapter=None
):
    """Called by the authenticated route; exposed for synthetic integration tests."""
    claim = claim_step(database, job_id, generation, step, settings)
    if claim.status == "completed":
        return {"status": "completed", "replayed": True, **claim.result}
    job = _job_snapshot(database, job_id, generation)
    config = (
        BusinessConfig.model_validate(job.config_snapshot)
        if job.config_snapshot
        else default_config
    )
    try:
        if step == "analyze":
            result = _analyze(database, settings, job, config, claim, analysis_adapter)
        elif step == "draft":
            result = _draft(database, settings, job, config, claim)
        elif step == "notify":
            with database.session() as db:
                lead = db.get(Lead, job.lead_id)
                analysis = db.get(Analysis, lead.analysis_id) if lead and lead.analysis_id else None
                if not lead or not analysis:
                    raise CommunicationError("analysis_missing")
                service = next(
                    (s.name for s in config.services if s.id == analysis.facts.get("service_id")),
                    None,
                )
                payload = {
                    "lead_id": lead.id,
                    "name": lead.name,
                    "company": lead.company,
                    "score": lead.score,
                    "temperature": lead.temperature,
                    "service": service,
                    "urgency": analysis.facts.get("urgency"),
                    "summary": analysis.facts.get("summary"),
                }
            notification = telegram_notify(settings, payload)
            result = complete_step(database, job_id, generation, step, claim.token, notification)
        elif step == "send":
            envelope = prepare_send(database, settings, config, job)
            sent = (
                {"accepted": True}
                if envelope.get("already_accepted")
                else smtp_send(settings, envelope)
            )
            result = complete_step(
                database,
                job_id,
                generation,
                step,
                claim.token,
                sent,
                apply=lambda db, owned: record_accepted(db, owned, config),
            )
        else:  # jobs.py permits finish only after all preceding steps completed.
            result = complete_step(database, job_id, generation, step, claim.token, {}, final=True)
        return {"status": "completed", "replayed": False, **result}
    except (AIError, CommunicationError) as error:
        if error.code in {"ai_budget_exhausted", "ai_parallel_limit"}:
            raise
        retryable = error.retryable and job.attempts < settings.max_job_attempts
        if isinstance(error, AIError) and settings.mode == "controlled":
            retryable = False
        if isinstance(error, AIError) and settings.mode == "live":
            with database.session() as db:
                attempts = db.scalar(
                    select(func.count())
                    .select_from(JobStep)
                    .where(
                        JobStep.job_id == job_id,
                        JobStep.step == "analyze",
                        JobStep.reserved_tokens > 0,
                    )
                )
            retryable = retryable and attempts < 3
        unknown = getattr(error, "unknown", False)
        code = error.code
        exhausted = error.retryable or code == "ai_attempt_limit"

        def failure(db, owned_job):
            if step == "analyze":
                lead = locked_lead(db, owned_job.lead_id)
                lead.score, lead.temperature = None, None
                lead.processing_status = (
                    "pending" if retryable else ("failed" if exhausted else "needs_review")
                )
                lead.version += 1
                db.add(
                    Audit(
                        lead_id=lead.id,
                        kind="analysis.error",
                        actor="system",
                        detail={"code": code},
                    )
                )
            elif step == "draft" and job.kind == "followup_prepare":
                lead = locked_lead(db, owned_job.lead_id)
                lead.followup_status = "needs_review"
            elif step == "send":
                message_failure(db, owned_job, code, unknown, retryable)

        fail_step(
            database,
            job_id,
            generation,
            step,
            claim.token,
            error.code,
            settings,
            retryable=retryable,
            unknown=unknown,
            apply=failure,
        )
        raise


@router.post("/jobs/{job_id}/steps/{step}")
def process_step(job_id: UUID, step: str, payload: StepInput, request: Request):
    database, settings = request.app.state.db, request.app.state.settings
    try:
        result = execute_step(
            database,
            settings,
            request.app.state.business,
            str(job_id),
            payload.generation,
            step,
            analysis_adapter=getattr(request.app.state, "analysis_adapter", None),
        )
    except JobConflict as error:
        raise HTTPException(error.status_code, error.code) from None
    except (AIError, CommunicationError) as error:
        raise HTTPException(503 if error.retryable else 422, error.code) from None
    with database.session.begin() as db:
        db.execute(
            insert(IntegrationState)
            .values(name="n8n", state="ready", last_success_at=utcnow(), detail={})
            .on_conflict_do_update(
                index_elements=["name"], set_={"state": "ready", "last_success_at": utcnow()}
            )
        )
    return result


@router.post("/mailboxes/default/sync")
def mailbox_sync(request: Request):
    try:
        return sync_mailbox(request.app.state.db, request.app.state.settings)
    except CommunicationError as error:
        raise HTTPException(503, error.code) from None


@router.post("/followups/check")
def followup_check(request: Request):
    return check_followups(request.app.state.db, request.app.state.business)
