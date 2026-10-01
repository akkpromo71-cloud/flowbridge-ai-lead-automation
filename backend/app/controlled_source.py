"""Offline, atomic import of one verified synthetic analysis; never runs jobs.

Intake and processing services enqueue work/record provider completion, so they
cannot truthfully represent this offline restoration. Reuse their schemas,
validators, scoring and fake draft adapter, but record only the actual restore.
"""

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from sqlalchemy import func, select, text

from app.adapters.draft import FakeDraftAdapter
from app.domain.analysis import SCHEMA_VERSION, AnalysisFacts, validate_facts
from app.domain.config import BusinessConfig
from app.domain.scoring import ScoreResult, score
from app.intake import canonical_hash, key_hash
from app.models import (
    CONTROLLED_ANALYSIS_HISTORY,
    Analysis,
    Approval,
    Audit,
    IntegrationState,
    Job,
    JobStep,
    Lead,
    Message,
    MessageVersion,
    new_id,
)
from app.schemas import LeadInput
from app.settings import ROOT

REPORT_NAME = "controlled-ai-smoke-4.json"
PROVENANCE = "restored_from_verified_controlled_report"
MODEL = "gpt-4.1-mini-2025-04-14"
DRAFT_RESERVATION = "controlled_draft_smoke"


class SourceRestoreError(RuntimeError):
    """Static safe codes only; never echo input or DB/provider exceptions."""


@dataclass(frozen=True)
class VerifiedSource:
    digest: str
    source: str
    facts: dict
    result: dict
    usage: dict
    latency_ms: int
    config: BusinessConfig
    historical_reference: dict


def verified_report(report, config):
    """Import only the explicitly authorized local artifact and known fixture."""
    report = Path(report).resolve()
    if report != (ROOT / ".local" / REPORT_NAME).resolve():
        raise SourceRestoreError("only_verified_controlled_report_allowed")
    try:
        raw = report.read_bytes()
        if len(raw) > 256_000:
            raise ValueError
        data = json.loads(raw.decode("utf-8"))
        fixture = json.loads(
            (ROOT / "evaluation/controlled-pipeline-smoke.json").read_text(encoding="utf-8")
        )
        analysis, api = data["analysis"], data["api"]
        if (
            data["status"] != "completed"
            or type(data["requests_attempted"]) is not int
            or data["requests_attempted"] != 1
            or data["model"] != MODEL
            or data["actual_model_matches"] is not True
            or data["schema"] != {"status": "passed"}
            or data["semantic"] != {"status": "pass", "issues": []}
            or data["score_checks"] != {"score": True, "temperature": True}
            or data["case"] != fixture
            or analysis["model"] != MODEL
            or api["http_status"] != 200
            or api["actual_model"] != MODEL
            or api["response_status"] != "completed"
            or api["incomplete_reason"] is not None
            or api["refusal_present"] is not False
            or api["transport_error"] is not None
            or config.ai.model != MODEL
        ):
            raise ValueError
        facts = AnalysisFacts.model_validate(analysis["facts"])
        validate_facts(facts, data["case"]["source"], config)
        result = ScoreResult.model_validate(analysis["result"])
        if (
            score(facts, config).model_dump(mode="json") != analysis["result"]
            or result.score != 95
            or result.temperature != "HOT"
            or result.status != "completed"
            or facts.service_fit != "fit"
            or facts.service_id != "lead_automation"
            or facts.intent != "proposal"
            or facts.language != "ru"
            or facts.urgency != "within_90_days"
            or facts.contradictions
            or not facts.business_context.value
            or not facts.desired_outcome.value
            or len(facts.budgets) != 1
            or facts.budgets[0].minimum != "460000"
            or facts.budgets[0].maximum != "460000"
            or facts.budgets[0].currency != "KZT"
            or facts.budgets[0].purpose != "services"
            or facts.budgets[0].period != "one_time"
        ):
            raise ValueError
        usage = analysis["usage"]
        if (
            usage["provider"] != "openai"
            or usage["model"] != MODEL
            or usage["config_version"] != config.version
            or usage["prompt_version"] != config.ai.prompt_version
            or usage["schema_version"] != SCHEMA_VERSION
            or any(
                type(usage[name]) is not int or usage[name] < 0
                for name in ("input_tokens", "output_tokens", "latency_ms")
            )
            or api["usage"] != {name: usage[name] for name in ("input_tokens", "output_tokens")}
            or analysis["latency_ms"] != usage["latency_ms"]
        ):
            raise ValueError
        references = {
            "request_id": api.get("request_id"),
            "response_id": api.get("response_id"),
            "historical_lead_id": data.get("lead_id"),
            "requests_attempted": 1,
        }
        if str(UUID(references["historical_lead_id"])) != references["historical_lead_id"]:
            raise ValueError
        for name, prefix in (("request_id", "req_"), ("response_id", "resp_")):
            value = references[name]
            if value is not None and not re.fullmatch(prefix + r"[A-Za-z0-9_-]{1,190}", value):
                raise ValueError
        # Allowlist usage: arbitrary report metadata is never imported.
        selected_usage = {
            name: usage[name]
            for name in (
                "provider",
                "model",
                "config_version",
                "prompt_version",
                "schema_version",
                "input_tokens",
                "output_tokens",
                "latency_ms",
            )
        }
        if re.search(r"sk-(?:proj-)?[A-Za-z0-9_-]{16,}", json.dumps(analysis["facts"])):
            raise ValueError
        return VerifiedSource(
            hashlib.sha256(raw).hexdigest(),
            data["case"]["source"],
            analysis["facts"],
            analysis["result"],
            selected_usage,
            analysis["latency_ms"],
            config,
            references,
        )
    except Exception:
        raise SourceRestoreError("verified_report_validation_failed") from None


def _provenance(source):
    return {
        "provenance": PROVENANCE,
        "source_report": REPORT_NAME,
        "source_report_sha256": source.digest,
        "historical_provider_reference": source.historical_reference,
        "config_snapshot_source": "current_local_config_verified_against_historical_result",
    }


def _payload(source):
    # Contact identity is a new synthetic placeholder, not a historical client fact.
    return LeadInput(
        name="Synthetic restored source",
        email="controlled.restore@example.com",
        message=source.source,
    )


def verify_restored(db, marker, source):
    """No repair-on-read: any changed imported row/report stops the operation."""
    detail = marker.detail
    if marker.state != "restored" or any(
        detail.get(k) != v for k, v in _provenance(source).items()
    ):
        raise SourceRestoreError("restored_source_report_mismatch")
    lead = db.get(Lead, detail.get("lead_id"))
    analysis = db.get(Analysis, detail.get("analysis_id"))
    message = db.get(Message, detail.get("message_id"))
    version = db.get(MessageVersion, detail.get("source_version_id"))
    payload = _payload(source).model_dump(mode="json")
    if (
        not all((lead, analysis, message, version))
        or lead.original_message != source.source
        or lead.name != payload["name"]
        or lead.email != payload["email"]
        or lead.phone is not None
        or lead.company is not None
        or lead.utm != {}
        or lead.source != "restored_report"
        or lead.intake_key != key_hash("controlled-verified-report-source")
        or lead.payload_hash != canonical_hash(payload)
        or lead.analysis_id != analysis.id
        or analysis.lead_id != lead.id
        or lead.processing_status != "completed"
        or lead.score != 95
        or lead.temperature != "HOT"
        or analysis.operation_key != "restored:controlled-ai-smoke-4"
        or analysis.facts != source.facts
        or analysis.result != source.result
        or analysis.config_snapshot != source.config.model_dump(mode="json")
        or analysis.config_version != source.config.version
        or analysis.prompt_version != source.usage["prompt_version"]
        or analysis.schema_version != source.usage["schema_version"]
        or analysis.model != MODEL
        or analysis.latency_ms != source.latency_ms
        or analysis.usage != {**source.usage, **_provenance(source)}
        or message.lead_id != lead.id
        or message.direction != "outbound"
        or message.kind != "initial"
        or message.purpose_key != f"initial:{lead.id}"
        or message.state != "pending_approval"
        or message.current_version_id != version.id
        or message.approved_version_id is not None
        or message.provider_accepted_at is not None
        or message.send_started_at is not None
        or version.message_id != message.id
        or version.revision != 1
        or version.recipient != lead.email
        or version.checksum
        != canonical_hash(
            {"recipient": lead.email, "subject": version.subject, "body": version.body}
        )
        or canonical_hash(version.generation) != detail.get("placeholder_generation_hash")
        or version.generation.get("model_output")
        != {"subject": version.subject, "body": version.body}
        or any(version.generation.get(k) != v for k, v in _provenance(source).items())
        or version.generation.get("provider") != "fake"
    ):
        raise SourceRestoreError("restored_source_rows_differ_from_report")
    for model, count in (
        (Lead, 1),
        (Analysis, 1),
        (Message, 1),
        (MessageVersion, 1),
        (Job, 0),
        (JobStep, 0),
        (Approval, 0),
    ):
        if db.scalar(select(func.count()).select_from(model)) != count:
            raise SourceRestoreError("unexpected_controlled_source_rows")
    return lead, message, version


def restore_source(database, source):
    with database.session.begin() as db:
        # Same lock as controlled AI reservation: no concurrent application call/import.
        db.execute(text("SELECT pg_advisory_xact_lock(817246)"))
        mode = db.get(IntegrationState, "deployment")
        if not mode or mode.detail.get("mode") != "controlled":
            raise SourceRestoreError("controlled_database_required")
        if db.get(IntegrationState, DRAFT_RESERVATION):
            raise SourceRestoreError("draft_attempt_already_reserved_do_not_restore")
        existing = db.get(IntegrationState, CONTROLLED_ANALYSIS_HISTORY)
        if existing:
            verify_restored(db, existing, source)
            return "already_restored"
        for model in (Lead, Analysis, Message, MessageVersion, Job, JobStep, Approval):
            if db.scalar(select(func.count()).select_from(model)):
                raise SourceRestoreError("controlled_source_not_empty_do_not_overwrite")
        payload = _payload(source)
        lead = Lead(
            id=new_id(),
            reference=new_id().replace("-", ""),
            intake_key=key_hash("controlled-verified-report-source"),
            payload_hash=canonical_hash(payload.model_dump(mode="json")),
            name=payload.name,
            email=str(payload.email),
            original_message=source.source,
            source="restored_report",
            processing_status="completed",
            score=95,
            temperature="HOT",
        )
        db.add(lead)
        db.flush()
        analysis = Analysis(
            id=new_id(),
            lead_id=lead.id,
            operation_key="restored:controlled-ai-smoke-4",
            facts=source.facts,
            result=source.result,
            config_version=source.config.version,
            config_snapshot=source.config.model_dump(mode="json"),
            prompt_version=source.usage["prompt_version"],
            schema_version=source.usage["schema_version"],
            model=MODEL,
            usage={**source.usage, **_provenance(source)},
            latency_ms=source.latency_ms,
        )
        db.add(analysis)
        db.flush()
        lead.analysis_id = analysis.id
        fake = FakeDraftAdapter()
        draft = fake.draft(AnalysisFacts.model_validate(source.facts), source.config)
        message = Message(
            id=new_id(),
            lead_id=lead.id,
            direction="outbound",
            kind="initial",
            state="pending_approval",
            purpose_key=f"initial:{lead.id}",
        )
        db.add(message)
        db.flush()
        generation = {
            **fake.last_usage,
            **_provenance(source),
            "synthetic_placeholder_regenerated": True,
        }
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
            generation=generation,
        )
        db.add(version)
        db.flush()
        message.current_version_id = version.id
        marker = IntegrationState(
            name=CONTROLLED_ANALYSIS_HISTORY,
            state="restored",
            detail={
                **_provenance(source),
                "lead_id": lead.id,
                "analysis_id": analysis.id,
                "message_id": message.id,
                "source_version_id": version.id,
                "placeholder_generation_hash": canonical_hash(generation),
                "draft_requests_attempted": 0,
            },
        )
        db.add(marker)
        db.add(
            Audit(
                lead_id=lead.id,
                kind="controlled.source_restored",
                actor="local_operator",
                detail={"source_report": REPORT_NAME, "source_report_sha256": source.digest},
            )
        )
        db.flush()
        verify_restored(db, marker, source)
        return "restored"
