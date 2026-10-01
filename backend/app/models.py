from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

CONTROLLED_ANALYSIS_HISTORY = "controlled_analysis_history"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid4())


class Base(DeclarativeBase):
    pass


class Entity:
    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_id)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Operator(Entity, Base):
    __tablename__ = "operators"
    email: Mapped[str] = mapped_column(String(254), unique=True)
    display_name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(default=True)


class Session(Entity, Base):
    __tablename__ = "sessions"
    operator_id: Mapped[str] = mapped_column(ForeignKey("operators.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class Lead(Entity, Base):
    __tablename__ = "leads"
    reference: Mapped[str] = mapped_column(String(32), unique=True)
    intake_key: Mapped[str] = mapped_column(String(64), unique=True)
    payload_hash: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(254), index=True)
    phone: Mapped[str | None] = mapped_column(String(40))
    company: Mapped[str | None] = mapped_column(String(160))
    original_message: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(40), default="website")
    utm: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    processing_status: Mapped[str] = mapped_column(String(24), default="pending")
    sales_stage: Mapped[str] = mapped_column(String(24), default="new")
    temperature: Mapped[str | None] = mapped_column(String(8))
    score: Mapped[int | None] = mapped_column(Integer)
    analysis_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False))
    priority_override: Mapped[str | None] = mapped_column(String(8))
    version: Mapped[int] = mapped_column(default=1)
    communication_state: Mapped[str] = mapped_column(String(24), default="allowed")
    followup_status: Mapped[str] = mapped_column(String(24), default="not_scheduled")
    followup_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        ForeignKeyConstraint(
            ["id", "analysis_id"],
            ["analyses.lead_id", "analyses.id"],
            name="fk_lead_owned_analysis",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint("score IS NULL OR (score >= 0 AND score <= 100)"),
        CheckConstraint(
            "processing_status IN ('pending','processing','completed','needs_review','failed')"
        ),
        CheckConstraint(
            "sales_stage IN ('new','contacted','replied','meeting_booked','won','lost')"
        ),
        CheckConstraint("temperature IS NULL OR temperature IN ('HOT','WARM','COLD')"),
        CheckConstraint("communication_state IN ('allowed','paused','opted_out')"),
        Index("ix_leads_list", "created_at", "id"),
        Index("ix_leads_followup", "followup_status", "followup_due_at"),
    )


class Analysis(Entity, Base):
    __tablename__ = "analyses"
    lead_id: Mapped[str] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    operation_key: Mapped[str] = mapped_column(String(140), unique=True)
    facts: Mapped[dict[str, Any]] = mapped_column(JSONB)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB)
    config_version: Mapped[str] = mapped_column(String(80))
    config_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    prompt_version: Mapped[str] = mapped_column(String(80))
    schema_version: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(100))
    usage: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    latency_ms: Mapped[int] = mapped_column(default=0)
    __table_args__ = (UniqueConstraint("lead_id", "id", name="uq_analysis_owner"),)


class Message(Entity, Base):
    __tablename__ = "messages"
    lead_id: Mapped[str | None] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), index=True
    )
    direction: Mapped[str] = mapped_column(String(16))
    kind: Mapped[str] = mapped_column(String(24))
    state: Mapped[str] = mapped_column(String(32), default="draft")
    purpose_key: Mapped[str] = mapped_column(String(180), unique=True)
    current_version_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False))
    approved_version_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False))
    rfc_message_id: Mapped[str | None] = mapped_column(String(512), index=True)
    reply_headers: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    provider_accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    send_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    parent_message_id: Mapped[str | None] = mapped_column(ForeignKey("messages.id"))
    __table_args__ = (
        ForeignKeyConstraint(
            ["id", "current_version_id"],
            ["message_versions.message_id", "message_versions.id"],
            name="fk_message_owned_current",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["id", "approved_version_id"],
            ["message_versions.message_id", "message_versions.id"],
            name="fk_message_owned_approval",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint("direction IN ('inbound','outbound')"),
        CheckConstraint(
            "state IN ('draft','pending_approval','approved','queued','sending','provider_accepted','rejected','cancelled','failed','delivery_unknown','received','unmatched')"
        ),
    )


class MessageVersion(Entity, Base):
    __tablename__ = "message_versions"
    message_id: Mapped[str] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"))
    revision: Mapped[int] = mapped_column(Integer)
    subject: Mapped[str] = mapped_column(String(250))
    body: Mapped[str] = mapped_column(Text)
    recipient: Mapped[str] = mapped_column(String(254))
    checksum: Mapped[str] = mapped_column(String(64))
    generation: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    __table_args__ = (
        UniqueConstraint("message_id", "revision"),
        UniqueConstraint("message_id", "id", name="uq_version_owner"),
    )


class Approval(Entity, Base):
    __tablename__ = "approval_decisions"
    version_id: Mapped[str] = mapped_column(ForeignKey("message_versions.id", ondelete="CASCADE"))
    operator_id: Mapped[str] = mapped_column(ForeignKey("operators.id"))
    decision: Mapped[str] = mapped_column(String(12))
    reason: Mapped[str] = mapped_column(String(500), default="")
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    payload_hash: Mapped[str] = mapped_column(String(64))
    __table_args__ = (CheckConstraint("decision IN ('approve','reject')"),)


class Job(Entity, Base):
    __tablename__ = "jobs"
    kind: Mapped[str] = mapped_column(String(32))
    lead_id: Mapped[str | None] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"))
    message_id: Mapped[str | None] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"))
    dedup_key: Mapped[str] = mapped_column(String(180), unique=True)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    generation: Mapped[int] = mapped_column(default=0)
    attempts: Mapped[int] = mapped_column(default=0)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    error_code: Mapped[str | None] = mapped_column(String(80))
    config_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (Index("ix_jobs_due", "status", "next_attempt_at"),)


class JobStep(Entity, Base):
    __tablename__ = "job_steps"
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    generation: Mapped[int] = mapped_column(Integer)
    step: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(24), default="not_started")
    claim_token: Mapped[str | None] = mapped_column(String(64))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(80))
    reserved_tokens: Mapped[int] = mapped_column(default=0)
    reserved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    __table_args__ = (
        UniqueConstraint("job_id", "generation", "step"),
        CheckConstraint(
            "status IN ('not_started','running','completed','unknown','retryable','failed')"
        ),
    )


class Audit(Entity, Base):
    __tablename__ = "audit_events"
    lead_id: Mapped[str | None] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(64))
    actor: Mapped[str] = mapped_column(String(120))
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class IntegrationState(Base):
    __tablename__ = "integration_state"
    name: Mapped[str] = mapped_column(String(80), primary_key=True)
    state: Mapped[str] = mapped_column(String(24), default="not_configured")
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class Suppression(Base):
    __tablename__ = "recipient_suppressions"
    email: Mapped[str] = mapped_column(String(254), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    reason: Mapped[str] = mapped_column(String(80))


class RateLimit(Base):
    __tablename__ = "rate_limit_buckets"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    count: Mapped[int] = mapped_column(default=1)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
