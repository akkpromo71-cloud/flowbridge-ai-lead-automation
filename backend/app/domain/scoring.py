"""Deterministic prioritisation: never parse the summary or infer missing data."""

from decimal import Decimal
from typing import Literal

from pydantic import Field

from app.domain.analysis import AnalysisFacts, StrictModel
from app.domain.config import BusinessConfig


class Criterion(StrictModel):
    key: str
    points: int
    maximum: int
    reason: str


class ScoreResult(StrictModel):
    score: int | None = Field(ge=0, le=100)
    temperature: Literal["HOT", "WARM", "COLD"] | None
    status: Literal["completed", "needs_review"]
    criteria: list[Criterion]
    config_version: str
    scoring_version: str
    review_reasons: list[str]


def temperature_for(value: int, config: BusinessConfig) -> Literal["HOT", "WARM", "COLD"]:
    if not 0 <= value <= 100:
        raise ValueError("Score must be within 0–100")
    if value >= config.scoring.hot_min:
        return "HOT"
    if value >= config.scoring.warm_min:
        return "WARM"
    return "COLD"


def _budget_points(facts: AnalysisFacts, config: BusinessConfig) -> tuple[int, str, bool]:
    comparisons: list[tuple[int, str]] = []
    for budget in facts.budgets:
        if budget.purpose != "services" or budget.service_id not in (None, facts.service_id):
            continue
        threshold = next(
            (
                rule
                for rule in config.scoring.budget_thresholds
                if rule.service_id == facts.service_id
                and rule.currency == budget.currency
                and rule.period == budget.period
            ),
            None,
        )
        if threshold is None or (budget.minimum is None and budget.maximum is None):
            continue
        minimum = Decimal(budget.minimum) if budget.minimum is not None else None
        maximum = Decimal(budget.maximum) if budget.maximum is not None else None
        if minimum is not None and minimum >= threshold.amount:
            comparisons.append((15, "Бюджет услуг достигает настроенного порога"))
        elif maximum is not None and maximum < threshold.amount:
            comparisons.append((5, "Бюджет услуг ниже настроенного порога"))
        else:
            comparisons.append((7, "Диапазон пересекает порог или не подтверждает его достижение"))
    if len(comparisons) > 1:
        # Do not add alternatives or choose whichever gives the largest score.
        return 7, "Несколько сопоставимых бюджетов требуют уточнения", True
    if comparisons:
        return (*comparisons[0], False)
    return 7, "Нет сопоставимого бюджета услуг; валюты и периоды не пересчитываются", False


def score(facts: AnalysisFacts, config: BusinessConfig) -> ScoreResult:
    reasons = []
    if facts.service_fit == "unknown":
        reasons.append("service_fit_unknown")
    if facts.service_id is not None and facts.service_id not in {x.id for x in config.services}:
        reasons.append("service_not_configured")
    if facts.contradictions:
        reasons.append("source_contradiction")
    budget_points, budget_reason, ambiguous = _budget_points(facts, config)
    if ambiguous:
        reasons.append("multiple_comparable_budgets")
    if reasons:
        return ScoreResult(
            score=None,
            temperature=None,
            status="needs_review",
            criteria=[],
            config_version=config.version,
            scoring_version=config.scoring.version,
            review_reasons=reasons,
        )

    intent_points = {
        "proposal": 30,
        "project_discussion": 30,
        "comparison": 15,
        "information": 5,
        "unknown": 0,
    }
    urgency_points = {"within_30_days": 15, "within_90_days": 10, "later": 0, "unknown": 5}
    criteria = [
        Criterion(
            key="service_fit",
            points=30 if facts.service_fit == "fit" else 0,
            maximum=30,
            reason="Услуга подходит"
            if facts.service_fit == "fit"
            else "Запрос явно вне перечня услуг",
        ),
        Criterion(
            key="intent",
            points=intent_points[facts.intent],
            maximum=30,
            reason=f"Намерение: {facts.intent}",
        ),
        Criterion(
            key="urgency",
            points=urgency_points[facts.urgency],
            maximum=15,
            reason=f"Срок: {facts.urgency}",
        ),
        Criterion(key="budget", points=budget_points, maximum=15, reason=budget_reason),
        Criterion(
            key="business_context",
            points=5 if facts.business_context.value is not None else 0,
            maximum=5,
            reason="Бизнес-контекст подтверждён цитатой"
            if facts.business_context.value
            else "Бизнес-контекст не указан",
        ),
        Criterion(
            key="desired_outcome",
            points=5 if facts.desired_outcome.value is not None else 0,
            maximum=5,
            reason="Ожидаемый результат подтверждён цитатой"
            if facts.desired_outcome.value
            else "Ожидаемый результат не указан",
        ),
    ]
    total = sum(item.points for item in criteria)
    if facts.service_fit == "not_fit" and total > config.scoring.not_fit_cap:
        criteria.append(
            Criterion(
                key="not_fit_cap",
                points=config.scoring.not_fit_cap - total,
                maximum=0,
                reason="Ограничение оценки для неподходящей услуги",
            )
        )
        total = config.scoring.not_fit_cap
    return ScoreResult(
        score=total,
        temperature=temperature_for(total, config),
        status="completed",
        criteria=criteria,
        config_version=config.version,
        scoring_version=config.scoring.version,
        review_reasons=[],
    )
