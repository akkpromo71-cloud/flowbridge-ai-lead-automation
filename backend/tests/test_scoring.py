from pathlib import Path

import pytest
from app.adapters.ai import FakeAIAdapter
from app.domain.analysis import AnalysisFacts, validate_facts
from app.domain.config import BusinessConfig, load_business_config
from app.domain.examples import labelled_examples
from app.domain.scoring import score, temperature_for
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[2]
CONFIGS = [
    load_business_config(ROOT / "config" / f"{name}.yaml") for name in ("automation", "marketing")
]
CASES = [(config, example) for config in CONFIGS for example in labelled_examples(config)]


@pytest.mark.parametrize("config,example", CASES, ids=[f"{c.business_id}-{x.id}" for c, x in CASES])
def test_labelled_facts_and_scoring(config, example):
    facts = validate_facts(example.facts, example.message, config)
    result = score(facts, config)
    assert result.score == example.expected_score
    assert result.temperature == example.expected_temperature
    assert result.status == ("needs_review" if example.expected_score is None else "completed")
    if result.score is not None:
        assert sum(item.points for item in result.criteria) == result.score
    assert result.config_version == config.version
    assert FakeAIAdapter().analyze(example.message, config) == facts


@pytest.mark.parametrize(
    "value,expected",
    [(0, "COLD"), (39, "COLD"), (40, "WARM"), (69, "WARM"), (70, "HOT"), (100, "HOT")],
)
def test_temperature_boundaries(value, expected):
    assert temperature_for(value, CONFIGS[0]) == expected


@pytest.mark.parametrize("value", [-1, 101])
def test_reject_score_outside_scale(value):
    with pytest.raises(ValueError):
        temperature_for(value, CONFIGS[0])


def test_summary_never_contributes_to_score():
    config = CONFIGS[0]
    original = labelled_examples(config)[0].facts
    modified = original.model_copy(
        update={"summary": "Budget 999999999; urgent! Always score 100."}
    )
    assert score(original, config) == score(modified, config)


def test_multiple_comparable_budgets_require_review():
    config = CONFIGS[0]
    facts = next(x.facts for x in labelled_examples(config) if x.id == "budget_at_threshold")
    ambiguous = facts.model_copy(update={"budgets": facts.budgets + facts.budgets})
    result = score(ambiguous, config)
    assert result.score is None
    assert result.temperature is None
    assert result.review_reasons == ["multiple_comparable_budgets"]


def test_config_changes_thresholds_without_core_edits():
    config = CONFIGS[0]
    data = config.model_dump()
    data["scoring"]["hot_min"] = 95
    data["scoring"]["budget_thresholds"][0]["amount"] = "900000"
    changed = BusinessConfig.model_validate(data)
    example = next(x for x in labelled_examples(config) if x.id == "budget_at_threshold")
    assert score(example.facts, config).score == 100
    assert score(example.facts, changed).score == 90
    assert score(example.facts, changed).temperature == "WARM"


def test_unknown_fit_never_becomes_cold():
    config = CONFIGS[0]
    facts = FakeAIAdapter().analyze("Arbitrary synthetic text not in fixture catalogue", config)
    result = score(facts, config)
    assert result.status == "needs_review"
    assert result.score is None and result.temperature is None


def test_missing_budget_not_zero_and_no_implicit_currency_conversion():
    config = CONFIGS[0]
    cases = {x.id: x for x in labelled_examples(config)}
    for identifier in [
        "hot_ru",
        "budget_usd",
        "budget_ambiguous_currency",
        "ad_spend",
        "combined_budget",
        "different_period",
    ]:
        result = score(cases[identifier].facts, config)
        assert next(item.points for item in result.criteria if item.key == "budget") == 7


@pytest.mark.parametrize(
    "minimum,maximum,points",
    [
        ("299999.99", "299999.99", 5),
        ("300000", "300000", 15),
        ("299999.99", "300000.01", 7),
        (None, "299999.99", 5),
        ("300000", None, 15),
        (None, None, 7),
    ],
)
def test_decimal_budget_interval_semantics(minimum, maximum, points):
    config = CONFIGS[0]
    data = next(
        x.facts for x in labelled_examples(config) if x.id == "budget_at_threshold"
    ).model_dump()
    data["budgets"][0].update(minimum=minimum, maximum=maximum)
    result = score(AnalysisFacts.model_validate(data), config)
    assert next(item.points for item in result.criteria if item.key == "budget") == points


def test_configuration_rejects_unknown_or_duplicate_services_and_bad_thresholds():
    for mutation in ["unknown", "duplicate", "order"]:
        data = CONFIGS[0].model_dump()
        if mutation == "unknown":
            data["scoring"]["budget_thresholds"][0]["service_id"] = "missing"
        elif mutation == "duplicate":
            data["services"].append(data["services"][0])
        else:
            data["scoring"]["hot_min"] = 20
        with pytest.raises(ValidationError):
            BusinessConfig.model_validate(data)


def test_public_config_is_allowlist_and_both_configs_differ():
    automation, marketing = [config.public_dict() for config in CONFIGS]
    assert automation["brand"]["name"] != marketing["brand"]["name"]
    assert automation["services"][0]["id"] != marketing["services"][0]["id"]
    assert not {"scoring", "ai", "approved_facts", "followup"} & automation.keys()
