"""Native Responses calls with bounded retries and an in-memory spending cap."""

import asyncio
import math

from openai import APIConnectionError, APIStatusError, AsyncOpenAI

from .budget import Budget
from .context import ContextOverflow

# Official Luna pricing checked 2026-09-09; reserve the cache-write premium.
# Microdollars per token: standard input $0.20/M, conservative input $0.25/M;
# output $1.20/M. Charging input conservatively also covers cached requests.
PRICES = {"gpt-5.6-luna": (0.25, 1.2)}


class ProviderFailure(RuntimeError):
    pass


class Gateway:
    def __init__(self, settings, *, client=None, emit=None, sleep=asyncio.sleep):
        if settings.model not in PRICES:
            raise ProviderFailure(
                "No verified price for this model; configure gpt-5.6-luna."
            )
        self.settings = settings
        self.client = client or AsyncOpenAI(
            api_key=settings.api_key.get_secret_value(), max_retries=0, timeout=60
        )
        self.emit = emit or (lambda *_: None)
        self.sleep = sleep
        self.budget = Budget(settings.budget_usd)
        self.usage = {"input_tokens": 0, "output_tokens": 0, "calls": 0}

    @property
    def cost_usd(self):
        return self.budget.cost_usd

    async def call(self, request, purpose="actor"):
        req = dict(
            request,
            model=self.settings.model,
            reasoning={"effort": self.settings.reasoning},
        )
        rates = PRICES[self.settings.model]
        for attempt in range(self.settings.max_retries + 1):
            try:
                count = await self.client.responses.input_tokens.count(**req)
                break
            except (APIConnectionError, APIStatusError) as exc:
                await self._wait_to_retry(exc, attempt, purpose="input_count")
        tokens = count.input_tokens
        if not isinstance(tokens, int) or tokens < 0:
            raise ProviderFailure("Provider returned an invalid input count.")
        if tokens > self.settings.max_input_tokens:
            raise ContextOverflow(
                f"Request needs {tokens} input tokens; cap is {self.settings.max_input_tokens}."
            )
        reserved = math.ceil(
            tokens * rates[0] + self.settings.max_output_tokens * rates[1]
        )
        for attempt in range(self.settings.max_retries + 1):
            self.budget.reserve(reserved)
            self.emit(
                "model_admitted",
                {
                    "purpose": purpose,
                    "attempt": attempt + 1,
                    "input_tokens": tokens,
                    "reserved_microusd": reserved,
                },
            )
            try:
                response = await self.client.responses.create(
                    **req,
                    max_output_tokens=self.settings.max_output_tokens,
                    store=False,
                )
            except (APIConnectionError, APIStatusError) as exc:
                # Unknown usage remains fully charged; never refund a lost response.
                await self._wait_to_retry(exc, attempt, purpose=purpose)
                continue
            usage = getattr(response, "usage", None)
            if usage is None:
                raise ProviderFailure(
                    "Provider omitted usage; conservative reservation retained."
                )
            actual = math.ceil(
                usage.input_tokens * rates[0] + usage.output_tokens * rates[1]
            )
            self.budget.reconcile(reserved, actual)
            self.usage["input_tokens"] += usage.input_tokens
            self.usage["output_tokens"] += usage.output_tokens
            self.usage["calls"] += 1
            self.emit(
                "model_usage",
                {
                    "purpose": purpose,
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "cost_microusd": actual,
                    "cost_usd": self.cost_usd,
                },
            )
            return response
        raise ProviderFailure("Retries exhausted.")

    async def _wait_to_retry(self, exc, attempt, *, purpose):
        retryable = isinstance(exc, APIConnectionError) or getattr(
            exc, "status_code", 0
        ) in {408, 409, 429, 500, 502, 503, 504}
        if not retryable or attempt == self.settings.max_retries:
            raise ProviderFailure(
                f"{purpose} request failed after {attempt + 1} attempt(s): {type(exc).__name__}."
            ) from exc
        delay = min(8, 2**attempt)
        retry_after = getattr(getattr(exc, "response", None), "headers", {}).get(
            "retry-after"
        )
        if retry_after:
            try:
                delay = max(delay, float(retry_after))
            except ValueError:
                pass
        if delay > 30:
            raise ProviderFailure(
                "Provider requests a long wait; stopped without retry."
            ) from exc
        self.emit(
            "provider_retry",
            {
                "attempt": attempt + 1,
                "purpose": purpose,
                "delay_seconds": delay,
                "error_type": type(exc).__name__,
            },
        )
        await self.sleep(delay)

    async def close(self):
        if hasattr(self.client, "close"):
            await self.client.close()
