"""Verified Luna rates and strict native-usage normalization."""

import math
from dataclasses import dataclass
from typing import Any, TypedDict

from .errors import ProviderFailure

LONG_CONTEXT_THRESHOLD = 272_000
TOKENS_PER_MILLION = 1_000_000


class UsageMetadata(TypedDict):
    """LangSmith-compatible token and cost metadata."""

    input_tokens: int
    output_tokens: int
    total_tokens: int
    input_token_details: dict[str, int]
    output_token_details: dict[str, int]
    input_cost: float
    output_cost: float
    total_cost: float


class AggregateUsage(TypedDict):
    """Task-level usage totals exposed by ``ModelClient.usage``."""

    input_tokens: int
    output_tokens: int
    total_tokens: int
    calls: int
    input_token_details: dict[str, int]
    output_token_details: dict[str, int]


@dataclass(frozen=True, slots=True)
class TokenRates:
    """USD per million tokens for one context-price tier."""

    regular_input: float
    cached_input: float
    cache_write_input: float
    output: float
    reserved_input: float

    def conservative_microusd(self, input_tokens: int, output_tokens: int) -> int:
        """Price tokens at reservation rates, rounded up to a microdollar."""

        return math.ceil(
            input_tokens * self.reserved_input + output_tokens * self.output
        )


@dataclass(frozen=True, slots=True)
class ModelPricing:
    """Normal and long-context rates for a supported model."""

    standard: TokenRates
    long_context: TokenRates

    def for_input(self, input_tokens: int) -> TokenRates:
        return (
            self.long_context
            if input_tokens > LONG_CONTEXT_THRESHOLD
            else self.standard
        )


# Official Luna pricing checked 2026-09-09. The conservative input rate uses
# the cache-write premium; reported usage distinguishes all native categories.
PRICES = {
    "gpt-5.6-luna": ModelPricing(
        standard=TokenRates(
            regular_input=0.20,
            cached_input=0.02,
            cache_write_input=0.25,
            output=1.20,
            reserved_input=0.25,
        ),
        long_context=TokenRates(
            regular_input=0.40,
            cached_input=0.04,
            cache_write_input=0.50,
            output=1.80,
            reserved_input=0.50,
        ),
    )
}


@dataclass(frozen=True, slots=True)
class PricedUsage:
    """Validated provider usage with conservative and reported costs."""

    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    cache_write_input_tokens: int
    reasoning_output_tokens: int
    input_cost_usd: float
    output_cost_usd: float
    conservative_microusd: int

    @classmethod
    def from_provider(cls, usage: Any, model: str) -> "PricedUsage":
        input_tokens = _token_count(getattr(usage, "input_tokens", None))
        output_tokens = _token_count(getattr(usage, "output_tokens", None))
        input_details = getattr(usage, "input_tokens_details", None)
        output_details = getattr(usage, "output_tokens_details", None)
        cached_tokens = _token_count(getattr(input_details, "cached_tokens", 0))
        written_tokens = _token_count(getattr(input_details, "cache_write_tokens", 0))
        reasoning_tokens = _token_count(getattr(output_details, "reasoning_tokens", 0))
        if (
            cached_tokens + written_tokens > input_tokens
            or reasoning_tokens > output_tokens
        ):
            raise ProviderFailure(
                "Provider returned inconsistent usage; reservation retained."
            )

        rates = PRICES[model].for_input(input_tokens)
        regular_tokens = input_tokens - cached_tokens - written_tokens
        input_cost = (
            regular_tokens * rates.regular_input
            + cached_tokens * rates.cached_input
            + written_tokens * rates.cache_write_input
        ) / TOKENS_PER_MILLION
        output_cost = output_tokens * rates.output / TOKENS_PER_MILLION
        return cls(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached_tokens,
            cache_write_input_tokens=written_tokens,
            reasoning_output_tokens=reasoning_tokens,
            input_cost_usd=input_cost,
            output_cost_usd=output_cost,
            conservative_microusd=rates.conservative_microusd(
                input_tokens, output_tokens
            ),
        )

    @property
    def total_cost_usd(self) -> float:
        return self.input_cost_usd + self.output_cost_usd

    def metadata(self) -> UsageMetadata:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.input_tokens + self.output_tokens,
            "input_token_details": {
                "cache_read": self.cached_input_tokens,
                "cache_creation": self.cache_write_input_tokens,
            },
            "output_token_details": {"reasoning": self.reasoning_output_tokens},
            "input_cost": self.input_cost_usd,
            "output_cost": self.output_cost_usd,
            "total_cost": self.total_cost_usd,
        }


@dataclass(slots=True)
class UsageTotals:
    """Accumulate successful response usage for one task."""

    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_input_tokens: int = 0
    reasoning_output_tokens: int = 0
    calls: int = 0
    reported_cost_usd: float = 0.0

    def add(self, priced: PricedUsage) -> None:
        self.input_tokens += priced.input_tokens
        self.output_tokens += priced.output_tokens
        self.cached_input_tokens += priced.cached_input_tokens
        self.cache_write_input_tokens += priced.cache_write_input_tokens
        self.reasoning_output_tokens += priced.reasoning_output_tokens
        self.calls += 1
        self.reported_cost_usd += priced.total_cost_usd

    def metadata(self) -> AggregateUsage:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.input_tokens + self.output_tokens,
            "calls": self.calls,
            "input_token_details": {
                "cache_read": self.cached_input_tokens,
                "cache_creation": self.cache_write_input_tokens,
            },
            "output_token_details": {"reasoning": self.reasoning_output_tokens},
        }


def conservative_cost_microusd(
    model: str, input_tokens: int, output_tokens: int
) -> int:
    """Return a cache-write-safe charge for a request or completed response."""

    return (
        PRICES[model]
        .for_input(input_tokens)
        .conservative_microusd(input_tokens, output_tokens)
    )


def price_usage(usage: Any, model: str) -> PricedUsage:
    """Validate and price native Responses usage exactly once."""

    return PricedUsage.from_provider(usage, model)


def usage_metadata(usage: Any, model: str) -> UsageMetadata:
    """Normalize native Responses usage to LangSmith's token and cost schema."""

    return price_usage(usage, model).metadata()


def _token_count(value: Any) -> int:
    if type(value) is not int or value < 0:
        raise ProviderFailure("Provider returned invalid usage; reservation retained.")
    return value
