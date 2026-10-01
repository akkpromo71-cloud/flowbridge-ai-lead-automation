"""One isolated application-pipeline smoke; key stays in this process's memory.

The existing analysis adapter, guarded evaluator transport, worker and n8n steps
are reused. No second analyser and no provider fallback. This is synthetic only.
"""

import argparse
import json
import os
import secrets
import socket
import subprocess
import threading
import time
from pathlib import Path

import httpx
import uvicorn
from alembic import command
from alembic.config import Config
from openai import OpenAI
from sqlalchemy import func, select, text

from app.adapters.ai import OpenAIAdapter
from app.db import Database
from app.deployment import ensure_database_mode
from app.domain.analysis import AnalysisFacts, SemanticError, validate_facts
from app.domain.config import load_business_config
from app.evaluate_openai import GuardedTransport, _expectations, _hidden_key, _schema_diagnostic
from app.main import create_app
from app.models import (
    CONTROLLED_ANALYSIS_HISTORY,
    Analysis,
    Audit,
    IntegrationState,
    Job,
    JobStep,
    Lead,
    Operator,
)
from app.security import password_hasher
from app.settings import ROOT, Settings
from app.worker import run_once


def runtime_root():
    location = Path(os.environ["LOCALAPPDATA"]) / "AILeadAutomationPro"
    if "onedrive" in str(location).casefold() or location.is_relative_to(ROOT):
        raise RuntimeError("unsafe_private_runtime_path")
    location.mkdir(parents=True, exist_ok=True)
    return location


def controlled_settings(key=""):
    tokens = json.loads((runtime_root() / "controlled-runtime.json").read_text(encoding="utf-8"))
    return Settings(
        _env_file=None,
        mode="controlled",
        database_url="postgresql+psycopg://ai_leads_controlled@127.0.0.1:15432/ai_leads_controlled",
        public_url="http://127.0.0.1:8001",
        allowed_origins="http://127.0.0.1:8001",
        internal_token=tokens["INTERNAL_TOKEN"],
        n8n_webhook_token=tokens["N8N_WEBHOOK_TOKEN"],
        n8n_base_url="http://127.0.0.1:5683",
        openai_api_key=key,
        telegram_bot_token="",
        telegram_chat_id="",
        smtp_host="",
        smtp_password="",
        imap_host="",
        imap_password="",
        allow_external_sends=False,
        real_draft_enabled=False,
        max_job_attempts=1,
        ai_input_price_per_million="0.40",
        ai_output_price_per_million="1.60",
        ai_price_model="gpt-4.1-mini-2025-04-14",
        ai_price_source="https://developers.openai.com/api/docs/models/gpt-4.1-mini",
        ai_price_as_of="2026-09-28",
    )


def prepare():
    private = runtime_root()
    token_path = private / "controlled-runtime.json"
    if not token_path.exists():
        with token_path.open("x", encoding="utf-8") as stream:
            json.dump(
                {
                    "MODE": "controlled",
                    "INTERNAL_TOKEN": secrets.token_urlsafe(32),
                    "N8N_WEBHOOK_TOKEN": secrets.token_urlsafe(32),
                },
                stream,
            )
    settings = controlled_settings()
    db = Database(settings.database_url)
    try:
        with db.engine.begin() as connection:
            if connection.scalar(text("SELECT current_database()")) != "ai_leads_controlled":
                raise RuntimeError("controlled_database_mismatch")
            cfg = Config(str(ROOT / "alembic.ini"))
            cfg.attributes["connection"] = connection
            command.upgrade(cfg, "head")
        ensure_database_mode(db, "controlled")
        operator_path = private / "controlled-operator.json"
        email = "controlled.operator@example.com"
        with db.session.begin() as session:
            existing = session.scalar(select(Operator).where(Operator.email == email))
            if not existing:
                password = secrets.token_urlsafe(24)
                with operator_path.open("x", encoding="utf-8") as stream:
                    json.dump(
                        {
                            "email": email,
                            "password": password,
                            "purpose": "private-synthetic-controlled",
                        },
                        stream,
                    )
                session.add(
                    Operator(
                        email=email,
                        display_name="Controlled оператор",
                        password_hash=password_hasher.hash(password),
                    )
                )
            elif not operator_path.exists():
                raise RuntimeError("controlled_operator_access_missing")
    finally:
        db.engine.dispose()
    print(
        "Controlled DB, migrations and private service/operator configuration prepared. No OpenAI key read or saved."
    )


def preflight():
    settings = controlled_settings()
    config = load_business_config(settings.business_config)
    if config.ai.model != "gpt-4.1-mini-2025-04-14" or config.ai.max_output_tokens > 2400:
        raise RuntimeError("controlled_model_or_output_limit")
    db = Database(settings.database_url)
    try:
        ensure_database_mode(db, "controlled")
        with db.session() as session:
            if session.get(IntegrationState, CONTROLLED_ANALYSIS_HISTORY):
                raise RuntimeError("controlled_historical_analysis_consumed_do_not_repeat")
            if session.scalar(
                select(func.count()).select_from(JobStep).where(JobStep.reserved_tokens > 0)
            ):
                raise RuntimeError("controlled_attempt_already_reserved_do_not_repeat")
    finally:
        db.engine.dispose()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 8001))
    with httpx.Client(trust_env=False, timeout=5) as client:
        client.get("http://127.0.0.1:5683/healthz/readiness").raise_for_status()
    if not (ROOT / "frontend/dist/index.html").exists():
        raise RuntimeError("frontend_build_missing")
    return settings


def smoke(output):
    output = output.resolve()
    if not output.is_relative_to(ROOT / ".local") or output.exists():
        raise RuntimeError("output_must_be_new_project_local_file")
    settings = preflight()
    fixture = json.loads(
        (ROOT / "evaluation/controlled-pipeline-smoke.json").read_text(encoding="utf-8")
    )
    print(
        "Модель: gpt-4.1-mini-2025-04-14; запросов максимум 1; резерв до $0.01984; разрешённый предел $0.02."
    )
    key = _hidden_key()
    settings = controlled_settings(key)
    guard = GuardedTransport(httpx.HTTPTransport(retries=0), 1, api_key=key)
    http = httpx.Client(transport=guard, trust_env=False, timeout=40, follow_redirects=False)
    adapter = OpenAIAdapter(client=OpenAI(api_key=key, max_retries=0, http_client=http))
    app = create_app(settings, analysis_adapter=adapter)
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=8001, log_level="warning", access_log=False)
    )
    api_thread = threading.Thread(target=server.run, daemon=True)
    api_thread.start()
    stop = threading.Event()
    worker_settings = settings.model_copy(
        update={"openai_api_key": type(settings.openai_api_key)("")}
    )
    worker_db = Database(settings.database_url)

    def worker():
        with httpx.Client(trust_env=False, timeout=10) as client:
            while not stop.is_set():
                try:
                    run_once(worker_db, worker_settings, client=client)
                except Exception:
                    stop.set()  # No exception payload or secret logging.
                stop.wait(0.25)

    worker_thread = threading.Thread(target=worker, daemon=True)
    report = {
        "case": fixture,
        "model": settings.ai_price_model,
        "requests_attempted": 0,
        "status": "stopped",
    }
    name = f"Controlled-{secrets.token_hex(6)}"
    browser_returncode = None
    try:
        for _ in range(100):
            if server.started:
                break
            if not api_thread.is_alive():
                raise RuntimeError("controlled_api_start_failed")
            time.sleep(0.1)
        if not server.started:
            raise RuntimeError("controlled_api_start_timeout")
        worker_thread.start()
        allowed = {
            key: value
            for key, value in os.environ.items()
            if key.upper()
            in {
                "PATH",
                "SYSTEMROOT",
                "WINDIR",
                "COMSPEC",
                "TEMP",
                "TMP",
                "LOCALAPPDATA",
                "APPDATA",
                "USERPROFILE",
                "PATHEXT",
            }
        }
        allowed.update(
            CONTROLLED_OPERATOR_FILE=str(runtime_root() / "controlled-operator.json"),
            CONTROLLED_SMOKE_NAME=name,
        )
        result = subprocess.run(
            [
                "node",
                "node_modules/@playwright/test/cli.js",
                "test",
                "--config",
                "playwright.controlled.config.ts",
            ],
            cwd=ROOT / "frontend",
            env=allowed,
            timeout=150,
            check=False,
        )
        browser_returncode = result.returncode
        if browser_returncode == 0:
            for _ in range(40):
                with worker_db.session() as db:
                    pipeline_status = db.scalar(
                        select(Job.status)
                        .join(Lead)
                        .where(Lead.name == name, Job.kind == "lead_processing")
                    )
                if pipeline_status == "succeeded":
                    break
                time.sleep(0.25)
            else:
                report["local_error"] = "pipeline_finish_not_confirmed"
                browser_returncode = 1
    except (RuntimeError, subprocess.TimeoutExpired):
        report["local_error"] = "controlled_browser_or_runtime_failure"
    finally:
        stop.set()
        if worker_thread.is_alive():
            worker_thread.join(12)
        server.should_exit = True
        api_thread.join(50)
        worker_db.engine.dispose()
        http.close()
        report["requests_attempted"] = guard.attempts
        report["browser_exit_code"] = browser_returncode
        record = guard.records[0] if guard.records else {}
        report["api"] = {k: v for k, v in record.items() if k != "parsed_text"}
        database = Database(settings.database_url)
        try:
            with database.session() as db:
                lead = db.scalar(select(Lead).where(Lead.name == name))
                analysis = db.get(Analysis, lead.analysis_id) if lead and lead.analysis_id else None
                report["lead_id"] = lead.id if lead else None
                report["analysis"] = (
                    {
                        "facts": analysis.facts,
                        "result": analysis.result,
                        "usage": analysis.usage,
                        "model": analysis.model,
                        "latency_ms": analysis.latency_ms,
                    }
                    if analysis
                    else None
                )
                report["job_steps"] = (
                    [
                        {
                            "step": s.step,
                            "generation": s.generation,
                            "status": s.status,
                            "error_code": s.error_code,
                        }
                        for s in db.scalars(select(JobStep).join(Job).where(Job.lead_id == lead.id))
                    ]
                    if lead
                    else []
                )
                report["audit"] = (
                    [
                        {"kind": e.kind, "created_at": e.created_at.isoformat()}
                        for e in db.scalars(
                            select(Audit).where(Audit.lead_id == lead.id).order_by(Audit.created_at)
                        )
                    ]
                    if lead
                    else []
                )
        finally:
            database.engine.dispose()
        if analysis:
            semantic = {"status": "pass", "issues": []}
            try:
                validate_facts(
                    AnalysisFacts.model_validate(analysis.facts),
                    fixture["source"],
                    load_business_config(settings.business_config),
                )
            except SemanticError as error:
                semantic = {"status": "semantic_error", "issues": error.issues}
            report["schema"] = {"status": "passed"}
            report["semantic"] = semantic
            report["expectation_checks"] = _expectations(
                analysis.facts,
                fixture["expectations"],
                semantic,
                analysis.result,
            )
            report["score_checks"] = {
                "score": analysis.result["score"] == fixture["expected_score"],
                "temperature": analysis.result["temperature"] == fixture["expected_temperature"],
            }
            report["actual_model_matches"] = record.get("actual_model") == settings.ai_price_model
            report["factual_review"] = "pending_human_review_of_synthetic_facts"
            report["status"] = "completed" if browser_returncode == 0 else "stopped"
        elif record:
            error = None
            if record.get("parsed_text"):
                from pydantic import ValidationError

                try:
                    AnalysisFacts.model_validate_json(record["parsed_text"])
                except ValidationError as exc:
                    error = exc
            report["safe_diagnostic"] = _schema_diagnostic(record, validation_error=error)
        usage = record.get("usage")
        if usage:
            from decimal import Decimal

            report["estimated_cost_usd"] = str(
                (
                    Decimal(usage["input_tokens"]) * Decimal("0.40")
                    + Decimal(usage["output_tokens"]) * Decimal("1.60")
                )
                / Decimal(1_000_000)
            )
        else:
            report["estimated_cost_usd"] = None
        output.parent.mkdir(exist_ok=True)
        with output.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
        print(
            f"Статус: {report['status']}; реальных запросов: {guard.attempts}; отчёт: {output}. Не повторяйте запуск."
        )


def main():
    parser = argparse.ArgumentParser(description="Controlled synthetic application pipeline")
    parser.add_argument("action", choices=["prepare", "plan", "run"])
    parser.add_argument("--approve-live", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT / ".local/controlled-ai-smoke.json")
    args = parser.parse_args()
    if args.action == "prepare":
        prepare()
    elif args.action == "plan":
        preflight()
        print(
            "Ready. 0 requests. One analysis request; gpt-4.1-mini-2025-04-14; at most $0.01984; fake draft and communication."
        )
    elif not args.approve_live:
        raise SystemExit("Explicit --approve-live is required; 0 requests.")
    else:
        smoke(args.output)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        raise SystemExit(
            "Controlled setup/preflight stopped. No automatic repeat; inspect local readiness, never send the key to chat."
        ) from None
