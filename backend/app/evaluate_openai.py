"""Explicit, bounded provider evaluation; no application settings, DB or jobs."""

import argparse
import getpass
import hashlib
import json
import logging
import re
import sys
import time
import warnings
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

import httpx
from openai import OpenAI
from pydantic import ValidationError

from app.adapters.ai import AIError, OpenAIAdapter
from app.domain.analysis import (
    SCHEMA_VERSION,
    AnalysisFacts,
    Budget,
    SemanticError,
    SupportedFact,
    validate_facts,
)
from app.domain.config import load_business_config
from app.domain.examples import labelled_examples
from app.domain.scoring import score

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "evaluation/openai-evaluator.json"
MODEL = "gpt-4.1-mini-2025-04-14"
MAX_SOURCE_CHARS = 4000
MAX_SOURCE_BYTES = 12000
MAX_REQUEST_BYTES = 32768
CONSERVATIVE_INPUT_TOKENS = 40000
MAX_OUTPUT_TOKENS = 2400
MAX_TIMEOUT_SECONDS = 40
PRICE_SOURCE = "https://developers.openai.com/api/docs/models/gpt-4.1-mini"


class EvaluationError(RuntimeError):
    """Only static, safe codes cross the command-line boundary."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _utc():
    return datetime.now(timezone.utc).isoformat()


def _json_file(path):
    try:
        raw = path.read_bytes()
        if len(raw) > 200000:
            raise EvaluationError("configuration_too_large")
        return json.loads(raw.decode("utf-8-sig")), hashlib.sha256(raw).hexdigest()
    except (OSError, UnicodeError, ValueError):
        raise EvaluationError("configuration_unreadable") from None


def _decimal(value):
    try:
        result = Decimal(str(value))
        if not result.is_finite():
            raise ValueError
        return result
    except (InvalidOperation, ValueError):
        raise EvaluationError("invalid_decimal") from None


def _load(config_path, today):
    path = Path(config_path)
    if not path.is_absolute():
        path = ROOT / path
    data, config_hash = _json_file(path)
    if not isinstance(data, dict) or set(data) != {"business_config", "cases_file", "pricing"}:
        raise EvaluationError("invalid_evaluator_config")
    try:
        business_path = (path.parent / data["business_config"]).resolve()
        business = load_business_config(business_path)
        business_hash = hashlib.sha256(business_path.read_bytes()).hexdigest()
        corpus_path = (path.parent / data["cases_file"]).resolve()
        corpus, corpus_hash = _json_file(corpus_path)
        frozen_hash = corpus_path.with_suffix(".sha256").read_text(encoding="utf-8").strip()
        if frozen_hash != corpus_hash:
            raise ValueError
        pricing = data["pricing"]
        if set(pricing) != {
            "model",
            "input_per_million_usd",
            "output_per_million_usd",
            "as_of",
            "source",
        }:
            raise ValueError
        price_date = date.fromisoformat(pricing["as_of"])
        if (
            pricing["model"] != MODEL
            or business.ai.model != MODEL
            or pricing["source"] != PRICE_SOURCE
            or _decimal(pricing["input_per_million_usd"]) != Decimal("0.40")
            or _decimal(pricing["output_per_million_usd"]) != Decimal("1.60")
            or not 0 <= (today - price_date).days <= 30
            or business.ai.max_output_tokens > MAX_OUTPUT_TOKENS
            or business.ai.timeout_seconds > MAX_TIMEOUT_SECONDS
        ):
            raise ValueError
        cases = corpus["cases"]
        if len(cases) != 10 or len({case["id"] for case in cases}) != 10:
            raise ValueError
        if [case["id"] for case in cases] != [f"case-{number:02d}" for number in range(1, 11)]:
            raise ValueError
        known_sources = {example.message for example in labelled_examples(business)}
        if len({case["source"] for case in cases}) != 10:
            raise ValueError
        for case in cases:
            source = case["source"]
            expected = case["expectations"]
            if (
                not isinstance(source, str)
                or not source.strip()
                or len(source) > MAX_SOURCE_CHARS
                or len(source.encode("utf-8")) > MAX_SOURCE_BYTES
                or source in known_sources
                or not isinstance(expected, dict)
                or expected["validation"] not in {"pass", "semantic_error"}
                or expected["scoring_status"] not in {"completed", "needs_review", "not_run"}
            ):
                raise ValueError
    except (OSError, TypeError, KeyError, ValueError):
        raise EvaluationError("invalid_configuration_or_cases") from None
    return (
        business,
        cases,
        pricing,
        {
            "evaluator_config_sha256": config_hash,
            "business_config_sha256": business_hash,
            "case_set_sha256": corpus_hash,
        },
    )


def _identifier(value):
    return (
        value if isinstance(value, str) and re.fullmatch(r"[a-zA-Z0-9_.:-]{1,200}", value) else None
    )


def _redact(value, key):
    if isinstance(value, str):
        return value.replace(key, "[REDACTED]") if key else value
    if isinstance(value, list):
        return [_redact(item, key) for item in value]
    if isinstance(value, dict):
        return {name: _redact(item, key) for name, item in value.items()}
    return value


_DIAGNOSTIC_FIELDS = (
    set(AnalysisFacts.model_fields) | set(Budget.model_fields) | set(SupportedFact.model_fields)
)
_RESPONSE_STATUSES = {"completed", "failed", "in_progress", "cancelled", "queued", "incomplete"}
_INCOMPLETE_REASONS = {"max_output_tokens", "content_filter"}


def _safe_schema_errors(error):
    """Retain only schema-owned paths and Pydantic error types, never input values."""
    result = []
    for issue in error.errors(include_url=False, include_context=False, include_input=False)[:12]:
        parts = []
        for part in issue.get("loc", ()):
            if isinstance(part, str):
                parts.append(part if part in _DIAGNOSTIC_FIELDS else "<unrecognized_field>")
            elif type(part) is int:
                parts.append(f"[{part}]" if 0 <= part <= 10 else "[index]")
            else:
                parts.append("<unrecognized_field>")
        path = ".".join(parts).replace(".[", "[") or "$"
        reason = issue.get("type")
        if not isinstance(reason, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,79}", reason):
            reason = "validation_error"
        result.append(
            {
                "field": parts[0] if parts else "$",
                "path": path,
                "reason": reason,
            }
        )
    return result


def _schema_diagnostic(record, *, validation_error=None, local_valid=False):
    """Evaluator-only metadata; provider text and SDK exception messages never persist."""
    if validation_error is not None:
        cause = "pydantic_validation"
        errors = _safe_schema_errors(validation_error)
    elif local_valid:
        cause, errors = "sdk_parse_mismatch", []
    elif record.get("output_text_count") != 1:
        cause, errors = "no_single_output_text", []
    else:
        cause, errors = "unclassified", []
    return {
        "cause": cause,
        "response_status": record.get("response_status"),
        "incomplete_reason": record.get("incomplete_reason"),
        "refusal_present": record.get("refusal_present", False),
        "output_text_count": record.get("output_text_count", 0),
        "output_text_phases": record.get("output_text_phases", []),
        "errors": errors,
    }


class GuardedTransport(httpx.BaseTransport):
    """One HTTPS endpoint, bounded requests; never retain request headers or bodies."""

    def __init__(self, inner, limit, *, api_key="synthetic-transport-key"):
        if type(limit) is not int or not 1 <= limit <= 10:
            raise EvaluationError("invalid_request_limit")
        self.inner, self.limit, self.api_key = inner, limit, api_key
        self.attempts = 0
        self.records = []

    def handle_request(self, request):
        if self.attempts >= self.limit:
            raise EvaluationError("request_limit_exceeded")
        if (
            request.method != "POST"
            or str(request.url) != "https://api.openai.com/v1/responses"
            or len(request.read()) > MAX_REQUEST_BYTES
        ):
            raise EvaluationError("request_contract_blocked")
        # SDK 3.x accepts ambient custom headers. Rebuild them so neither those
        # headers nor ambient org/project/auth credentials reach this request.
        request.headers = httpx.Headers(
            {
                "Host": "api.openai.com",
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Content-Length": str(len(request.content)),
            }
        )
        record = {
            "http_status": None,
            "actual_model": None,
            "request_id": None,
            "response_id": None,
            "usage": None,
            "parsed_text": None,
            "response_status": None,
            "incomplete_reason": None,
            "refusal_present": False,
            "output_text_count": 0,
            "output_text_phases": [],
            "transport_error": None,
        }
        self.attempts += 1
        self.records.append(record)
        try:
            response = self.inner.handle_request(request)
            record["http_status"] = response.status_code
            record["request_id"] = _identifier(response.headers.get("x-request-id"))
            response.read()
            if 200 <= response.status_code < 300:
                try:
                    payload = response.json()
                    record["actual_model"] = _identifier(payload.get("model"))
                    record["response_id"] = _identifier(payload.get("id"))
                    status = payload.get("status")
                    record["response_status"] = (
                        status
                        if isinstance(status, str) and status in _RESPONSE_STATUSES
                        else "other"
                    )
                    details = payload.get("incomplete_details")
                    if isinstance(details, dict):
                        reason = details.get("reason")
                        record["incomplete_reason"] = (
                            reason
                            if isinstance(reason, str) and reason in _INCOMPLETE_REASONS
                            else "other"
                        )
                    usage = payload.get("usage")
                    if isinstance(usage, dict) and all(
                        type(usage.get(key)) is int and usage[key] >= 0
                        for key in ("input_tokens", "output_tokens")
                    ):
                        record["usage"] = {
                            key: usage[key] for key in ("input_tokens", "output_tokens")
                        }
                    texts = []
                    for output in payload.get("output", []):
                        if not isinstance(output, dict):
                            continue
                        phase = output.get("phase")
                        safe_phase = (
                            phase
                            if phase == "final_answer"
                            else (None if phase is None else "other")
                        )
                        for part in output.get("content", []):
                            if not isinstance(part, dict):
                                continue
                            if part.get("type") == "refusal":
                                record["refusal_present"] = True
                            if part.get("type") == "output_text" and isinstance(
                                part.get("text"), str
                            ):
                                texts.append(part["text"])
                                if len(record["output_text_phases"]) < 3:
                                    record["output_text_phases"].append(safe_phase)
                    record["output_text_count"] = len(texts)
                    if len(texts) == 1:
                        record["parsed_text"] = texts[0]
                except (ValueError, TypeError, AttributeError, KeyError):
                    pass
            return response
        except httpx.TimeoutException:
            record["transport_error"] = "network_timeout"
            raise
        except httpx.TransportError:
            record["transport_error"] = "network_error"
            raise

    def close(self):
        self.inner.close()


def _path(data, path):
    for part in path.split("."):
        if not isinstance(data, dict) or part not in data:
            return None
        data = data[part]
    return data


def _budget_matches(actual, expected):
    for key, values in expected.items():
        value = actual.get(key)
        if key in {"minimum", "maximum"} and value is not None:
            if not any(
                candidate is not None and _decimal(value) == _decimal(candidate)
                for candidate in values
            ):
                return False
        elif value not in values:
            return False
    return True


def _expectations(facts, expected, validation, scoring):
    checks = []

    def check(criterion, passed):
        checks.append({"criterion": criterion, "passed": bool(passed)})

    if facts is not None:
        for path, allowed in expected.get("field_values", {}).items():
            check(path, _path(facts, path) in allowed)
        for path in expected.get("null_fields", []):
            check(path + ":null", _path(facts, path) is None)
        for path in expected.get("non_null_fields", []):
            check(path + ":non_null", _path(facts, path) is not None)
        check(
            "contradictions",
            bool(facts["contradictions"]) == (expected["contradictions"] == "nonempty"),
        )
        remaining = list(facts["budgets"])
        expected_budgets = expected.get("budgets", [])
        check("budget_count", len(remaining) == len(expected_budgets))

        # Maximum five budgets; exhaustive matching avoids a greedy ambiguous pairing.
        def match(budgets, wanted):
            if not wanted:
                return not budgets
            return any(
                _budget_matches(budget, wanted[0])
                and match(budgets[:i] + budgets[i + 1 :], wanted[1:])
                for i, budget in enumerate(budgets)
            )

        check("budget_facts", match(remaining, expected_budgets))
    check("server_validation", validation["status"] == expected["validation"])
    check(
        "required_issue_codes",
        set(expected.get("required_issue_codes", [])).issubset(validation["issues"]),
    )
    check(
        "scoring_status",
        (scoring["status"] if scoring else "not_run") == expected["scoring_status"],
    )
    return {
        "status": "not_evaluated"
        if facts is None
        else ("passed" if all(c["passed"] for c in checks) else "failed"),
        "checks": checks,
        "human_review": "pending",
    }


def _error_code(record, adapter_error):
    status = record.get("http_status")
    if record.get("transport_error"):
        return record["transport_error"]
    if status == 401:
        return "authentication_error"
    if status == 403:
        return "permission_error"
    if status == 429:
        return "quota_or_rate_limit"
    if status is not None and not 200 <= status < 300:
        return "provider_error" if status >= 500 else "api_contract_error"
    return adapter_error


def _result(case, business, adapter, guard, pricing):
    started, instant, before = _utc(), time.monotonic(), guard.attempts
    facts, error = None, None
    try:
        facts = adapter.analyze(case["source"], business)
    except AIError as exc:
        error = exc.code
    except Exception:
        # Provider/SDK exceptions may echo payloads or headers. Never stringify them.
        error = "local_request_error" if guard.attempts == before else "api_contract_error"
    record = guard.records[-1] if guard.attempts > before else {}
    error = _error_code(record, error)
    schema_status, validation = "not_checked", {"status": "not_run", "issues": []}
    schema_error = None
    if facts is None and record.get("parsed_text"):
        try:
            facts = AnalysisFacts.model_validate_json(record["parsed_text"])
        except ValidationError as exc:
            schema_status = "failed"
            schema_error = exc
        except ValueError:
            schema_status = "failed"
    if record:
        # Only the evaluator sees this transient provider text. Reports receive
        # bounded paths/reasons below, never a raw response or exception message.
        record["parsed_text"] = None
    if facts is not None:
        schema_status = "passed"
        try:
            validate_facts(facts, case["source"], business)
            validation = {"status": "pass", "issues": []}
        except SemanticError as exc:
            validation = {"status": "semantic_error", "issues": exc.issues}
    elif error == "ai_schema":
        schema_status = "failed"
    schema = {"status": schema_status}
    if error == "ai_schema":
        schema["diagnostic"] = _schema_diagnostic(
            record, validation_error=schema_error, local_valid=facts is not None
        )
    if record.get("http_status") == 200 and record.get("actual_model") != business.ai.model:
        error = "unexpected_model"
    usage = record.get("usage")
    if usage and (
        usage["input_tokens"] > CONSERVATIVE_INPUT_TOKENS
        or usage["output_tokens"] > business.ai.max_output_tokens
    ):
        error = "usage_limit_exceeded"
    scoring = (
        score(facts, business).model_dump(mode="json")
        if facts is not None and validation["status"] == "pass" and error is None
        else None
    )
    facts_json = facts.model_dump(mode="json") if facts is not None else None
    cost = (
        None
        if usage is None or record.get("actual_model") != business.ai.model
        else str(
            (
                Decimal(usage["input_tokens"]) * _decimal(pricing["input_per_million_usd"])
                + Decimal(usage["output_tokens"]) * _decimal(pricing["output_per_million_usd"])
            )
            / Decimal(1000000)
        )
    )
    return {
        "case_id": case["id"],
        "source": case["source"],
        "expectations": case["expectations"],
        "started_at_utc": started,
        "latency_ms": round((time.monotonic() - instant) * 1000),
        "api": {
            "success": record.get("http_status") is not None and 200 <= record["http_status"] < 300,
            **{
                key: record.get(key)
                for key in ("http_status", "actual_model", "request_id", "response_id")
            },
        },
        "schema": schema,
        "validation": validation,
        "facts": facts_json,
        "scoring": scoring,
        "expectation_checks": _expectations(facts_json, case["expectations"], validation, scoring),
        "usage": usage,
        "cost_usd": cost,
        "error_code": error,
    }


def _validate_key(value):
    if not isinstance(value, str) or not value:
        raise EvaluationError("invalid_api_key")
    # HTTP headers accept printable ASCII only. Catch pasted Unicode/control
    # characters before constructing the SDK request, without echoing the key.
    if any(not 33 <= ord(character) <= 126 for character in value):
        raise EvaluationError("invalid_api_key_characters")
    return value


def _hidden_key():
    if not sys.stdin.isatty():
        raise EvaluationError("interactive_secret_input_required")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            value = getpass.getpass("Ключ OpenAI только для этого запуска (ввод скрыт): ")
    except getpass.GetPassWarning:
        raise EvaluationError("secure_secret_input_unavailable") from None
    return _validate_key(value)


def run_evaluation(
    config_path=DEFAULT_CONFIG,
    *,
    action="plan",
    count=None,
    start_case=1,
    approve_live=False,
    max_cost_usd=None,
    output_path=None,
    transport=None,
    key_provider=None,
    today=None,
):
    """MockTransport injection exercises the exact SDK/adapter and the same gates."""
    business, cases, pricing, hashes = _load(
        config_path, today or datetime.now(timezone.utc).date()
    )
    if action not in {"plan", "live-one", "live-batch"}:
        raise EvaluationError("invalid_action")
    if action == "live-batch":
        if (
            type(count) is not int
            or type(start_case) is not int
            or not 1 <= start_case <= 10
            or not 1 <= count <= 10
            or start_case + count > 11
        ):
            raise EvaluationError("invalid_request_limit")
        limit = count
    else:
        if count is not None:
            raise EvaluationError("count_requires_live_batch")
        if start_case != 1:
            raise EvaluationError("start_case_requires_live_batch")
        limit = 1
    selected_cases = cases[start_case - 1 : start_case - 1 + limit]
    per_request = (
        Decimal(CONSERVATIVE_INPUT_TOKENS) * _decimal(pricing["input_per_million_usd"])
        + Decimal(business.ai.max_output_tokens) * _decimal(pricing["output_per_million_usd"])
    ) / Decimal(1000000)
    maximum = limit * per_request
    report = {
        "action": action,
        "execution_transport": "none"
        if action == "plan"
        else ("mock" if transport is not None else "network"),
        "status": "planned",
        "model": business.ai.model,
        "requested_model": business.ai.model,
        "prompt_version": business.ai.prompt_version,
        "schema_version": SCHEMA_VERSION,
        "scoring_version": business.scoring.version,
        "business_config_version": business.version,
        "adapter_source_sha256": hashlib.sha256(
            (Path(__file__).parent / "adapters/ai.py").read_bytes()
        ).hexdigest(),
        "request_limit": limit,
        "requests_attempted": 0,
        "started_at_utc": _utc(),
        "estimated_max_cost_usd": str(maximum),
        "per_request_max_cost_usd": str(per_request),
        "authorized_budget_usd": None,
        "pricing": pricing,
        "limits": {
            "source_chars": MAX_SOURCE_CHARS,
            "source_utf8_bytes": MAX_SOURCE_BYTES,
            "request_bytes": MAX_REQUEST_BYTES,
            "input_token_estimate": CONSERVATIVE_INPUT_TOKENS,
            "max_output_tokens": business.ai.max_output_tokens,
            "timeout_seconds": business.ai.timeout_seconds,
            "sdk_retries": 0,
            "evaluator_retries": 0,
        },
        **hashes,
        "results": [],
        "stop_reason": None,
        "total_cost_usd": "0",
        "known_cost_usd": "0",
        "selected_case_ids": [case["id"] for case in selected_cases],
        "scope": "provider_and_analysis_only; no_full_live_path; human_semantic_review_required",
    }
    if action == "plan":
        return report
    if approve_live is not True:
        raise EvaluationError("explicit_live_approval_required")
    budget = _decimal(max_cost_usd) if max_cost_usd is not None else None
    if budget is None or not (
        per_request if action == "live-batch" else maximum
    ) <= budget <= Decimal("1"):
        raise EvaluationError("explicit_cost_limit_required")
    report["authorized_budget_usd"] = str(budget)
    if output_path is None:
        raise EvaluationError("output_path_required")
    if transport is not None and not isinstance(transport, httpx.MockTransport):
        raise EvaluationError("only_mock_transport_injection_allowed")
    try:
        stream = Path(output_path).open("x", encoding="utf-8")
    except OSError:
        raise EvaluationError("output_not_writable_or_exists") from None

    key, guard = None, None

    def save():
        serialized = json.dumps(_redact(report, key), ensure_ascii=False, indent=2)
        try:
            stream.seek(0)
            stream.write(serialized + "\n")
            stream.truncate()
            stream.flush()
        except OSError:
            raise EvaluationError("output_write_failed") from None

    previous_logging_threshold = logging.root.manager.disable
    try:
        save()  # Prove the destination is writable before key entry or networking.
        key = _validate_key((key_provider or _hidden_key)())
        # Parent logger.disabled does not suppress descendants that propagate to
        # a root handler. The CLI has no other work; restore this process setting.
        logging.disable(logging.CRITICAL)
        guard = GuardedTransport(
            transport or httpx.HTTPTransport(retries=0, trust_env=False), limit, api_key=key
        )
        client = httpx.Client(
            transport=guard,
            trust_env=False,
            follow_redirects=False,
            timeout=business.ai.timeout_seconds,
        )
        with OpenAI(
            api_key=key,
            admin_api_key="",
            webhook_secret="",
            organization="",
            project="",
            base_url="https://api.openai.com/v1",
            max_retries=0,
            http_client=client,
            default_headers={"Authorization": f"Bearer {key}"},
        ) as sdk:
            adapter = OpenAIAdapter(client=sdk)
            report["status"] = "running"
            for case in selected_cases:
                # Reserve the conservative cost of the next request against
                # known actual usage before permitting another network call.
                if _decimal(report["known_cost_usd"]) + per_request > budget:
                    report["stop_reason"] = "cost_limit_reached"
                    report["status"] = "stopped"
                    save()
                    break
                result = _result(case, business, adapter, guard, pricing)
                report["results"].append(result)
                report["requests_attempted"] = guard.attempts
                known = sum(
                    (
                        _decimal(item["cost_usd"])
                        for item in report["results"]
                        if item["cost_usd"] is not None
                    ),
                    Decimal(0),
                )
                report["known_cost_usd"] = str(known)
                report["total_cost_usd"] = (
                    str(known)
                    if all(item["cost_usd"] is not None for item in report["results"])
                    else None
                )
                if result["error_code"] not in (None, "ai_semantic"):
                    report["stop_reason"] = result["error_code"]
                    report["status"] = "stopped"
                elif action == "live-batch" and result["cost_usd"] is None:
                    report["stop_reason"] = "usage_unavailable"
                    report["status"] = "stopped"
                save()
                if report["stop_reason"]:
                    break
            if report["status"] == "running":
                report["status"] = "completed"
    except EvaluationError as exc:
        report["status"], report["stop_reason"] = "stopped", exc.code
    except (KeyboardInterrupt, EOFError):
        report["status"], report["stop_reason"] = "stopped", "interrupted"
    except Exception:
        report["status"], report["stop_reason"] = "stopped", "evaluation_error"
    finally:
        if guard is not None:
            report["requests_attempted"] = guard.attempts
            if guard.attempts > len(report["results"]):
                report["total_cost_usd"] = None
        report["finished_at_utc"] = _utc()
        logging.disable(previous_logging_threshold)
        try:
            save()
        finally:
            stream.close()
    return _redact(report, key)


def _explanation(code):
    if code is None:
        return "нет"
    messages = {
        "authentication_error": "ключ не принят; проверьте настройку отдельно, повтор не выполнялся",
        "permission_error": "у ключа нет доступа; повтор не выполнялся",
        "quota_or_rate_limit": "провайдер сообщил об ограничении квоты или частоты; повтор не выполнялся",
        "network_timeout": "истекло время ожидания; результат и расходы могут быть неизвестны, повтор не выполнялся",
        "network_error": "соединение не удалось; результат и расходы могут быть неизвестны, повтор не выполнялся",
        "api_contract_error": "ответ не соответствует ожидаемому API-контракту; прогон остановлен",
        "local_request_error": "локальная ошибка подготовки запроса до отправки; OpenAI не ответил",
        "invalid_api_key_characters": "во вводе ключа есть пробел, управляющий или не-ASCII символ; запрос не отправлен",
        "ai_schema": "ответ не соответствует схеме анализа; прогон остановлен",
        "ai_refusal": "модель отказалась от анализа; прогон остановлен",
        "ai_incomplete": "ответ модели неполный; прогон остановлен",
        "unexpected_model": "провайдер вернул другую или неуказанную модель; стоимость не подтверждена",
        "usage_limit_exceeded": "usage превышает согласованные пределы; дальнейшие запросы запрещены",
        "usage_unavailable": "usage или цена неизвестны; batch остановлен до следующего запроса",
        "cost_limit_reached": "для следующего запроса не хватает консервативного резерва бюджета",
        "explicit_live_approval_required": "не указан явный флаг разрешения; внешний вызов запрещён",
        "explicit_cost_limit_required": "нужен явный бюджет не ниже резерва одного запроса и не более $1; внешний вызов запрещён",
        "invalid_configuration_or_cases": "проверьте модель, свежесть тарифа, пределы и неизменность размеченных примеров",
        "interactive_secret_input_required": "для скрытого ввода ключа нужен интерактивный терминал",
        "secure_secret_input_unavailable": "терминал не гарантирует скрытый ввод; ключ не запрашивается открыто",
        "output_not_writable_or_exists": "нужен новый доступный файл отчёта; существующий файл не перезаписывается",
        "output_write_failed": "запись отчёта не удалась; дальнейшие запросы запрещены",
        "interrupted": "проверка прервана; незавершённая попытка может иметь неизвестную стоимость",
    }
    return messages.get(
        code, "проверка остановлена; исправьте причину перед отдельным явно разрешённым запуском"
    )


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Ограниченная проверка анализатора; по умолчанию без API."
    )
    parser.add_argument(
        "action", nargs="?", choices=("plan", "live-one", "live-batch"), default="plan"
    )
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--count", type=int)
    parser.add_argument("--start-case", type=int, default=1)
    parser.add_argument("--approve-live", action="store_true")
    parser.add_argument("--max-cost-usd")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        # Display price/model before asking for the secret, even for an approved run.
        plan = run_evaluation(args.config, today=None)
        count = args.count if args.action == "live-batch" and args.count is not None else 1
        print(
            f"Модель: {plan['model']}; запросов: {count}; "
            f"кейсы: {args.start_case:02d}–{args.start_case + count - 1:02d}; "
            f"резерв одного запроса ${plan['per_request_max_cost_usd']}; "
            f"вся партия без остановки до ${_decimal(plan['per_request_max_cost_usd']) * count}."
        )
        result = run_evaluation(
            args.config,
            action=args.action,
            count=args.count,
            start_case=args.start_case,
            approve_live=args.approve_live,
            max_cost_usd=args.max_cost_usd,
            output_path=args.output,
        )
        print(
            f"Статус: {result['status']}; попыток запросов: {result['requests_attempted']}; причина остановки: {_explanation(result['stop_reason'])}."
        )
        return 0 if result["status"] in ("planned", "completed") else 1
    except EvaluationError as exc:
        print(f"Проверка остановлена: {exc.code}. {_explanation(exc.code)}.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
