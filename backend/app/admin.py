from datetime import timedelta
from statistics import median
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert

from app.domain.config import load_business_config
from app.domain.examples import labelled_examples
from app.domain.scoring import score
from app.intake import canonical_hash, key_hash
from app.metrics import technical_metrics
from app.models import (
    Analysis,
    Approval,
    Audit,
    IntegrationState,
    Job,
    Lead,
    Message,
    MessageVersion,
    Operator,
    Suppression,
    new_id,
    utcnow,
)
from app.schemas import DecisionInput, DraftInput, InboxMatch, LeadUpdate, ReviewInput, StopInput
from app.security import operator

router = APIRouter(prefix="/api/v1/admin", dependencies=[Depends(operator)])
public_router = APIRouter(prefix="/api/v1/public")


def lead_dict(lead, analysis=None):
    fields = (
        "id",
        "reference",
        "name",
        "email",
        "phone",
        "company",
        "source",
        "created_at",
        "updated_at",
        "processing_status",
        "sales_stage",
        "temperature",
        "score",
        "priority_override",
        "version",
        "followup_due_at",
        "followup_status",
    )
    result = {key: getattr(lead, key) for key in fields}
    result.update(
        communication_stopped=lead.communication_state != "allowed",
        opted_out=lead.communication_state == "opted_out",
    )
    if analysis:
        result.update(
            summary=analysis.facts.get("summary"), service_id=analysis.facts.get("service_id")
        )
    return result


def analysis_dict(analysis):
    return dict(
        id=analysis.id,
        facts=analysis.facts,
        **{key: analysis.result.get(key) for key in ("score", "temperature", "review_reasons")},
        contributions=analysis.result.get("criteria", []),
        config_version=analysis.config_version,
        prompt_version=analysis.prompt_version,
        model=analysis.model,
        created_at=analysis.created_at,
        latency_ms=analysis.latency_ms,
    )


def locked_lead(db, lead_id):
    lead = db.scalar(select(Lead).where(Lead.id == str(lead_id)).with_for_update())
    if not lead:
        raise HTTPException(404, "Заявка не найдена")
    return lead


def cancel_followup(db, lead, reason):
    lead.followup_status = "cancelled"
    db.execute(
        update(Message)
        .where(
            Message.lead_id == lead.id,
            Message.kind == "followup",
            Message.state.in_(["draft", "pending_approval", "approved", "queued"]),
        )
        .values(state="cancelled", approved_version_id=None)
    )
    # A worker may already be running. The final domain gate always rechecks the lead.
    # No job row is locked here: workers lock job -> lead. The final domain gate
    # observes this cancellation without reversing that order.
    db.add(
        Audit(lead_id=lead.id, kind="followup.cancelled", actor="system", detail={"reason": reason})
    )


@public_router.get("/demo")
def public_demo(request: Request):
    config = request.app.state.business
    scenarios = []
    selected = {"hot_ru", "minimal_fit", "not_fit_ru", "ambiguous_ru", "ad_spend", "injection_ru"}
    for example in [e for e in labelled_examples(config) if e.id in selected]:
        result = score(example.facts, config)
        scenarios.append(
            {
                "id": example.id,
                "title": example.title,
                "input": example.message,
                "analysis": {
                    "facts": example.facts.model_dump(mode="json"),
                    "score": result.score,
                    "temperature": result.temperature,
                    "contributions": [c.model_dump() for c in result.criteria],
                    "review_reasons": result.review_reasons,
                    "model": "fake-v1",
                    "config_version": config.version,
                },
            }
        )
    return {"mode": "demo", "readonly": True, "scenarios": scenarios}


@router.get("/leads")
def leads(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    q: str = Query("", max_length=120),
    temperature: Literal["HOT", "WARM", "COLD"] | None = None,
    stage: Literal["new", "contacted", "replied", "meeting_booked", "won", "lost"] | None = None,
    sort: Literal["newest", "oldest", "score"] = "newest",
):
    query = select(Lead)
    if q:
        pattern = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        query = query.where(
            Lead.name.ilike(pattern) | Lead.email.ilike(pattern) | Lead.company.ilike(pattern)
        )
    if temperature:
        query = query.where(Lead.temperature == temperature)
    if stage:
        query = query.where(Lead.sales_stage == stage)
    order = {
        "newest": Lead.created_at.desc(),
        "oldest": Lead.created_at.asc(),
        "score": Lead.score.desc().nulls_last(),
    }[sort]
    with request.app.state.db.session() as db:
        total = db.scalar(select(func.count()).select_from(query.subquery()))
        rows = db.scalars(
            query.order_by(order, Lead.id).offset((page - 1) * page_size).limit(page_size)
        ).all()
        analyses = {
            a.id: a
            for a in db.scalars(
                select(Analysis).where(
                    Analysis.id.in_([r.analysis_id for r in rows if r.analysis_id])
                )
            )
        }
        items = [lead_dict(row, analyses.get(row.analysis_id)) for row in rows]
    return dict(items=items, total=total, page=page, page_size=page_size)


@router.get("/leads/{lead_id}")
def detail(lead_id: UUID, request: Request):
    with request.app.state.db.session() as db:
        lead = db.get(Lead, str(lead_id))
        if not lead:
            raise HTTPException(404, "Заявка не найдена")
        analysis = db.get(Analysis, lead.analysis_id) if lead.analysis_id else None
        messages = []
        for msg in db.scalars(
            select(Message).where(Message.lead_id == lead.id).order_by(Message.created_at)
        ):
            version = (
                db.get(MessageVersion, msg.current_version_id) if msg.current_version_id else None
            )
            messages.append(
                dict(
                    id=msg.id,
                    direction=msg.direction,
                    kind=msg.kind,
                    state=msg.state,
                    created_at=msg.created_at,
                    provider_accepted_at=msg.provider_accepted_at,
                    current_version=dict(
                        id=version.id,
                        revision=version.revision,
                        subject=version.subject,
                        body=version.body,
                        recipient=version.recipient,
                        created_at=version.created_at,
                        generation={
                            key: value
                            for key, value in version.generation.items()
                            if key != "model_output"
                        },
                    )
                    if version
                    else None,
                )
            )
        events = [
            dict(id=e.id, kind=e.kind, actor=e.actor, created_at=e.created_at, detail=e.detail)
            for e in db.scalars(
                select(Audit).where(Audit.lead_id == lead.id).order_by(Audit.created_at)
            )
        ]
        return {
            **lead_dict(lead, analysis),
            "original_message": lead.original_message,
            "utm": lead.utm,
            "analysis": analysis_dict(analysis) if analysis else None,
            "messages": messages,
            "audit_events": events,
        }


@router.patch("/leads/{lead_id}")
def update_lead(
    lead_id: UUID, payload: LeadUpdate, request: Request, user: Operator = Depends(operator)
):
    with request.app.state.db.session.begin() as db:
        lead = locked_lead(db, lead_id)
        if lead.version != payload.expected_version:
            raise HTTPException(409, "Заявка изменилась. Обновите карточку")
        changes = payload.model_dump(exclude_unset=True)
        for field in ("sales_stage", "priority_override"):
            if field in changes:
                if field == "sales_stage" and changes[field] is None:
                    raise HTTPException(422, "Стадия не может быть пустой")
                setattr(lead, field, changes[field])
        if lead.sales_stage in {"won", "lost", "replied"}:
            cancel_followup(db, lead, lead.sales_stage)
        lead.version += 1
        lead.updated_at = utcnow()
        db.add(Audit(lead_id=lead.id, kind="lead.updated", actor=user.email, detail=changes))
        return lead_dict(lead)


@router.post("/leads/{lead_id}/reanalyze", status_code=202)
def reanalyze(lead_id: UUID, request: Request, user: Operator = Depends(operator)):
    with request.app.state.db.session.begin() as db:
        lead = locked_lead(db, lead_id)
        active = db.scalar(
            select(Job).where(
                Job.lead_id == lead.id,
                Job.kind == "lead_processing",
                Job.status.in_(["pending", "dispatching", "dispatched", "running", "retry_wait"]),
            )
        )
        if active:
            return {"job_id": active.id, "status": active.status}
        config = load_business_config(request.app.state.settings.business_config)
        job = Job(
            id=new_id(),
            kind="lead_processing",
            lead_id=lead.id,
            dedup_key=f"reanalyze:{new_id()}",
            config_snapshot=config.model_dump(mode="json"),
        )
        db.add(job)
        lead.processing_status = "pending"
        db.add(
            Audit(
                lead_id=lead.id,
                kind="analysis.requested",
                actor=user.email,
                detail={"config_version": config.version},
            )
        )
        return {"job_id": job.id, "status": "pending"}


@router.post("/messages/{message_id}/versions", status_code=201)
def edit_message(
    message_id: UUID, payload: DraftInput, request: Request, user: Operator = Depends(operator)
):
    with request.app.state.db.session.begin() as db:
        msg = db.get(Message, str(message_id))
        if not msg or not msg.lead_id:
            raise HTTPException(404, "Сообщение не найдено")
        lead = locked_lead(db, msg.lead_id)
        db.refresh(msg, with_for_update=True)
        current = db.get(MessageVersion, msg.current_version_id)
        if msg.direction != "outbound" or msg.state in {
            "sending",
            "provider_accepted",
            "delivery_unknown",
            "cancelled",
        }:
            raise HTTPException(409, "Эту отправку уже нельзя редактировать")
        if not current or current.revision != payload.expected_version:
            raise HTTPException(409, "Версия изменилась. Обновите карточку")
        if str(payload.recipient).lower() != lead.email.lower():
            raise HTTPException(422, "В v1 получатель должен совпадать с email заявки")
        version = MessageVersion(
            id=new_id(),
            message_id=msg.id,
            revision=current.revision + 1,
            subject=payload.subject,
            body=payload.body,
            recipient=str(payload.recipient).lower(),
            checksum=canonical_hash(payload.model_dump(mode="json", exclude={"expected_version"})),
            generation={"provider": "operator", "origin_version_id": current.id},
        )
        db.add(version)
        db.flush()
        msg.current_version_id, msg.approved_version_id, msg.state = (
            version.id,
            None,
            "pending_approval",
        )
        db.add(
            Audit(
                lead_id=lead.id,
                kind="message.edited",
                actor=user.email,
                detail={"message_id": msg.id, "revision": version.revision},
            )
        )
        return {"id": version.id, "revision": version.revision}


@router.post("/messages/{message_id}/decisions")
def decision(
    message_id: UUID, payload: DecisionInput, request: Request, user: Operator = Depends(operator)
):
    key = key_hash(request.headers.get("idempotency-key"))
    data_hash = canonical_hash({**payload.model_dump(), "message_id": str(message_id)})
    with request.app.state.db.session.begin() as db:
        # Serializes even malicious reuse of a decision key across different messages.
        from sqlalchemy import text

        db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": int(key[:15], 16)})
        existing = db.scalar(select(Approval).where(Approval.idempotency_key == key))
        if existing:
            if existing.payload_hash != data_hash:
                raise HTTPException(409, "Ключ решения уже использован с другими данными")
            return {
                "id": existing.id,
                "decision": existing.decision,
                "version_id": existing.version_id,
            }
        msg = db.get(Message, str(message_id))
        if not msg or not msg.lead_id:
            raise HTTPException(404, "Сообщение не найдено")
        lead = locked_lead(db, msg.lead_id)
        db.refresh(msg, with_for_update=True)
        if msg.direction != "outbound" or msg.current_version_id != payload.version_id:
            raise HTTPException(409, "Одобрение относится только к текущей исходящей версии")
        if msg.state in {"sending", "provider_accepted", "delivery_unknown", "cancelled"}:
            raise HTTPException(409, "Сообщение уже обрабатывается или отменено")
        if payload.decision == "approve" and (
            lead.communication_state != "allowed"
            or lead.sales_stage in {"won", "lost"}
            or db.get(Suppression, lead.email)
        ):
            raise HTTPException(409, "Коммуникация остановлена")
        if payload.decision == "approve":
            prior_send = db.scalar(
                select(Job).where(Job.dedup_key == f"send:{msg.id}:{payload.version_id}")
            )
            if msg.state in {"rejected", "failed"} or (
                prior_send
                and prior_send.status
                in {
                    "failed",
                    "needs_review",
                    "cancelled",
                    "succeeded",
                }
            ):
                raise HTTPException(409, "Создайте новую версию перед повторным одобрением")
        approval = Approval(
            id=new_id(),
            version_id=payload.version_id,
            operator_id=user.id,
            decision=payload.decision,
            reason=payload.reason,
            idempotency_key=key,
            payload_hash=data_hash,
        )
        db.add(approval)
        if payload.decision == "approve":
            if msg.kind == "followup" and lead.replied_at:
                raise HTTPException(409, "Клиент уже ответил")
            msg.approved_version_id, msg.state = payload.version_id, "queued"
            db.execute(
                insert(Job)
                .values(
                    kind="email_send",
                    lead_id=lead.id,
                    message_id=msg.id,
                    dedup_key=f"send:{msg.id}:{payload.version_id}",
                    config_snapshot=request.app.state.business.model_dump(mode="json"),
                )
                .on_conflict_do_nothing(index_elements=["dedup_key"])
            )
        else:
            msg.approved_version_id, msg.state = None, "rejected"
        db.add(
            Audit(
                lead_id=lead.id,
                kind=f"message.{payload.decision}",
                actor=user.email,
                detail={"message_id": msg.id, "version_id": payload.version_id},
            )
        )
        return {"id": approval.id, "decision": approval.decision, "version_id": approval.version_id}


@router.post("/leads/{lead_id}/communication-stop")
def stop(lead_id: UUID, payload: StopInput, request: Request, user: Operator = Depends(operator)):
    with request.app.state.db.session.begin() as db:
        lead = locked_lead(db, lead_id)
        lead.communication_state = "opted_out" if payload.opt_out else "paused"
        if payload.opt_out:
            db.execute(
                insert(Suppression)
                .values(email=lead.email, reason="operator_opt_out")
                .on_conflict_do_nothing()
            )
        cancel_followup(db, lead, lead.communication_state)
        lead.version += 1
        db.add(
            Audit(
                lead_id=lead.id,
                kind="communication.stopped",
                actor=user.email,
                detail={"reason": payload.reason, "opt_out": payload.opt_out},
            )
        )
    return {"status": "stopped"}


@router.get("/analytics")
def analytics(request: Request, days: int = Query(30, ge=1, le=366)):
    since = utcnow() - timedelta(days=days)

    def counts(items):
        result = {}
        for value in items:
            result[value or "unassessed"] = result.get(value or "unassessed", 0) + 1
        return result

    with request.app.state.db.session() as db:
        rows = db.scalars(select(Lead).where(Lead.created_at >= since)).all()
        ids = [row.id for row in rows]
        pending = db.scalar(
            select(func.count())
            .select_from(Message)
            .where(Message.lead_id.in_(ids), Message.state == "pending_approval")
        )
        times = []
        services = []
        first_acceptances = dict(
            db.execute(
                select(Message.lead_id, func.min(Message.provider_accepted_at))
                .where(Message.lead_id.in_(ids), Message.direction == "outbound")
                .group_by(Message.lead_id)
            ).all()
        )
        analyses = {
            a.id: a
            for a in db.scalars(
                select(Analysis).where(
                    Analysis.id.in_([r.analysis_id for r in rows if r.analysis_id])
                )
            )
        }
        for lead in rows:
            first = first_acceptances.get(lead.id)
            if first:
                times.append(max(0, (first - lead.created_at).total_seconds()))
            analysis = analyses.get(lead.analysis_id)
            services.append(analysis.facts.get("service_id") if analysis else None)
        from zoneinfo import ZoneInfo

        zone = ZoneInfo(request.app.state.business.timezone)
        series = counts([lead.created_at.astimezone(zone).date().isoformat() for lead in rows])
        won = sum(row.sales_stage == "won" for row in rows)
        return dict(
            total=len(rows),
            temperatures=counts([r.temperature for r in rows]),
            stages=counts([r.sales_stage for r in rows]),
            sources=counts([r.source for r in rows]),
            services=counts(services),
            pending_approval=pending,
            processing_errors=sum(r.processing_status == "failed" for r in rows),
            median_first_response_seconds=median(times) if times else None,
            technical=technical_metrics(db, since, request.app.state.settings),
            won_conversion={
                "numerator": won,
                "denominator": len(rows),
                "value": won / len(rows) if rows else None,
            },
            period={"from": since, "to": utcnow(), "timezone": str(zone)},
            series=[{"date": key, "count": value} for key, value in sorted(series.items())],
        )


@router.get("/integrations")
def integrations(request: Request):
    settings = request.app.state.settings
    output = []
    configured = {
        "openai": bool(settings.openai_api_key.get_secret_value()),
        "telegram": bool(
            settings.telegram_bot_token.get_secret_value() and settings.telegram_chat_id
        ),
        "smtp": bool(settings.smtp_host and settings.smtp_from),
        "imap": bool(settings.imap_host and settings.imap_username),
        "n8n": True,
    }
    with request.app.state.db.session() as db:
        for name in configured:
            saved = db.get(IntegrationState, name)
            simulated = name != "n8n" and (
                settings.mode in {"demo", "test"}
                or (settings.mode == "controlled" and name != "openai")
            )
            state = (
                "simulated"
                if simulated
                else (
                    saved.state
                    if saved
                    else ("unverified" if configured[name] else "not_configured")
                )
            )
            if (
                name == "n8n"
                and saved
                and saved.last_success_at
                and saved.last_success_at < utcnow() - timedelta(minutes=10)
            ):
                state = "stale"
            output.append(
                dict(
                    name=name,
                    state=state,
                    last_success_at=saved.last_success_at if saved else None,
                    detail="Синтетическая имитация; реальных вызовов нет"
                    if simulated
                    else (
                        "Внешние отправки отключены"
                        if name in {"smtp", "telegram"} and not settings.allow_external_sends
                        else ""
                    ),
                )
            )
    return output


@router.get("/inbox/unmatched")
def unmatched(request: Request):
    with request.app.state.db.session() as db:
        result = []
        for msg in db.scalars(
            select(Message)
            .where(Message.state == "unmatched")
            .order_by(Message.created_at.desc())
            .limit(100)
        ):
            version = db.get(MessageVersion, msg.current_version_id)
            result.append(
                {
                    "id": msg.id,
                    "subject": version.subject if version else "",
                    "body": version.body if version else "",
                    "sender": msg.reply_headers.get("sender"),
                    "created_at": msg.created_at,
                    "category": msg.reply_headers.get("category", "unmatched"),
                    "problem": msg.reply_headers.get("problem"),
                }
            )
        return result


@router.post("/inbox/{message_id}/match")
def match_inbound(
    message_id: UUID, payload: InboxMatch, request: Request, user: Operator = Depends(operator)
):
    try:
        lead_id = str(UUID(payload.lead_id))
    except ValueError:
        raise HTTPException(422, "Некорректный идентификатор заявки") from None
    with request.app.state.db.session.begin() as db:
        lead = locked_lead(db, lead_id)
        msg = db.scalar(select(Message).where(Message.id == str(message_id)).with_for_update())
        if not msg or msg.state != "unmatched":
            raise HTTPException(409, "Письмо уже разобрано или не найдено")
        msg.lead_id, msg.state, msg.kind = lead.id, "received", "reply"
        lead.replied_at = utcnow()
        if lead.sales_stage not in {"won", "lost", "meeting_booked"}:
            lead.sales_stage = "replied"
        lead.version += 1
        cancel_followup(db, lead, "operator_matched_reply")
        db.add(
            Audit(
                lead_id=lead.id,
                kind="inbox.matched",
                actor=user.email,
                detail={"message_id": msg.id, "reason": payload.reason},
            )
        )
    return {"status": "matched"}


@router.post("/leads/{lead_id}/followup-review")
def followup_review(
    lead_id: UUID, payload: ReviewInput, request: Request, user: Operator = Depends(operator)
):
    from app.communication import blocked_reason, inbox_fresh

    with request.app.state.db.session.begin() as db:
        # Match the worker's Job -> Lead lock order. A failed preparation can be
        # retried explicitly because this job only builds a draft, never sends.
        preparation = db.scalar(
            select(Job).where(Job.dedup_key == f"followup:{lead_id}").with_for_update()
        )
        lead = locked_lead(db, lead_id)
        if lead.followup_status != "needs_review" or not inbox_fresh(
            db, request.app.state.business
        ):
            raise HTTPException(409, "Нужна актуальная синхронизация входящего ящика")
        if blocked_reason(db, lead) or lead.replied_at:
            raise HTTPException(409, "Условия follow-up больше не выполняются")
        existing = db.scalar(
            select(Message)
            .where(Message.lead_id == lead.id, Message.kind == "followup")
            .with_for_update()
        )
        if existing:
            if existing.state in {"sending", "provider_accepted", "delivery_unknown", "cancelled"}:
                raise HTTPException(409, "Отправку нельзя возобновить без сверки её результата")
            current = db.get(MessageVersion, existing.current_version_id)
            if not current:
                raise HTTPException(409, "Не найдена текущая версия черновика")
            # A new revision makes a new explicit approval/send key. Reusing
            # a failed old send job would otherwise leave the message queued forever.
            revision = MessageVersion(
                id=new_id(),
                message_id=existing.id,
                revision=current.revision + 1,
                recipient=current.recipient,
                subject=current.subject,
                body=current.body,
                checksum=current.checksum,
            )
            db.add(revision)
            db.flush()
            existing.current_version_id = revision.id
            existing.state, existing.approved_version_id = "pending_approval", None
            lead.followup_status = "pending_approval"
        elif preparation:
            if preparation.status not in {"failed", "cancelled", "needs_review"}:
                raise HTTPException(409, "Подготовка follow-up ещё выполняется")
            preparation.status, preparation.attempts = "pending", 0
            preparation.error_code, preparation.lease_until = None, None
            preparation.next_attempt_at = utcnow()
            lead.followup_status = "pending_approval"
        else:
            lead.followup_status = "scheduled"
        db.add(
            Audit(
                lead_id=lead.id,
                kind="followup.reviewed",
                actor=user.email,
                detail={"reason": payload.reason},
            )
        )
    return {"status": "reviewed"}
