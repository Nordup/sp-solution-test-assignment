"""Published token-rate and usage accounting behavior."""

from types import SimpleNamespace

import pytest

from browser_agent.model.pricing import LONG_CONTEXT_THRESHOLD, usage_metadata


def test_usage_pricing_accounts_for_cache_and_reasoning():
    usage = SimpleNamespace(
        input_tokens=1000,
        output_tokens=200,
        input_tokens_details=SimpleNamespace(cached_tokens=400, cache_write_tokens=200),
        output_tokens_details=SimpleNamespace(reasoning_tokens=150),
    )
    metrics = usage_metadata(usage, "gpt-5.6-luna")
    assert metrics["input_cost"] == pytest.approx(
        (400 * 0.2 + 400 * 0.02 + 200 * 0.25) / 1_000_000
    )
    assert metrics["output_cost"] == pytest.approx(200 * 1.2 / 1_000_000)
    assert metrics["input_token_details"] == {"cache_read": 400, "cache_creation": 200}
    assert metrics["output_token_details"] == {"reasoning": 150}


def test_long_context_usage_pricing_applies_published_multipliers():
    usage = SimpleNamespace(
        input_tokens=LONG_CONTEXT_THRESHOLD + 1,
        output_tokens=200,
        input_tokens_details=SimpleNamespace(cached_tokens=400, cache_write_tokens=200),
        output_tokens_details=SimpleNamespace(reasoning_tokens=150),
    )
    metrics = usage_metadata(usage, "gpt-5.6-luna")
    assert metrics["input_cost"] == pytest.approx(
        ((usage.input_tokens - 600) * 0.4 + 400 * 0.04 + 200 * 0.5) / 1_000_000
    )
    assert metrics["output_cost"] == pytest.approx(200 * 1.8 / 1_000_000)
