from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class LeadInput(StrictModel):
    name: str = Field(min_length=2, max_length=120)
    email: EmailStr = Field(max_length=254)
    message: str = Field(min_length=10, max_length=8000)
    phone: str | None = Field(default=None, max_length=40)
    company: str | None = Field(default=None, max_length=160)
    utm: dict[str, str] = Field(default_factory=dict)

    @field_validator("utm")
    @classmethod
    def allow_utm(cls, value):
        if set(value) - {"utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term"}:
            raise ValueError("Неизвестная UTM-метка")
        if any(len(v) > 200 for v in value.values()):
            raise ValueError("Слишком длинная UTM-метка")
        return value

    @field_validator("name", "email", "phone", "company")
    @classmethod
    def no_header_controls(cls, value):
        if value and any(ord(c) < 32 for c in value):
            raise ValueError("Недопустимые управляющие символы")
        return value


class Login(StrictModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class LeadUpdate(StrictModel):
    expected_version: int = Field(ge=1)
    sales_stage: Literal["new", "contacted", "replied", "meeting_booked", "won", "lost"] | None = (
        None
    )
    priority_override: Literal["HOT", "WARM", "COLD"] | None = None
    reason: str = Field(min_length=3, max_length=500)


class DraftInput(StrictModel):
    subject: str = Field(min_length=1, max_length=250)
    body: str = Field(min_length=1, max_length=12000)
    recipient: EmailStr
    expected_version: int = Field(ge=1)

    @field_validator("subject", "recipient")
    @classmethod
    def no_header_controls(cls, value):
        if any(ord(c) < 32 for c in value):
            raise ValueError("Header control characters are forbidden")
        return value


class DecisionInput(StrictModel):
    version_id: str
    decision: Literal["approve", "reject"]
    reason: str = Field(default="", max_length=500)


class StopInput(StrictModel):
    reason: str = Field(min_length=3, max_length=500)
    opt_out: bool = False


class StepInput(StrictModel):
    generation: int = Field(ge=1)


class InboxMatch(StrictModel):
    lead_id: str
    reason: str = Field(min_length=3, max_length=500)


class ReviewInput(StrictModel):
    reason: str = Field(min_length=3, max_length=500)
