import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import httpx2
import pytest
from app.adapters.ai import AIError, FakeAIAdapter, OpenAIAdapter, prepare_draft
from app.domain.analysis import AnalysisFacts, SemanticError, validate_facts
from app.domain.config import load_business_config
from app.domain.examples import labelled_examples
from openai import APIConnectionError, APIError, APITimeoutError, RateLimitError
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[2]
CONFIG = load_business_config(ROOT / "config" / "automation.yaml")
EXAMPLES = {x.id: x for x in labelled_examples(CONFIG)}


def response(facts=None, status="completed", output=None):
    return SimpleNamespace(
        status=status,
        output=output or [],
        output_parsed=facts,
        usage=SimpleNamespace(input_tokens=120, output_tokens=80),
    )


def adapter_for(result=None, error=None):
    parse = Mock(return_value=result, side_effect=error)
    client = SimpleNamespace(responses=SimpleNamespace(parse=parse))
    return OpenAIAdapter(client=client), parse


def test_strict_schema_all_properties_required_no_extra_keys():
    schema = AnalysisFacts.model_json_schema()
    for item in [schema, *schema["$defs"].values()]:
        if item.get("type") == "object":
            assert item["additionalProperties"] is False
            assert set(item["required"]) == set(item["properties"])
    data = EXAMPLES["hot_ru"].facts.model_dump()
    data["recipient"] = "attacker@example.test"
    with pytest.raises(ValidationError):
        AnalysisFacts.model_validate(data)


@pytest.mark.parametrize(
    "fit,service_id",
    [("fit", None), ("not_fit", "lead_automation"), ("unknown", "lead_automation")],
)
def test_service_fit_id_invariant(fit, service_id):
    data = EXAMPLES["hot_ru"].facts.model_dump()
    data.update(service_fit=fit, service_id=service_id)
    with pytest.raises(ValidationError):
        AnalysisFacts.model_validate(data)


def repair_request_facts():
    """A schema probe for the frozen non-fit category, never a production rule."""
    return {
        "service_fit": "not_fit",
        "service_id": None,
        "service_evidence": ["Автоматизацию заявок или интеграцию программ мы не заказываем."],
        "budgets": [],
        "urgency": "within_30_days",
        "urgency_evidence": ["Начать ремонт нужно в течение 10 дней."],
        "intent": "proposal",
        "intent_evidence": ["Пришлите предложение именно на ремонт техники."],
        "business_context": {
            "value": "склад упаковочных материалов",
            "evidence": ["У нас склад упаковочных материалов."],
        },
        "desired_outcome": {
            "value": "отремонтировать погрузчик и возобновить перемещение палет",
            "evidence": [
                "Нужен выезд механика для ремонта двигателя погрузчика, чтобы снова перемещать палеты."
            ],
        },
        "summary": "Складу нужен ремонт погрузчика; начать работы нужно в течение 10 дней. Бюджет не определён.",
        "missing_information": ["сумма бюджета"],
        "contradictions": [],
        "suggested_next_action": "review_fit",
        "language": "ru",
    }


def test_non_fit_repair_shape_and_null_service_id_are_valid():
    source = json.loads((ROOT / "evaluation/openai-cases.json").read_text(encoding="utf-8"))[
        "cases"
    ][5]["source"]
    facts = AnalysisFacts.model_validate(repair_request_facts())
    assert facts.service_fit == "not_fit" and facts.service_id is None
    assert facts.budgets == [] and facts.contradictions == []
    assert validate_facts(facts, source, CONFIG) is facts
    schema = AnalysisFacts.model_json_schema()
    assert set(schema["required"]) == set(schema["properties"])
    assert schema["properties"]["service_id"]["anyOf"][-1] == {"type": "null"}
    assert "service_fit=not_fit" in schema["properties"]["service_id"]["description"]
    assert "explicit mismatch" in schema["properties"]["service_evidence"]["description"]
    adapter, parse = adapter_for(response(facts))
    assert adapter.analyze(source, CONFIG) is facts
    instructions = parse.call_args.kwargs["instructions"]
    assert (
        "service_fit=not_fit means explicit mismatch and requires service_id=null" in instructions
    )
    assert "a not_fit decision needs a quote of the mismatch" in instructions


@pytest.mark.parametrize(
    "change,path,reason",
    [
        ({"service_id": "forklift_repair"}, (), "value_error"),
        ({"suggested_next_action": "reject"}, ("suggested_next_action",), "literal_error"),
        ({"budgets": None}, ("budgets",), "list_type"),
        ({"service_evidence": None}, ("service_evidence",), "list_type"),
        ({"service_evidence": []}, (), "value_error"),
        ({"service_fit": "not-fit"}, ("service_fit",), "literal_error"),
        ({"missing_information": None}, ("missing_information",), "list_type"),
    ],
)
def test_non_fit_plausible_schema_mistakes_are_rejected(change, path, reason):
    data = repair_request_facts()
    data.update(change)
    with pytest.raises(ValidationError) as caught:
        AnalysisFacts.model_validate(data)
    assert any(error["loc"] == path and error["type"] == reason for error in caught.value.errors())


def test_non_fit_missing_required_field_and_unsupported_service_are_distinct():
    data = repair_request_facts()
    del data["contradictions"]
    with pytest.raises(ValidationError) as caught:
        AnalysisFacts.model_validate(data)
    assert any(
        error["loc"] == ("contradictions",) and error["type"] == "missing"
        for error in caught.value.errors()
    )

    data = repair_request_facts()
    data.update(service_fit="fit", service_id="forklift_repair")
    facts = AnalysisFacts.model_validate(data)
    with pytest.raises(SemanticError, match="service_not_configured"):
        validate_facts(facts, " ".join(data["service_evidence"]), CONFIG)


@pytest.mark.parametrize("field", ["business_context", "desired_outcome"])
def test_scoring_facts_require_evidence(field):
    data = EXAMPLES["hot_ru"].facts.model_dump()
    data[field]["evidence"] = []
    with pytest.raises(ValidationError):
        AnalysisFacts.model_validate(data)


def test_evidence_must_be_exact_source_substring():
    example = EXAMPLES["hot_ru"]
    data = example.facts.model_dump()
    data["service_evidence"] = ["An invented quote"]
    with pytest.raises(SemanticError, match="evidence_not_in_source"):
        validate_facts(AnalysisFacts.model_validate(data), example.message, CONFIG)


def test_evidence_exact_match_and_safe_unicode_case_variants_preserve_original_quote():
    example = EXAMPLES["hot_en"]
    assert validate_facts(example.facts, example.message, CONFIG) == example.facts

    data = example.facts.model_dump()
    changed_case = data["service_evidence"][0].swapcase()
    data["service_evidence"] = [changed_case]
    facts = AnalysisFacts.model_validate(data)
    assert validate_facts(facts, example.message, CONFIG) is facts
    assert facts.service_evidence == [changed_case]

    source = example.message + " Café."
    data = example.facts.model_dump()
    data["business_context"] = {"value": "Café", "evidence": ["Cafe\u0301"]}
    facts = AnalysisFacts.model_validate(data)
    assert validate_facts(facts, source, CONFIG) is facts
    assert facts.business_context.evidence == ["Cafe\u0301"]


@pytest.mark.parametrize(
    "change",
    [
        lambda quote: quote.replace("need", "want"),
        lambda quote: quote.replace("need ", "need  "),
        lambda quote: " ".join(reversed(quote.split())),
        lambda quote: "Ｗ" + quote[1:],
    ],
)
def test_evidence_paraphrases_reordering_and_compatibility_folding_stay_rejected(change):
    example = EXAMPLES["hot_en"]
    data = example.facts.model_dump()
    changed_quote = change(data["service_evidence"][0])
    assert changed_quote != data["service_evidence"][0]
    data["service_evidence"] = [changed_quote]
    with pytest.raises(SemanticError, match="evidence_not_in_source"):
        validate_facts(AnalysisFacts.model_validate(data), example.message, CONFIG)


def test_suggested_next_action_enum_semantics_are_explained_to_model():
    schema = AnalysisFacts.model_json_schema()
    action_description = schema["properties"]["suggested_next_action"]["description"]
    assert "review_fit" in action_description and "Never use review_fit" in action_description
    assert "supplier comparisons" in action_description

    example = EXAMPLES["hot_en"]
    adapter, parse = adapter_for(response(example.facts))
    adapter.analyze(example.message, CONFIG)
    instructions = parse.call_args.kwargs["instructions"]
    assert "review_fit is only for uncertain or negative service fit" in instructions


def test_start_deadline_summary_contract_is_explained_to_model():
    description = AnalysisFacts.model_json_schema()["properties"]["summary"]["description"]
    assert "start" in description and "completion" in description

    example = EXAMPLES["hot_en"]
    adapter, parse = adapter_for(response(example.facts))
    adapter.analyze(example.message, CONFIG)
    instructions = parse.call_args.kwargs["instructions"]
    assert "start within X days" in instructions and "finish within X days" in instructions


@pytest.mark.parametrize(
    "quote,purpose,period,service_id",
    [
        (
            "Our service fee is exactly 450000 KZT one-time.",
            "services",
            "one_time",
            "lead_automation",
        ),
        ("The advertising spend is exactly 450000 KZT per month.", "ad_spend", "monthly", None),
        (
            "A single total of 450000 KZT one-time covers services and advertising.",
            "combined",
            "one_time",
            None,
        ),
        (
            "The budget is exactly 450000 KZT one-time; its purpose is unstated.",
            "unknown",
            "one_time",
            None,
        ),
    ],
)
def test_budget_purpose_categories_have_distinct_supported_examples(
    quote, purpose, period, service_id
):
    example = EXAMPLES["hot_en"]
    data = example.facts.model_dump()
    data["budgets"] = [
        {
            "minimum": "450000",
            "maximum": "450000",
            "currency": "KZT",
            "period": period,
            "purpose": purpose,
            "service_id": service_id,
            "evidence": [quote],
        }
    ]
    facts = AnalysisFacts.model_validate(data)
    assert validate_facts(facts, example.message + " " + quote, CONFIG) is facts

    description = AnalysisFacts.model_json_schema()["$defs"]["Budget"]["properties"]["purpose"][
        "description"
    ]
    assert "combined" in description and "even if no split" in description
    assert "unknown" in description and "does not identify" in description


def test_budget_evidence_casefold_keeps_model_quote_unchanged():
    example = EXAMPLES["hot_en"]
    quote = "The service fee is 450000 KZT one-time."
    model_quote = "the service fee is 450000 KZT one-time."
    data = example.facts.model_dump()
    data["budgets"] = [
        {
            "minimum": "450000",
            "maximum": "450000",
            "currency": "KZT",
            "period": "one_time",
            "purpose": "services",
            "service_id": "lead_automation",
            "evidence": [model_quote],
        }
    ]
    facts = AnalysisFacts.model_validate(data)
    assert validate_facts(facts, example.message + " " + quote, CONFIG) is facts
    assert facts.budgets[0].evidence == [model_quote]


def test_currency_cannot_be_inferred_from_dollar_symbol():
    example = EXAMPLES["budget_ambiguous_currency"]
    data = example.facts.model_dump()
    data["budgets"][0]["currency"] = "USD"
    with pytest.raises(SemanticError, match="budget_currency_not_supported"):
        validate_facts(AnalysisFacts.model_validate(data), example.message, CONFIG)


@pytest.mark.parametrize(
    "minimum,maximum", [("500", "100"), (1.5, "10"), ("NaN", None), ("-1", None)]
)
def test_budget_rejects_bad_ranges_float_and_nonfinite(minimum, maximum):
    data = EXAMPLES["budget_at_threshold"].facts.model_dump()
    data["budgets"][0].update(minimum=minimum, maximum=maximum)
    with pytest.raises(ValidationError):
        AnalysisFacts.model_validate(data)


def test_fabricated_budget_amount_rejected():
    example = EXAMPLES["budget_at_threshold"]
    data = example.facts.model_dump()
    data["budgets"][0].update(minimum="999999", maximum="999999")
    with pytest.raises(SemanticError, match="budget_amount_not_supported"):
        validate_facts(AnalysisFacts.model_validate(data), example.message, CONFIG)


def test_explicit_budget_period_and_purpose_cannot_be_relabelled():
    example = EXAMPLES["ad_spend"]
    data = example.facts.model_dump()
    data["budgets"][0]["purpose"] = "services"
    with pytest.raises(SemanticError, match="budget_purpose_conflict"):
        validate_facts(AnalysisFacts.model_validate(data), example.message, CONFIG)
    example = EXAMPLES["budget_at_threshold"]
    data = example.facts.model_dump()
    data["budgets"][0]["purpose"] = "ad_spend"
    with pytest.raises(SemanticError, match="budget_purpose_conflict"):
        validate_facts(AnalysisFacts.model_validate(data), example.message, CONFIG)
    example = EXAMPLES["budget_at_threshold"]
    data = example.facts.model_dump()
    data["budgets"][0]["period"] = "monthly"
    with pytest.raises(SemanticError, match="budget_period_not_supported"):
        validate_facts(AnalysisFacts.model_validate(data), example.message, CONFIG)


def test_explicit_day_count_must_match_urgency_bucket():
    example = EXAMPLES["within_90"]
    data = example.facts.model_dump()
    data["urgency"] = "within_30_days"
    with pytest.raises(SemanticError, match="urgency_evidence_conflict"):
        validate_facts(AnalysisFacts.model_validate(data), example.message, CONFIG)


def test_successful_provider_request_has_no_tools_or_persistence():
    example = EXAMPLES["hot_ru"]
    adapter, parse = adapter_for(response(example.facts))
    assert adapter.analyze(example.message, CONFIG) == example.facts
    call = parse.call_args.kwargs
    assert call["store"] is False
    assert "tools" not in call
    assert call["text_format"] is AnalysisFacts
    assert call["model"] == "gpt-4.1-mini-2025-04-14"
    assert call["input"][0]["role"] == "user"
    assert adapter.last_usage["input_tokens"] == 120
    assert adapter.last_usage["output_tokens"] == 80
    assert adapter.last_usage["schema_version"] == "lead-facts-1"
    parse.assert_called_once()


@pytest.mark.parametrize(
    "result,code",
    [
        (response(status="incomplete"), "ai_incomplete"),
        (response(status="failed"), "ai_provider"),
        (
            response(output=[SimpleNamespace(content=[SimpleNamespace(type="refusal")])]),
            "ai_refusal",
        ),
        (response(facts={}), "ai_schema"),
    ],
)
def test_provider_terminal_results_are_explicit(result, code):
    adapter, parse = adapter_for(result)
    with pytest.raises(AIError) as error:
        adapter.analyze(EXAMPLES["hot_ru"].message, CONFIG)
    assert error.value.code == code
    parse.assert_called_once()


def test_provider_semantic_error_is_separate_from_json_schema_error():
    example = EXAMPLES["hot_ru"]
    invalid = example.facts.model_copy(update={"service_evidence": ["Fabricated quote"]})
    adapter, _ = adapter_for(response(invalid))
    with pytest.raises(AIError) as error:
        adapter.analyze(example.message, CONFIG)
    assert error.value.code == "ai_semantic"
    assert error.value.retryable is False
    assert error.value.issues == ["evidence_not_in_source"]


def test_production_adapter_schema_failure_does_not_log_raw_enquiry(caplog):
    private_marker = "synthetic.private@example.test"
    data = repair_request_facts()
    data["suggested_next_action"] = private_marker
    with pytest.raises(ValidationError) as validation:
        AnalysisFacts.model_validate(data)
    adapter, parse = adapter_for(error=validation.value)
    with pytest.raises(AIError) as caught:
        adapter.analyze(private_marker, CONFIG)
    assert caught.value.code == "ai_schema"
    assert private_marker not in str(caught.value)
    assert private_marker not in caplog.text
    parse.assert_called_once()


REQUEST = httpx2.Request("POST", "https://api.openai.com/v1/responses")


@pytest.mark.parametrize(
    "error,code",
    [
        (APITimeoutError(REQUEST), "ai_timeout"),
        (
            RateLimitError("synthetic", response=httpx2.Response(429, request=REQUEST), body=None),
            "ai_rate_limit",
        ),
        (APIConnectionError(request=REQUEST), "ai_provider"),
        (APIError("synthetic", REQUEST, body=None), "ai_provider"),
        (json.JSONDecodeError("synthetic", "", 0), "ai_schema"),
    ],
)
def test_provider_failures_do_not_retry_in_adapter(error, code):
    adapter, parse = adapter_for(error=error)
    with pytest.raises(AIError) as caught:
        adapter.analyze(EXAMPLES["hot_ru"].message, CONFIG)
    assert caught.value.code == code
    assert caught.value.retryable == (code != "ai_schema")
    assert adapter.last_usage["input_tokens"] is None
    assert adapter.last_usage["output_tokens"] is None
    parse.assert_called_once()


def test_missing_key_never_falls_back_to_fake():
    with pytest.raises(AIError, match="ai_not_configured"):
        OpenAIAdapter(api_key=None)


def test_fake_never_constructs_provider_client(monkeypatch):
    constructor = Mock(side_effect=AssertionError("Network clients forbidden"))
    monkeypatch.setattr("app.adapters.ai.OpenAI", constructor)
    fake = FakeAIAdapter()
    for example in EXAMPLES.values():
        fake.analyze(example.message, CONFIG)
    constructor.assert_not_called()


@pytest.mark.parametrize("identifier", ["injection_ru", "injection_en", "html_text"])
def test_untrusted_instructions_do_not_enter_draft_or_permissions(identifier):
    facts = FakeAIAdapter().analyze(EXAMPLES[identifier].message, CONFIG)
    draft = prepare_draft(facts, CONFIG)
    assert set(type(draft).model_fields) == {"subject", "body"}
    assert "attacker" not in draft.body
    assert "<script>" not in draft.body
    assert "100" not in draft.body
    assert CONFIG.company_name in draft.body
