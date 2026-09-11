"""Verified Luna rates and provider usage normalization."""

from typing import Any, TypedDict

from .errors import ProviderFailure

# Official Luna pricing checked 2026-09-09. Reservations use the cache-write
# premium, while reported usage distinguishes regular, cached, and written input.
LONG_CONTEXT_THRESHOLD = 272_000
PRICES = {"gpt-5.6-luna": (0.25, 1.2)}
LONG_PRICES = {"gpt-5.6-luna": (0.5, 1.8)}
USAGE_PRICES = {"gpt-5.6-luna": (0.20, 0.02, 0.25, 1.2)}
LONG_USAGE_PRICES = {"gpt-5.6-luna": (0.40, 0.04, 0.50, 1.8)}

ReservationRates = tuple[float, float]
UsageRates = tuple[float, float, float, float]


class UsageMetadata(TypedDict):
    input_tokens: int
    output_tokens: int
    total_tokens: int
    input_token_details: dict[str, int]
    output_token_details: dict[str, int]
    input_cost: float
    output_cost: float
    total_cost: float


def reservation_rates(model: str, tokens: int) -> ReservationRates:
    """Return conservative input/output rates in USD per million tokens."""

    prices = LONG_PRICES if tokens > LONG_CONTEXT_THRESHOLD else PRICES
    return prices[model]


def usage_rates(model: str, tokens: int) -> UsageRates:
    """Return reported regular/cache/write/output rates for native usage."""

    prices = LONG_USAGE_PRICES if tokens > LONG_CONTEXT_THRESHOLD else USAGE_PRICES
    return prices[model]


def usage_metadata(usage: Any, model: str) -> UsageMetadata:
    """Normalize native Responses usage to LangSmith's token and cost schema."""

    def count(value: Any) -> int:
        if type(value) is not int or value < 0:
            raise ProviderFailure(
                "Provider returned invalid usage; reservation retained."
            )
        return value

    input_tokens = count(usage.input_tokens)
    output_tokens = count(usage.output_tokens)
    input_details = getattr(usage, "input_tokens_details", None)
    cached_tokens = count(getattr(input_details, "cached_tokens", 0))
    written_tokens = count(getattr(input_details, "cache_write_tokens", 0))
    output_details = getattr(usage, "output_tokens_details", None)
    reasoning_tokens = count(getattr(output_details, "reasoning_tokens", 0))
    if (
        cached_tokens + written_tokens > input_tokens
        or reasoning_tokens > output_tokens
    ):
        raise ProviderFailure(
            "Provider returned inconsistent usage; reservation retained."
        )

    regular_rate, cache_rate, write_rate, output_rate = usage_rates(model, input_tokens)
    input_cost = (
        (input_tokens - cached_tokens - written_tokens) * regular_rate
        + cached_tokens * cache_rate
        + written_tokens * write_rate
    ) / 1_000_000
    output_cost = output_tokens * output_rate / 1_000_000
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "input_token_details": {
            "cache_read": cached_tokens,
            "cache_creation": written_tokens,
        },
        "output_token_details": {"reasoning": reasoning_tokens},
        "input_cost": input_cost,
        "output_cost": output_cost,
        "total_cost": input_cost + output_cost,
    }
