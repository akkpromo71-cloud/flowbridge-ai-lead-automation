"""Validated, versioned business configuration and its public projection."""

from decimal import Decimal
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Service(ConfigModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,49}$")
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=500)


class Branding(ConfigModel):
    primary_color: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    headline: str = Field(min_length=1, max_length=150)
    description: str = Field(min_length=1, max_length=500)
    primary_cta: str = Field(min_length=1, max_length=80)
    secondary_cta: str = Field(min_length=1, max_length=80)


class BudgetThreshold(ConfigModel):
    service_id: str
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    period: Literal["one_time", "monthly"]
    amount: Decimal = Field(gt=0, max_digits=15, decimal_places=2)


class ScoringConfig(ConfigModel):
    version: str = Field(min_length=1)
    hot_min: int = Field(default=70, ge=1, le=100)
    warm_min: int = Field(default=40, ge=1, le=99)
    not_fit_cap: int = Field(default=39, ge=0, le=99)
    budget_thresholds: list[BudgetThreshold]

    @model_validator(mode="after")
    def thresholds_are_ordered(self) -> "ScoringConfig":
        if not self.not_fit_cap < self.warm_min < self.hot_min:
            raise ValueError("Require not_fit_cap < warm_min < hot_min")
        keys = [(x.service_id, x.currency, x.period) for x in self.budget_thresholds]
        if len(keys) != len(set(keys)):
            raise ValueError("Duplicate budget threshold")
        return self


class FollowupConfig(ConfigModel):
    delay_hours: int = Field(default=48, ge=1, le=720)
    mailbox_freshness_minutes: int = Field(default=5, ge=1, le=60)


class AIConfig(ConfigModel):
    model: str = "gpt-4.1-mini-2025-04-14"
    prompt_version: str = "lead-facts-1"
    max_output_tokens: int = Field(default=2400, ge=256, le=8000)
    timeout_seconds: int = Field(default=40, ge=1, le=120)


class DraftConfig(ConfigModel):
    prompt_version: str = Field(default="lead-draft-2", min_length=1, max_length=80)
    max_output_tokens: int = Field(default=800, ge=256, le=2400)
    timeout_seconds: int = Field(default=40, ge=1, le=120)
    business_instructions: str = Field(
        default="Keep the reply concise and ask only useful clarifying questions.", max_length=2000
    )
    call_to_action_ru: str = Field(
        default="Предлагаем обсудить текущий процесс и следующие шаги.", max_length=300
    )
    call_to_action_en: str = Field(
        default="Let's discuss your current process and next steps.", max_length=300
    )


class NotificationConfig(ConfigModel):
    hot_enabled: bool = True


class BusinessConfig(ConfigModel):
    version: str = Field(min_length=1)
    business_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,49}$")
    company_name: str = Field(min_length=1, max_length=100)
    tagline: str = Field(min_length=1, max_length=200)
    timezone: str
    language: Literal["ru", "en"] = "ru"
    services: list[Service] = Field(min_length=1, max_length=20)
    branding: Branding
    scoring: ScoringConfig
    followup: FollowupConfig = Field(default_factory=FollowupConfig)
    ai: AIConfig = Field(default_factory=AIConfig)
    draft: DraftConfig = Field(default_factory=DraftConfig)
    notifications: NotificationConfig = Field(default_factory=NotificationConfig)
    approved_facts: list[str] = Field(default_factory=list, max_length=20)
    privacy_notice: str = Field(min_length=1, max_length=2000)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Unknown timezone; install tzdata on Windows") from exc
        return value

    @model_validator(mode="after")
    def services_are_consistent(self) -> "BusinessConfig":
        service_ids = [x.id for x in self.services]
        if len(service_ids) != len(set(service_ids)):
            raise ValueError("Duplicate service ID")
        if any(x.service_id not in service_ids for x in self.scoring.budget_thresholds):
            raise ValueError("Budget threshold references unknown service")
        return self

    def public_dict(self) -> dict:
        """Explicit allowlist prevents private rules reaching the public form."""
        result = self.model_dump(
            mode="json",
            include={
                "business_id",
                "company_name",
                "tagline",
                "timezone",
                "language",
                "services",
                "branding",
                "privacy_notice",
            },
        )
        result["brand"] = {"name": self.company_name, "tagline": self.tagline}
        return result


def load_business_config(path: str | Path) -> BusinessConfig:
    with Path(path).open(encoding="utf-8-sig") as stream:
        return BusinessConfig.model_validate(yaml.safe_load(stream))
