"""Free synthetic historical-source restoration in isolated test PostgreSQL."""

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import httpx
import pytest
from app import controlled_draft_smoke as smoke
from app import controlled_smoke
from app import controlled_source as source_module
from app.adapters.ai import AIError
from app.domain.config import load_business_config
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
)
from app.processing import execute_step
from app.settings import ROOT
from sqlalchemy import func, select
from test_controlled_draft_smoke import _response
from test_processing import create_lead_job

pytestmark = pytest.mark.postgres


@pytest.fixture
def historical_source(tmp_path, monkeypatch):
    # A portable mock report, never the owner's private live evidence.
    local_root = tmp_path / "synthetic-source"
    (local_root / ".local").mkdir(parents=True)
    (local_root / "evaluation").mkdir()
    (local_root / "config").mkdir()
    (local_root / "config/automation.yaml").write_bytes(
        (ROOT / "config/automation.yaml").read_bytes()
    )
    (local_root / ".local" / source_module.REPORT_NAME).write_bytes(
        (ROOT / "backend/tests/fixtures/controlled-analysis-report.json").read_bytes()
    )
    (local_root / "evaluation/controlled-pipeline-smoke.json").write_bytes(
        (ROOT / "evaluation/controlled-pipeline-smoke.json").read_bytes()
    )
    monkeypatch.setattr(source_module, "ROOT", local_root)
    monkeypatch.setattr(smoke, "ROOT", local_root)
    return source_module.verified_report(
        local_root / ".local" / source_module.REPORT_NAME,
        load_business_config(ROOT / "config/automation.yaml"),
    )


@pytest.fixture
def controlled_db(database, settings, monkeypatch):
    with database.session.begin() as db:
        db.add(IntegrationState(name="deployment", state="ready", detail={"mode": "controlled"}))
    runtime = SimpleNamespace(
        database_url=settings.database_url, business_config=ROOT / "config/automation.yaml"
    )
    monkeypatch.setattr(smoke, "controlled_settings", lambda: runtime)
    monkeypatch.setattr(controlled_smoke, "controlled_settings", lambda: runtime)
    return database


def _counts(database):
    with database.session() as db:
        return {
            model.__name__: db.scalar(select(func.count()).select_from(model))
            for model in (Lead, Analysis, Message, MessageVersion, Job, JobStep, Approval, Audit)
        }


def test_restore_is_idempotent_atomic_and_truthful(controlled_db, historical_source, monkeypatch):
    forbidden = Mock(side_effect=AssertionError("No external calls or hidden key"))
    monkeypatch.setattr(smoke, "_hidden_key", forbidden)
    monkeypatch.setattr(smoke, "OpenAIDraftAdapter", forbidden)
    monkeypatch.setattr("app.adapters.ai.OpenAIAdapter", forbidden)
    for name in ("smtp_send", "telegram_notify", "sync_mailbox"):
        monkeypatch.setattr(f"app.communication.{name}", forbidden)
    report = source_module.ROOT / ".local" / source_module.REPORT_NAME
    assert smoke.prepare_source(report) == "restored"
    with controlled_db.session() as db:
        before = dict(db.get(IntegrationState, CONTROLLED_ANALYSIS_HISTORY).detail)
    assert smoke.prepare_source(report) == "already_restored"
    assert _counts(controlled_db) == {
        "Lead": 1,
        "Analysis": 1,
        "Message": 1,
        "MessageVersion": 1,
        "Job": 0,
        "JobStep": 0,
        "Approval": 0,
        "Audit": 1,
    }
    with controlled_db.session() as db:
        marker = db.get(IntegrationState, CONTROLLED_ANALYSIS_HISTORY)
        assert marker.detail == before
        analysis = db.get(Analysis, marker.detail["analysis_id"])
        assert analysis.facts == historical_source.facts
        assert analysis.result == historical_source.result
        assert analysis.usage["provenance"] == source_module.PROVENANCE
        assert (
            analysis.usage["historical_provider_reference"]
            == historical_source.historical_reference
        )
        assert db.scalar(select(Audit.kind)) == "controlled.source_restored"
        assert db.get(IntegrationState, smoke.RESERVATION) is None
        assert marker.detail["draft_requests_attempted"] == 0
        version = db.get(MessageVersion, marker.detail["source_version_id"])
        assert version.generation["synthetic_placeholder_regenerated"] is True
        assert version.generation["provenance"] == source_module.PROVENANCE
        assert version.generation["provider"] == "fake"
    assert (
        smoke.preflight(source_module.ROOT / ".local" / f"restored-plan-{uuid4()}.json")[
            "cost_ceiling"
        ]
        > 0
    )
    forbidden.assert_not_called()


def test_concurrent_restore_has_one_source(controlled_db, historical_source):
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(
            pool.map(
                lambda _: source_module.restore_source(controlled_db, historical_source), range(2)
            )
        )
    assert sorted(statuses) == ["already_restored", "restored"]
    assert _counts(controlled_db)["Lead"] == 1
    assert _counts(controlled_db)["Audit"] == 1


@pytest.mark.parametrize("mode", ["demo", "live", "test"])
def test_other_database_modes_never_written(database, historical_source, mode):
    with database.session.begin() as db:
        db.add(IntegrationState(name="deployment", state="ready", detail={"mode": mode}))
    with pytest.raises(source_module.SourceRestoreError, match="controlled_database_required"):
        source_module.restore_source(database, historical_source)
    assert all(value == 0 for value in _counts(database).values())


def test_existing_unrelated_source_is_not_overwritten(controlled_db, historical_source, settings):
    create_lead_job(controlled_db, settings)
    before = _counts(controlled_db)
    with pytest.raises(source_module.SourceRestoreError, match="controlled_source_not_empty"):
        source_module.restore_source(controlled_db, historical_source)
    assert _counts(controlled_db) == before


@pytest.mark.parametrize(
    "changed", ["facts", "result", "config", "message", "version", "report_hash"]
)
def test_mismatch_stops_instead_of_repair(controlled_db, historical_source, changed):
    source_module.restore_source(controlled_db, historical_source)
    with controlled_db.session.begin() as db:
        marker = db.get(IntegrationState, CONTROLLED_ANALYSIS_HISTORY)
        analysis = db.get(Analysis, marker.detail["analysis_id"])
        if changed == "facts":
            analysis.facts = {**analysis.facts, "summary": "Synthetic altered summary"}
        elif changed == "result":
            analysis.result = {**analysis.result, "score": 94}
        elif changed == "config":
            analysis.config_version = "synthetic_changed"
        elif changed == "message":
            db.get(Message, marker.detail["message_id"]).state = "approved"
        elif changed == "version":
            db.get(
                MessageVersion, marker.detail["source_version_id"]
            ).body = "Synthetic changed body"
    if changed == "report_hash":
        historical_source = replace(historical_source, digest="0" * 64)
    counts_before = _counts(controlled_db)
    with pytest.raises(source_module.SourceRestoreError, match="restored_source_"):
        source_module.restore_source(controlled_db, historical_source)
    assert _counts(controlled_db) == counts_before


@pytest.mark.parametrize(
    "change", ["request_count", "model", "schema", "semantic", "source", "budget", "usage"]
)
def test_unverified_report_rejected_before_database(
    historical_source, monkeypatch, tmp_path, change
):
    document = json.loads(
        (source_module.ROOT / ".local" / source_module.REPORT_NAME).read_text(encoding="utf-8")
    )
    if change == "request_count":
        document["requests_attempted"] = 0
    elif change == "model":
        document["api"]["actual_model"] = "wrong-model"
    elif change == "schema":
        document["schema"]["status"] = "failed"
    elif change == "semantic":
        document["semantic"]["status"] = "semantic_error"
    elif change == "source":
        document["case"]["source"] += " Synthetic altered source"
    elif change == "budget":
        document["analysis"]["facts"]["budgets"][0]["minimum"] = "450000"
    elif change == "usage":
        document["api"]["usage"]["input_tokens"] += 1
    (tmp_path / ".local").mkdir()
    (tmp_path / "evaluation").mkdir()
    report = tmp_path / ".local" / source_module.REPORT_NAME
    report.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "evaluation/controlled-pipeline-smoke.json").write_bytes(
        (ROOT / "evaluation/controlled-pipeline-smoke.json").read_bytes()
    )
    monkeypatch.setattr(source_module, "ROOT", tmp_path)
    with pytest.raises(source_module.SourceRestoreError, match="verified_report_validation_failed"):
        source_module.verified_report(report, historical_source.config)


def test_restore_rollback_has_no_partial_rows(controlled_db, historical_source, monkeypatch):
    monkeypatch.setattr(
        source_module.FakeDraftAdapter, "draft", Mock(side_effect=RuntimeError("synthetic failure"))
    )
    with pytest.raises(RuntimeError, match="synthetic failure"):
        source_module.restore_source(controlled_db, historical_source)
    assert all(value == 0 for value in _counts(controlled_db).values())
    with controlled_db.session() as db:
        assert db.get(IntegrationState, CONTROLLED_ANALYSIS_HISTORY) is None


def test_restore_does_not_reset_consumed_draft(controlled_db, historical_source):
    source_module.restore_source(controlled_db, historical_source)
    before = smoke.preflight(source_module.ROOT / ".local" / f"sealed-{uuid4()}.json")
    smoke.reserve(controlled_db, before)
    with pytest.raises(source_module.SourceRestoreError, match="draft_attempt_already_reserved"):
        source_module.restore_source(controlled_db, historical_source)
    with controlled_db.session() as db:
        assert db.get(IntegrationState, smoke.RESERVATION).state == "reserved"


def test_historical_seal_blocks_analysis_launcher_and_application_call(
    controlled_db, historical_source, settings, monkeypatch
):
    source_module.restore_source(controlled_db, historical_source)
    forbidden = Mock(side_effect=AssertionError("No key/HTTP/provider call"))
    monkeypatch.setattr(controlled_smoke, "_hidden_key", forbidden)
    monkeypatch.setattr(controlled_smoke.httpx, "Client", forbidden)
    with pytest.raises(RuntimeError, match="controlled_historical_analysis_consumed"):
        controlled_smoke.preflight()
    config, job = create_lead_job(controlled_db, settings)
    adapter = Mock(last_usage={"input_tokens": None, "output_tokens": None})
    adapter.analyze = forbidden
    controlled = settings.model_copy(update={"mode": "controlled"})
    with pytest.raises(AIError, match="controlled_historical_analysis_consumed"):
        execute_step(
            controlled_db,
            controlled,
            config,
            job.id,
            job.generation,
            "analyze",
            analysis_adapter=adapter,
        )
    forbidden.assert_not_called()


def test_restored_source_supports_future_draft_mock_without_analysis(
    controlled_db, historical_source, monkeypatch, tmp_path
):
    source_module.restore_source(controlled_db, historical_source)
    before = smoke.preflight(source_module.ROOT / ".local" / f"future-draft-{uuid4()}.json")
    monkeypatch.setattr(smoke, "preflight", lambda output: before)
    calls = []

    def handler(request):
        calls.append(request)
        assert "460000" in request.content.decode()
        return _response()

    result = smoke.run(
        tmp_path / "mock-draft.json",
        max_cost_usd="0.02",
        transport=httpx.MockTransport(handler),
        key_provider=lambda: "synthetic-key",
    )
    assert result["status"] == "completed" and len(calls) == 1
    assert result["source_provenance"]["provenance"] == source_module.PROVENANCE
    assert (
        result["source_provenance"]["historical_provider_reference"]
        == historical_source.historical_reference
    )
    assert result["human_approval_required"] is True and result["sent"] is False
    with controlled_db.session() as db:
        assert db.scalar(select(func.count()).select_from(JobStep)) == 0
        assert db.get(IntegrationState, CONTROLLED_ANALYSIS_HISTORY).state == "restored"
