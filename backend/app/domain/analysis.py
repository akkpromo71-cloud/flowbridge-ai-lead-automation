"""Strict extraction contract; evidence must come from the original request."""

import re
import unicodedata
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.config import BusinessConfig

SCHEMA_VERSION = "lead-facts-1"
MoneyString = Annotated[str, Field(pattern=r"^(0|[1-9][0-9]{0,11})(\.[0-9]{1,2})?$")]
Quote = Annotated[
    str,
    Field(
        min_length=1,
        max_length=1500,
        description="A contiguous quote from the original enquiry, not a paraphrase or reordered words.",
    ),
]
Intent = Literal["proposal", "project_discussion", "comparison", "information", "unknown"]
Urgency = Literal["within_30_days", "within_90_days", "later", "unknown"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class SupportedFact(StrictModel):
    value: str | None = Field(max_length=500)
    evidence: list[Quote] = Field(max_length=3)

    @model_validator(mode="after")
    def require_evidence(self) -> "SupportedFact":
        if (self.value is None) != (len(self.evidence) == 0):
            raise ValueError("Known fact requires evidence; unknown fact has no evidence")
        if self.value is not None and not self.value.strip():
            raise ValueError("A fact cannot be blank")
        return self


class Budget(StrictModel):
    minimum: MoneyString | None
    maximum: MoneyString | None
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")] | None
    period: Literal["one_time", "monthly", "unknown"]
    purpose: Literal["services", "ad_spend", "combined", "unknown"] = Field(
        description=(
            "services: only the provider's service fee; ad_spend: only advertising/media spend; "
            "combined: one stated total covering both services and advertising, even if no split "
            "is given; unknown: the source does not identify the budget's purpose. Separate "
            "explicit service and ad amounts are separate budgets."
        )
    )
    service_id: str | None
    evidence: list[Quote] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def ordered_interval(self) -> "Budget":
        if self.minimum is not None and self.maximum is not None:
            if Decimal(self.minimum) > Decimal(self.maximum):
                raise ValueError("Budget minimum exceeds maximum")
        return self


class AnalysisFacts(StrictModel):
    # All properties are required. Unknown information is explicit null/unknown.
    service_fit: Literal["fit", "not_fit", "unknown"]
    service_id: str | None = Field(
        description=(
            "Configured service ID only when service_fit=fit. Use null when "
            "service_fit=not_fit or service_fit=unknown; never name an unsupported service."
        )
    )
    service_evidence: list[Quote] = Field(
        max_length=3,
        description=(
            "For service_fit=fit or service_fit=not_fit, include a source quote supporting "
            "the match or explicit mismatch. For service_fit=unknown, an empty list is allowed."
        ),
    )
    budgets: list[Budget] = Field(max_length=5)
    urgency: Urgency
    urgency_evidence: list[Quote] = Field(max_length=3)
    intent: Intent
    intent_evidence: list[Quote] = Field(max_length=3)
    business_context: SupportedFact
    desired_outcome: SupportedFact
    summary: str = Field(
        min_length=1,
        max_length=1200,
        description=(
            "Brief factual summary. Preserve what each deadline applies to: a requested start "
            "date is not a completion or delivery deadline. Do not add promises or missing facts."
        ),
    )
    missing_information: list[str] = Field(max_length=10)
    contradictions: list[str] = Field(max_length=5)
    suggested_next_action: Literal["clarify", "discuss_project", "review_fit", "manual_review"] = (
        Field(
            description=(
                "clarify: ask about missing or ambiguous details; discuss_project: an in-scope "
                "fit, including early supplier comparisons; review_fit: human review of suitability "
                "when service_fit is not fit; manual_review: contradictions or other unresolved "
                "risks. Never use review_fit for an established fit."
            )
        )
    )
    language: Literal["ru", "en", "unknown"]

    @model_validator(mode="after")
    def coherent_facts(self) -> "AnalysisFacts":
        if (self.service_fit == "fit") != (self.service_id is not None):
            raise ValueError("Only fit may have a service_id; fit requires one")
        if self.service_fit != "unknown" and not self.service_evidence:
            raise ValueError("Service fit decision requires evidence")
        if self.urgency != "unknown" and not self.urgency_evidence:
            raise ValueError("Known urgency requires evidence")
        if self.intent != "unknown" and not self.intent_evidence:
            raise ValueError("Known intent requires evidence")
        return self


class SemanticError(ValueError):
    """Safe issue codes, never an echoed lead or provider response."""

    def __init__(self, issues: list[str]):
        self.issues = sorted(set(issues))
        super().__init__(";".join(self.issues))


def _number_supported(amount: str, quotes: str) -> bool:
    # Support explicit decimal amounts, grouped thousands and stated multipliers.
    # Ambiguous prose is sent for review instead of silently manufacturing money.
    pattern = r"(?<!\w)(\d+(?:[ \u00a0\u202f]\d{3})*(?:[.,]\d+)?)(\s*(?:тыс\b\.?|thousand\b|k\b|млн\b\.?|million\b))?"
    for match in re.finditer(pattern, quotes, flags=re.IGNORECASE):
        raw = re.sub(r"[ \u00a0\u202f]", "", match.group(1))
        variants = [raw.replace(",", ".")]
        if re.fullmatch(r"\d{1,3}(?:,\d{3})+", raw):
            variants.append(raw.replace(",", ""))
        multiplier = (match.group(2) or "").strip().lower()
        factor = (
            1000000 if multiplier.startswith(("млн", "million")) else (1000 if multiplier else 1)
        )
        if any(Decimal(value) * factor == Decimal(amount) for value in variants):
            return True
    return False


def _evidence_key(text: str) -> str:
    # Canonical Unicode composition and case only; no compatibility folding,
    # whitespace edits, token reordering or semantic paraphrase matching.
    return unicodedata.normalize("NFC", unicodedata.normalize("NFC", text).casefold())


def validate_facts(facts: AnalysisFacts, source: str, config: BusinessConfig) -> AnalysisFacts:
    """Validate source support. This does not claim to prove all NLP semantics."""
    issues: list[str] = []
    allowed = {service.id for service in config.services}
    if facts.service_id is not None and facts.service_id not in allowed:
        issues.append("service_not_configured")
    quote_groups = [
        facts.service_evidence,
        facts.urgency_evidence,
        facts.intent_evidence,
        facts.business_context.evidence,
        facts.desired_outcome.evidence,
        *[budget.evidence for budget in facts.budgets],
    ]
    source_key = _evidence_key(source)
    if any(_evidence_key(quote) not in source_key for group in quote_groups for quote in group):
        issues.append("evidence_not_in_source")
    # Where a duration is explicit, its bucket can be checked without NLP.
    if facts.urgency != "unknown":
        day_counts = [
            int(value)
            for value in re.findall(
                r"\b(\d+)\s*(?:days?\b|дн[еёя]|день)",
                " ".join(facts.urgency_evidence),
                re.I,
            )
        ]
        expected = {
            "within_30_days": lambda days: 0 <= days <= 30,
            "within_90_days": lambda days: 31 <= days <= 90,
            "later": lambda days: days > 90,
        }[facts.urgency]
        if any(not expected(days) for days in day_counts):
            issues.append("urgency_evidence_conflict")
    for budget in facts.budgets:
        if budget.service_id is not None and budget.service_id not in allowed:
            issues.append("budget_service_not_configured")
        evidence = " ".join(budget.evidence)
        periods = {
            "one_time": r"разов|единораз|one[ -]time|\bonce\b|за проект|project total",
            "monthly": r"в месяц|ежемесяч|месяч|per month|monthly|/month",
        }
        if budget.period != "unknown" and not re.search(periods[budget.period], evidence, re.I):
            issues.append("budget_period_not_supported")
        ads = bool(re.search(r"реклам|\bads?\b|advertis|media spend", evidence, re.I))
        services = bool(re.search(r"услуг|гонорар|\bservices?\b|\bfees?\b", evidence, re.I))
        if budget.purpose == "services" and ads and not services:
            issues.append("budget_purpose_conflict")
        if budget.purpose == "ad_spend" and services and not ads:
            issues.append("budget_purpose_conflict")
        for value in (budget.minimum, budget.maximum):
            if value is not None and not _number_supported(value, evidence):
                issues.append("budget_amount_not_supported")
        if budget.currency is not None:
            aliases = {
                "KZT": r"\bKZT\b|тенге|₸",
                "RUB": r"\bRUB\b|руб|₽",
                "USD": r"\bUSD\b|\bUS dollars?\b|доллар\w* США",
                "EUR": r"\bEUR\b|евро|€",
                "GBP": r"\bGBP\b|фунт|£",
            }
            if not re.search(
                aliases.get(budget.currency, rf"\b{budget.currency}\b"), evidence, re.I
            ):
                issues.append("budget_currency_not_supported")
    if facts.contradictions:
        issues.append("source_contradiction")
    if issues:
        raise SemanticError(issues)
    return facts
