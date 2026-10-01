"""One synthetic real-draft check using the existing production draft adapter.

This is a local evaluator, not an application worker or a mail workflow. The
earlier analysis reservation is immutable; a separate IntegrationState row is
the durable, single-use draft reservation. No network is used by plan.
"""

import argparse
import json
import re
import time
from decimal import Decimal
from pathlib import Path

import httpx
from openai import OpenAI
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from app.adapters.ai import AIError
from app.adapters.draft import OpenAIDraftAdapter, validate_draft
from app.controlled_smoke import controlled_settings
from app.controlled_source import (
    REPORT_NAME,
    SourceRestoreError,
    restore_source,
    verified_report,
    verify_restored,
)
from app.db import Database
from app.domain.analysis import AnalysisFacts, validate_facts
from app.domain.config import BusinessConfig, load_business_config
from app.domain.scoring import score
from app.evaluate_openai import GuardedTransport, _hidden_key, _redact
from app.intake import canonical_hash
from app.models import (
    CONTROLLED_ANALYSIS_HISTORY,
    Analysis,
    Audit,
    IntegrationState,
    JobStep,
    Lead,
    Message,
    MessageVersion,
    new_id,
    utcnow,
)
from app.settings import ROOT

FIXTURE = ROOT / "evaluation/controlled-pipeline-smoke.json"
OUTPUT = ROOT / ".local/controlled-draft-smoke.json"
RESERVATION = "controlled_draft_smoke"
MODEL = "gpt-4.1-mini-2025-04-14"
MAX_OUTPUT_TOKENS = 800
MAX_REQUESTS = 1
MAX_COST_USD = Decimal("0.02")
INPUT_PRICE = Decimal("0.40")
OUTPUT_PRICE = Decimal("1.60")
INPUT_TOKEN_CEILING = 40_000
TIMEOUT_SECONDS = 40


class DraftSmokeError(RuntimeError):
    pass


def cost_ceiling(config):
    return (
        Decimal(INPUT_TOKEN_CEILING) * INPUT_PRICE
        + Decimal(config.draft.max_output_tokens) * OUTPUT_PRICE
    ) / Decimal(1_000_000)


def _fixture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _source(db, fixture):
    mode = db.get(IntegrationState, "deployment")
    if not mode or mode.detail.get("mode") != "controlled":
        raise DraftSmokeError("controlled_database_required")
    if db.get(IntegrationState, RESERVATION):
        raise DraftSmokeError("draft_attempt_already_reserved_do_not_repeat")
    attempted = db.scalar(
        select(func.count()).select_from(JobStep).where(JobStep.reserved_tokens > 0)
    )
    analyzed = db.scalar(
        select(func.count())
        .select_from(JobStep)
        .where(
            JobStep.step == "analyze", JobStep.status == "completed", JobStep.reserved_tokens > 0
        )
    )
    historical_source = db.get(IntegrationState, CONTROLLED_ANALYSIS_HISTORY)
    if historical_source:
        if attempted != 0:
            raise DraftSmokeError("unexpected_call_after_historical_source_restore")
        imported = verified_report(
            ROOT / ".local" / REPORT_NAME, load_business_config(ROOT / "config/automation.yaml")
        )
        verify_restored(db, historical_source, imported)
    elif attempted != 1 or analyzed != 1:
        raise DraftSmokeError("expected_single_completed_analysis_reservation")
    leads = db.scalars(select(Lead).where(Lead.original_message == fixture["source"])).all()
    if len(leads) != 1:
        raise DraftSmokeError("synthetic_lead_not_unique")
    lead = leads[0]
    if (
        not lead.email.endswith("@example.com")
        or lead.processing_status != "completed"
        or lead.score != fixture["expected_score"]
        or lead.temperature != fixture["expected_temperature"]
    ):
        raise DraftSmokeError("synthetic_lead_not_ready")
    analysis = db.get(Analysis, lead.analysis_id) if lead.analysis_id else None
    if not analysis or analysis.model != MODEL:
        raise DraftSmokeError("real_analysis_not_found")
    config = BusinessConfig.model_validate(analysis.config_snapshot)
    facts = AnalysisFacts.model_validate(analysis.facts)
    validate_facts(facts, fixture["source"], config)
    if (
        config.ai.model != MODEL
        or config.draft.max_output_tokens > MAX_OUTPUT_TOKENS
        or config.draft.timeout_seconds > TIMEOUT_SECONDS
        or cost_ceiling(config) > MAX_COST_USD
        or score(facts, config).score != fixture["expected_score"]
        or facts.service_fit != "fit"
        or facts.service_id != "lead_automation"
        or facts.intent != "proposal"
        or facts.language != "ru"
        or facts.urgency != "within_90_days"
        or facts.contradictions
        or len(facts.budgets) != 1
        or facts.budgets[0].purpose != "services"
        or facts.budgets[0].minimum is None
        or Decimal(facts.budgets[0].minimum) != Decimal("460000")
        or facts.budgets[0].maximum is None
        or Decimal(facts.budgets[0].maximum) != Decimal("460000")
        or facts.budgets[0].currency != "KZT"
        or facts.budgets[0].period != "one_time"
        or not facts.business_context.value
        or not facts.desired_outcome.value
    ):
        raise DraftSmokeError("frozen_synthetic_facts_or_limits_changed")
    messages = db.scalars(select(Message).where(Message.lead_id == lead.id)).all()
    if len(messages) != 1:
        raise DraftSmokeError("unexpected_synthetic_message_state")
    message = messages[0]
    version = (
        db.get(MessageVersion, message.current_version_id) if message.current_version_id else None
    )
    if (
        message.kind != "initial"
        or message.state != "pending_approval"
        or message.approved_version_id is not None
        or not version
        or version.revision != 1
        or version.generation.get("provider") != "fake"
        or version.recipient != lead.email
    ):
        raise DraftSmokeError("source_draft_changed_or_approved")
    return lead, message, version, facts, config


def preflight(output=OUTPUT):
    output = Path(output).resolve()
    if not output.is_relative_to((ROOT / ".local").resolve()) or output.exists():
        raise DraftSmokeError("new_output_under_project_local_required")
    settings = controlled_settings()  # Private service tokens only; no OpenAI key.
    db = Database(settings.database_url)
    try:
        with db.session() as session:
            lead, message, version, facts, config = _source(session, _fixture())
            return {
                "lead_id": lead.id,
                "analysis_id": lead.analysis_id,
                "analysis_facts_hash": canonical_hash(facts.model_dump(mode="json")),
                "analysis_config_hash": canonical_hash(config.model_dump(mode="json")),
                "message_id": message.id,
                "source_version_id": version.id,
                "source_version_checksum": version.checksum,
                "prompt_version": config.draft.prompt_version,
                "cost_ceiling": cost_ceiling(config),
                "output": output,
            }
    finally:
        db.engine.dispose()


def prepare_source(report):
    settings = controlled_settings()  # Explicit empty OpenAI key; no .env or client.
    source = verified_report(report, load_business_config(settings.business_config))
    database = Database(settings.database_url)
    try:
        status = restore_source(database, source)
    finally:
        database.engine.dispose()
    print(
        f"Source {status}: {REPORT_NAME}. Draft requests_attempted=0. "
        "No analysis/API/SMTP/Telegram/IMAP calls."
    )
    return status


def reserve(database, prepared):
    """Atomic, never-cleared marker. A process crash consumes the attempt."""
    with database.session.begin() as db:
        mode = db.get(IntegrationState, "deployment")
        if not mode or mode.detail.get("mode") != "controlled":
            raise DraftSmokeError("controlled_database_required")
        message = db.get(Message, prepared["message_id"], with_for_update=True)
        if (
            not message
            or message.current_version_id != prepared["source_version_id"]
            or message.state != "pending_approval"
            or message.approved_version_id is not None
        ):
            raise DraftSmokeError("source_draft_changed_or_approved")
        created = db.execute(
            insert(IntegrationState)
            .values(
                name=RESERVATION,
                state="reserved",
                detail={
                    "lead_id": prepared["lead_id"],
                    "source_version_id": prepared["source_version_id"],
                    "request_limit": MAX_REQUESTS,
                    "reserved_at": utcnow().isoformat(),
                },
            )
            .on_conflict_do_nothing(index_elements=["name"])
            .returning(IntegrationState.name)
        ).scalar_one_or_none()
        if created is None:
            raise DraftSmokeError("draft_attempt_already_reserved_do_not_repeat")


def content_checks(draft, config, *, key=""):
    """Conservative automatic checks; factual quality still needs human review."""
    text = f"{draft.subject}\n{draft.body}"
    folded = text.casefold()
    cyrillic = len(re.findall(r"[а-яё]", folded))
    latin = len(re.findall(r"[a-z]", folded))
    checks = {
        "subject_nonempty": bool(draft.subject.strip()),
        "body_nonempty": bool(draft.body.strip()),
        "russian_language": cyrillic >= 20 and cyrillic > latin,
        "relevant_cta": bool(
            re.search(r"обсуд|созвон|встрет|уточн|напиши|ответ|свяж|согласу", folded)
        ),
        "no_advertising_budget_or_unrelated_service": not bool(
            re.search(r"реклам\w*|маркетинг\w*|ремонт\w* техник|advertis\w*|media spend", folded)
        ),
        "no_invented_price_or_delivery_term": not bool(
            re.search(
                r"бесплатн\w*|без оплаты|цена состав\w*|стоимость (состав\w*|будет)|"
                r"фиксированн\w* стоим\w*|завершим|сдадим|выполним к|"
                r"реализуем к|free of charge|we will deliver by",
                folded,
            )
        ),
        "no_sent_claim": not bool(
            re.search(
                r"письмо (уже )?отправлен|ответ (уже )?отправлен|мы (уже )?отправили|already sent",
                folded,
            )
        ),
        "no_approval_claim": not bool(
            re.search(
                r"менеджер (уже )?(одобрил|утвердил)|ответ (уже )?одобрен|already approved", folded
            )
        ),
        "no_prompt_leak": not bool(
            re.search(
                r"system prompt|developer instruction|ignore previous instruction|"
                r"системн\w* (промпт|инструкц)|внутренн\w* инструкц|скрыт\w* промпт",
                folded,
            )
        ),
        "no_secret_echo": not bool(
            (key and key in text) or re.search(r"sk-(?:proj-)?[A-Za-z0-9_-]{16,}", text)
        ),
    }
    try:
        validate_draft(draft, config)
    except AIError:
        checks["production_draft_validation"] = False
    else:
        checks["production_draft_validation"] = True
    return checks


def _persist(database, prepared, draft, generation):
    """New immutable version of the synthetic message; no Approval or send Job."""
    with database.session.begin() as db:
        message = db.get(Message, prepared["message_id"], with_for_update=True)
        lead = db.get(Lead, prepared["lead_id"])
        if (
            not message
            or not lead
            or message.lead_id != lead.id
            or message.current_version_id != prepared["source_version_id"]
            or message.approved_version_id is not None
            or message.state != "pending_approval"
        ):
            raise DraftSmokeError("source_draft_changed_during_request")
        version = MessageVersion(
            id=new_id(),
            message_id=message.id,
            revision=2,
            recipient=lead.email,
            subject=draft.subject,
            body=draft.body,
            checksum=canonical_hash(
                {"recipient": lead.email, "subject": draft.subject, "body": draft.body}
            ),
            generation={**generation, "origin_version_id": prepared["source_version_id"]},
        )
        db.add(version)
        db.flush()
        message.current_version_id = version.id
        message.state = "draft"
        db.add(
            Audit(
                lead_id=lead.id,
                kind="message.draft_smoke",
                actor="system",
                detail={"message_id": message.id, "version_id": version.id},
            )
        )
        return version.id


def run(output, *, max_cost_usd, transport=None, key_provider=None):
    prepared = preflight(output)
    if max_cost_usd is None or not (
        prepared["cost_ceiling"] <= Decimal(str(max_cost_usd)) <= MAX_COST_USD
    ):
        raise DraftSmokeError("explicit_draft_budget_limit_required")
    print(
        f"Модель: {MODEL}; draft-запросов максимум 1; консервативный резерв "
        f"до ${prepared['cost_ceiling']}; подтверждённый лимит ${max_cost_usd}."
    )
    key = (key_provider or _hidden_key)()
    database = Database(controlled_settings().database_url)
    guard = None
    http = None
    report = {
        "status": "stopped",
        "requested_model": MODEL,
        "request_limit": MAX_REQUESTS,
        "requests_attempted": 0,
        "source_lead_id": prepared["lead_id"],
        "source_message_id": prepared["message_id"],
        "source_version_id": prepared["source_version_id"],
        "provider": "openai",
        "prompt_version": prepared["prompt_version"],
        "max_cost_usd": str(max_cost_usd),
        "estimated_cost_usd": None,
        "human_approval_required": True,
        "sent": False,
    }
    started = time.monotonic()
    reserved = False
    try:
        reserve(database, prepared)
        reserved = True
        with database.session() as db:
            lead, _message, source_version, facts, config = _source_after_reservation(db, prepared)
            report["source_provenance"] = {
                "id": source_version.id,
                "revision": source_version.revision,
                "checksum": source_version.checksum,
                "provider": source_version.generation.get("provider"),
                "model": source_version.generation.get("model"),
                "prompt_version": source_version.generation.get("prompt_version"),
                "provenance": source_version.generation.get("provenance"),
                "source_report": source_version.generation.get("source_report"),
                "source_report_sha256": source_version.generation.get("source_report_sha256"),
                "historical_provider_reference": source_version.generation.get(
                    "historical_provider_reference"
                ),
            }
            if lead.email.endswith("@example.com") is False:
                raise DraftSmokeError("synthetic_recipient_required")
        guard = GuardedTransport(transport or httpx.HTTPTransport(retries=0), 1, api_key=key)
        http = httpx.Client(
            transport=guard, trust_env=False, timeout=TIMEOUT_SECONDS, follow_redirects=False
        )
        adapter = OpenAIDraftAdapter(client=OpenAI(api_key=key, max_retries=0, http_client=http))
        try:
            draft = adapter.draft(facts, config, "initial")
        except AIError as error:
            report["error_code"] = error.code
            report["schema_validation"] = (
                "failed"
                if error.code == "draft_schema"
                else (
                    "passed"
                    if error.code.startswith("draft_")
                    and error.code
                    in {
                        "draft_numeric_claim",
                        "draft_unsupported_promise",
                        "draft_contact_claim",
                        "draft_subject_header",
                        "draft_sender_as_customer",
                    }
                    else "not_checked"
                )
            )
            if report["schema_validation"] == "passed":
                report["content_validation"] = "failed"
        except Exception:
            report["error_code"] = "provider_or_contract_error"
        else:
            record = guard.records[0] if guard.records else {}
            checks = content_checks(draft, config, key=key)
            report["content_checks"] = checks
            report["schema_validation"] = "passed"
            report["content_validation"] = "passed" if all(checks.values()) else "failed"
            report["generation_provenance"] = {
                name: value for name, value in adapter.last_usage.items() if name != "model_output"
            }
            if record.get("actual_model") != MODEL:
                report["error_code"] = "unexpected_model"
            elif all(checks.values()):
                report["generated_subject"] = draft.subject
                report["generated_body"] = draft.body
                report["version_id"] = _persist(database, prepared, draft, adapter.last_usage)
                report["message_state"] = "draft"
                report["approved_version_id"] = None
                report["status"] = "completed"
            else:
                report["error_code"] = "draft_content_checks_failed"
        report["latency_ms"] = round((time.monotonic() - started) * 1000)
    except DraftSmokeError as error:
        report["error_code"] = str(error)
    finally:
        if http:
            http.close()
        if guard:
            report["requests_attempted"] = guard.attempts
            record = guard.records[0] if guard.records else {}
            report["api"] = {k: v for k, v in record.items() if k != "parsed_text"}
            report["model"] = record.get("actual_model")
            report["input_tokens"] = (record.get("usage") or {}).get("input_tokens")
            report["output_tokens"] = (record.get("usage") or {}).get("output_tokens")
            if record.get("usage"):
                usage = record["usage"]
                report["estimated_cost_usd"] = str(
                    (
                        Decimal(usage["input_tokens"]) * INPUT_PRICE
                        + Decimal(usage["output_tokens"]) * OUTPUT_PRICE
                    )
                    / Decimal(1_000_000)
                )
        if reserved:
            with database.session.begin() as db:
                marker = db.get(IntegrationState, RESERVATION)
                if marker is None:
                    raise DraftSmokeError("draft_reservation_disappeared")
                marker.state = "completed" if report["status"] == "completed" else "failed"
                marker.detail = {
                    **marker.detail,
                    "requests_attempted": report["requests_attempted"],
                    "status": report["status"],
                }
                if report["status"] == "completed":
                    marker.last_success_at = utcnow()
        database.engine.dispose()
        report["factual_review"] = "pending_human_review"
        report["latency_ms"] = report.get("latency_ms", round((time.monotonic() - started) * 1000))
        target = Path(output).resolve()
        target.parent.mkdir(exist_ok=True)
        report = _redact(report, key)
        with target.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
        print(
            f"Статус: {report['status']}; внешних запросов: {report['requests_attempted']}. Не повторяйте запуск."
        )
    return report


def _source_after_reservation(db, prepared):
    lead = db.get(Lead, prepared["lead_id"])
    message = db.get(Message, prepared["message_id"])
    version = db.get(MessageVersion, prepared["source_version_id"])
    analysis = db.get(Analysis, lead.analysis_id) if lead and lead.analysis_id else None
    if (
        not lead
        or not message
        or not version
        or not analysis
        or lead.analysis_id != prepared["analysis_id"]
        or lead.original_message != _fixture()["source"]
        or analysis.model != MODEL
        or version.checksum != prepared["source_version_checksum"]
        or message.current_version_id != version.id
        or message.approved_version_id is not None
        or message.state != "pending_approval"
    ):
        raise DraftSmokeError("source_draft_changed_after_reservation")
    facts = AnalysisFacts.model_validate(analysis.facts)
    config = BusinessConfig.model_validate(analysis.config_snapshot)
    validate_facts(facts, lead.original_message, config)
    if (
        canonical_hash(facts.model_dump(mode="json")) != prepared["analysis_facts_hash"]
        or canonical_hash(config.model_dump(mode="json")) != prepared["analysis_config_hash"]
        or score(facts, config).score != _fixture()["expected_score"]
    ):
        raise DraftSmokeError("source_analysis_changed_after_reservation")
    return (
        lead,
        message,
        version,
        facts,
        config,
    )


def main():
    parser = argparse.ArgumentParser(description="One controlled synthetic OpenAI draft")
    parser.add_argument("action", choices=["plan", "prepare-source", "run"])
    parser.add_argument("--report", type=Path, default=ROOT / ".local" / REPORT_NAME)
    parser.add_argument("--approve-live", action="store_true")
    parser.add_argument("--max-cost-usd", type=Decimal)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    if args.action == "prepare-source":
        prepare_source(args.report)
    elif args.action == "plan":
        prepared = preflight(args.output)
        print(
            f"Ready. 0 draft requests. Model {MODEL}; max 1 request; retries 0; "
            f"reserved cost ceiling ${prepared['cost_ceiling']}; configured limit ${MAX_COST_USD}. "
            "No SMTP, Telegram or IMAP."
        )
    elif not args.approve_live:
        raise SystemExit("Explicit --approve-live is required. 0 requests.")
    else:
        run(args.output, max_cost_usd=args.max_cost_usd)


if __name__ == "__main__":
    try:
        main()
    except (DraftSmokeError, SourceRestoreError) as error:
        # Only static application-owned codes; never echo SDK/database exceptions.
        raise SystemExit(f"Controlled draft smoke stopped: {error}. No automatic repeat.") from None
    except Exception:
        raise SystemExit(
            "Controlled draft smoke stopped; no automatic repeat. Inspect local state without sharing secrets."
        ) from None
