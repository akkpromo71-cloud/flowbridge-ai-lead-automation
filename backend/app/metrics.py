"""Conservative operational usage; configured estimates are never provider invoices."""

from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import String, cast, func, select

from app.models import Analysis, JobStep, MessageVersion


def usage_summary(analyses, attempts, settings):
    known = [
        row
        for row in analyses
        if row.usage.get("provider") == "openai"
        and isinstance(row.usage.get("input_tokens"), int)
        and isinstance(row.usage.get("output_tokens"), int)
        and row.usage["input_tokens"] >= 0
        and row.usage["output_tokens"] >= 0
    ]
    input_tokens = sum(row.usage["input_tokens"] for row in known)
    output_tokens = sum(row.usage["output_tokens"] for row in known)
    unknown = max(0, attempts - len(known))
    estimate = None
    if (
        settings.ai_input_price_per_million is not None
        and unknown == 0
        and all(row.model == settings.ai_price_model for row in known)
    ):
        amount = (
            Decimal(input_tokens) * settings.ai_input_price_per_million
            + Decimal(output_tokens) * settings.ai_output_price_per_million
        ) / Decimal(1_000_000)
        estimate = {
            "amount": str(amount.quantize(Decimal("0.000001"))),
            "currency": settings.ai_price_currency,
            "source": settings.ai_price_source,
            "as_of": settings.ai_price_as_of.isoformat(),
        }
    return {
        "ai_calls": attempts,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "unknown_usage_calls": unknown,
        "estimated_cost": estimate,
    }


def technical_metrics(db, since, settings):
    # A reserved step is an attempted external request, including a crash after
    # reservation. Only successful saved analyses have attributable token usage.
    # Missing/failed calls therefore remain unknown, never silently zero cost.
    attempts = db.scalar(
        select(func.count())
        .select_from(JobStep)
        .where(
            JobStep.step.in_(["analyze", "draft"]),
            JobStep.reserved_tokens > 0,
            JobStep.reserved_at >= since,
        )
    )
    # Attribute both attempts and known usage to the reservation/call period.
    # Completion after midnight must not move yesterday's request into today.
    # A successful analysis has one completed, positively reserved analyze step;
    # cached replays have no reservation and cannot duplicate usage here.
    analyses = db.scalars(
        select(Analysis)
        .join(JobStep, Analysis.operation_key == cast(JobStep.job_id, String) + ":analyze")
        .where(
            JobStep.step == "analyze",
            JobStep.status == "completed",
            JobStep.reserved_tokens > 0,
            JobStep.reserved_at >= since,
        )
    ).all()
    drafts = db.scalars(
        select(MessageVersion)
        .join(
            JobStep, cast(MessageVersion.message_id, String) == JobStep.result["message_id"].astext
        )
        .where(
            JobStep.step == "draft",
            JobStep.status == "completed",
            JobStep.reserved_tokens > 0,
            JobStep.reserved_at >= since,
            MessageVersion.revision == 1,
        )
    ).all()
    generated = [
        SimpleNamespace(model=v.generation.get("model"), usage=v.generation) for v in drafts
    ]
    return usage_summary([*analyses, *generated], attempts, settings)
