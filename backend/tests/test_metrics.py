from types import SimpleNamespace

import pytest
from app.metrics import usage_summary
from app.settings import Settings
from pydantic import ValidationError


def priced_settings(**changes):
    return Settings(
        _env_file=None,
        ai_input_price_per_million="0.50",
        ai_output_price_per_million="2.00",
        ai_price_model="synthetic-model",
        ai_price_source="Synthetic tariff for tests; not a real provider price",
        ai_price_as_of="2026-09-26",
        **changes,
    )


def analysis(provider="openai", model="synthetic-model"):
    return SimpleNamespace(
        model=model,
        usage={"provider": provider, "input_tokens": 1000, "output_tokens": 100},
    )


def test_configured_price_decimal_and_source():
    result = usage_summary([analysis()], 1, priced_settings())
    assert result["estimated_cost"]["amount"] == "0.000700"
    assert result["estimated_cost"]["as_of"] == "2026-09-26"
    assert result["unknown_usage_calls"] == 0


def test_unknown_failed_request_cannot_be_zero_cost():
    result = usage_summary([analysis()], 2, priced_settings())
    assert result["unknown_usage_calls"] == 1
    assert result["estimated_cost"] is None


def test_missing_tariff_and_model_mismatch_have_no_estimate():
    assert usage_summary([analysis()], 1, Settings(_env_file=None))["estimated_cost"] is None
    assert (
        usage_summary([analysis(model="different")], 1, priced_settings())["estimated_cost"] is None
    )


def test_demo_not_counted_as_paid_usage():
    result = usage_summary([analysis(provider="fake")], 0, Settings(_env_file=None))
    assert result["ai_calls"] == result["input_tokens"] == result["output_tokens"] == 0
    assert result["estimated_cost"] is None


def test_partial_tariff_is_invalid():
    with pytest.raises(ValidationError, match="both rates"):
        Settings(_env_file=None, ai_input_price_per_million="1")
