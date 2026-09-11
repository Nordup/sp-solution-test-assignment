"""Native Responses client with bounded retries and shared task accounting."""

import asyncio
import math
import time
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from openai import APIConnectionError, APIStatusError, AsyncOpenAI

from ..config import Settings
from ..context import ContextOverflow
from ..telemetry import diagnostic_span
from .budget import MICRO_USD_PER_USD, Budget
from .diagnostics import (
    bounded_error_fields,
    new_stream_progress,
    record_stream_event,
    response_details,
    visible_response_capture,
)
from .errors import ProviderFailure
from .pricing import PRICES, UsageMetadata, reservation_rates, usage_metadata
from .stream import close_async_iterator, diagnostic_stream_context

Purpose = Literal["actor", "security"]
Emit = Callable[[str, dict[str, Any]], None]
Sleep = Callable[[float], Awaitable[Any]]

_TERMINAL_EVENTS = {
    "response.completed",
    "response.incomplete",
    "response.failed",
}


def _discard_event(_event: str, _data: dict[str, Any]) -> None:
    pass


class ModelClient:
    """Send actor and security requests through one budgeted provider client."""

    def __init__(
        self,
        settings: Settings,
        *,
        client: Any = None,
        emit: Emit | None = None,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        if settings.model not in PRICES:
            raise ProviderFailure(
                "No verified price for this model; configure gpt-5.6-luna."
            )
        self.settings = settings
        self.client = client or AsyncOpenAI(
            api_key=settings.api_key.get_secret_value(),
            max_retries=0,
            timeout=60,
        )
        self.emit = emit or _discard_event
        self.sleep = sleep
        self.budget = Budget(settings.budget_usd)
        self.usage: dict[str, Any] = {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "calls": 0,
            "input_token_details": {"cache_read": 0, "cache_creation": 0},
            "output_token_details": {"reasoning": 0},
        }
        self.reported_cost_usd = 0.0
        self.attempts = 0

    @property
    def cost_usd(self) -> float:
        return self.budget.cost_usd

    async def call(
        self,
        request: Mapping[str, Any],
        purpose: Purpose = "actor",
    ) -> Any:
        """Count, reserve, stream, and account for one model response."""

        request_id = str(uuid4())
        provider_request, effort = self._prepare_request(request, purpose)
        input_tokens = await self._count_input(
            provider_request,
            purpose=purpose,
            request_id=request_id,
        )
        reservation = self._reservation(input_tokens)

        for attempt in range(self.settings.max_retries + 1):
            self.budget.reserve(reservation)
            self.attempts += 1
            context = self._attempt_context(
                request_id=request_id,
                purpose=purpose,
                effort=effort,
                attempt=attempt,
            )
            started = time.monotonic()
            self.emit(
                "model_admitted",
                {
                    **context,
                    "input_tokens": input_tokens,
                    "reserved_microusd": reservation,
                },
            )
            try:
                response, first_token = await self._request_response(
                    provider_request,
                    context=context,
                    started=started,
                )
                usage = getattr(response, "usage", None)
                if usage is None:
                    raise ProviderFailure(
                        "Provider omitted usage; conservative reservation retained."
                    )
                metrics = usage_metadata(usage, self.settings.model)
            except (APIConnectionError, APIStatusError) as exc:
                self._emit_model_error(context, started, exc)
                await self._wait_to_retry(
                    exc,
                    attempt,
                    purpose=purpose,
                    request_id=request_id,
                )
                continue
            except BaseException as exc:
                self._emit_model_error(context, started, exc)
                raise

            self._finalize_response(
                response,
                usage,
                metrics,
                context=context,
                reservation=reservation,
                started=started,
                first_token=first_token,
            )
            return response
        raise ProviderFailure("Retries exhausted.")

    def _prepare_request(
        self,
        request: Mapping[str, Any],
        purpose: Purpose,
    ) -> tuple[dict[str, Any], str]:
        effort = "medium" if purpose == "security" else self.settings.reasoning
        provider_request = dict(
            request,
            model=self.settings.model,
            reasoning={"effort": effort},
        )
        if purpose == "security":
            provider_request.pop("context_management", None)
        return provider_request, effort

    async def _count_input(
        self,
        provider_request: Mapping[str, Any],
        *,
        purpose: Purpose,
        request_id: str,
    ) -> int:
        count_request = {
            key: value
            for key, value in provider_request.items()
            if key != "context_management"
        }
        count: Any = None
        for attempt in range(self.settings.max_retries + 1):
            context = {
                "request_id": request_id,
                "purpose": purpose,
                "attempt": attempt + 1,
            }
            try:
                async with diagnostic_span(
                    self.emit, "input_count", **context
                ) as progress:
                    progress["request_type"] = "responses.input_tokens.count"
                    count = await self.client.responses.input_tokens.count(
                        **count_request
                    )
                    progress["input_tokens"] = getattr(count, "input_tokens", None)
                break
            except (APIConnectionError, APIStatusError) as exc:
                await self._wait_to_retry(
                    exc,
                    attempt,
                    purpose="input_count",
                    request_id=request_id,
                )

        tokens = count.input_tokens
        if not isinstance(tokens, int) or tokens < 0:
            raise ProviderFailure("Provider returned an invalid input count.")
        if tokens > self.settings.max_input_tokens:
            raise ContextOverflow(
                f"Request needs {tokens} input tokens; "
                f"cap is {self.settings.max_input_tokens}."
            )
        return tokens

    async def _request_response(
        self,
        provider_request: Mapping[str, Any],
        *,
        context: Mapping[str, Any],
        started: float,
    ) -> tuple[Any, float | None]:
        async with diagnostic_span(self.emit, "response_create", **context) as progress:
            progress["request_type"] = "responses.create"
            stream = await self.client.responses.create(
                **provider_request,
                max_output_tokens=self.settings.max_output_tokens,
                store=False,
                stream=True,
                service_tier="default",
            )
            progress["stream_opened"] = True
        return await self._read_stream(stream, context=context, started=started)

    async def _read_stream(
        self,
        stream: Any,
        *,
        context: Mapping[str, Any],
        started: float,
    ) -> tuple[Any, float | None]:
        response = None
        first_token = None
        async with diagnostic_span(self.emit, "stream_read", **context) as progress:
            progress.update(new_stream_progress())
            sequence = 0
            last_event_at = time.monotonic()
            async with diagnostic_stream_context(stream, self.emit, context):
                iterator = stream.__aiter__()
                try:
                    while True:
                        try:
                            event = await anext(iterator)
                        except StopAsyncIteration:
                            break
                        sequence += 1
                        last_event_at = record_stream_event(
                            progress,
                            event,
                            sequence,
                            last_event_at,
                        )
                        if first_token is None and self._has_visible_delta(event):
                            first_token = time.monotonic() - started
                            self.emit(
                                "model_first_token",
                                {
                                    **context,
                                    "ttft_seconds": first_token,
                                    "first_token_time": datetime.now(UTC).isoformat(),
                                },
                            )

                        event_type = getattr(event, "type", None)
                        if event_type in _TERMINAL_EVENTS:
                            response = getattr(event, "response", None)
                            progress.update(response_details(response))
                            break
                        if event_type == "error":
                            progress["stream_error"] = bounded_error_fields(event)
                            raise ProviderFailure(
                                "Provider stream reported an error; "
                                "reservation retained."
                            )
                finally:
                    await close_async_iterator(iterator)
                    nested = getattr(stream, "_iterator", None)
                    if nested is not None and nested is not iterator:
                        await close_async_iterator(nested)
            if response is not None:
                progress["response_capture"] = visible_response_capture(response)

        if response is None:
            raise ProviderFailure(
                "Provider stream ended without a final response; reservation retained."
            )
        return response, first_token

    @staticmethod
    def _has_visible_delta(event: Any) -> bool:
        return (
            str(getattr(event, "type", "")).endswith(".delta")
            and isinstance(getattr(event, "delta", None), str)
            and bool(event.delta)
        )

    def _reservation(self, input_tokens: int) -> int:
        input_rate, output_rate = reservation_rates(self.settings.model, input_tokens)
        return math.ceil(
            input_tokens * input_rate + self.settings.max_output_tokens * output_rate
        )

    def _actual_cost(self, usage: Any) -> int:
        input_rate, output_rate = reservation_rates(
            self.settings.model, usage.input_tokens
        )
        return math.ceil(
            usage.input_tokens * input_rate + usage.output_tokens * output_rate
        )

    def _attempt_context(
        self,
        *,
        request_id: str,
        purpose: Purpose,
        effort: str,
        attempt: int,
    ) -> dict[str, Any]:
        return {
            "request_id": request_id,
            "call_id": str(uuid4()),
            "purpose": purpose,
            "model": self.settings.model,
            "effort": effort,
            "attempt": attempt + 1,
        }

    def _record_usage(self, usage: Any, metrics: UsageMetadata) -> None:
        self.usage["input_tokens"] += usage.input_tokens
        self.usage["output_tokens"] += usage.output_tokens
        self.usage["total_tokens"] += metrics["total_tokens"]
        for field in ("input_token_details", "output_token_details"):
            for key, value in metrics[field].items():
                self.usage[field][key] += value
        self.usage["calls"] += 1
        self.reported_cost_usd += metrics["total_cost"]

    def _finalize_response(
        self,
        response: Any,
        usage: Any,
        metrics: UsageMetadata,
        *,
        context: Mapping[str, Any],
        reservation: int,
        started: float,
        first_token: float | None,
    ) -> None:
        actual_cost = self._actual_cost(usage)
        self._record_usage(usage, metrics)
        self.emit(
            "model_usage",
            {
                **context,
                **metrics,
                "usage_metadata": metrics,
                "response_id": getattr(response, "id", None),
                "response_status": getattr(response, "status", "completed"),
                "latency_seconds": time.monotonic() - started,
                "ttft_seconds": first_token,
                "cost_microusd": actual_cost,
                "cost_usd": (self.budget.spent - reservation + actual_cost)
                / MICRO_USD_PER_USD,
            },
        )
        # Completed usage is observable even when it exceeds the reservation.
        self.budget.reconcile(reservation, actual_cost)
        if getattr(response, "status", None) == "failed":
            raise ProviderFailure("Provider returned a failed response.")

    def _emit_model_error(
        self,
        context: Mapping[str, Any],
        started: float,
        error: BaseException,
    ) -> None:
        # Usage is unknown on every failed attempt, so its reservation stays charged.
        self.emit(
            "model_error",
            {
                **context,
                "error_type": type(error).__name__,
                "latency_seconds": time.monotonic() - started,
                "usage_unknown": True,
            },
        )

    async def _wait_to_retry(
        self,
        error: APIConnectionError | APIStatusError,
        attempt: int,
        *,
        purpose: str,
        request_id: str | None = None,
    ) -> None:
        retryable = isinstance(error, APIConnectionError) or getattr(
            error, "status_code", 0
        ) in {408, 409, 429, 500, 502, 503, 504}
        if not retryable or attempt == self.settings.max_retries:
            raise ProviderFailure(
                f"{purpose} request failed after {attempt + 1} attempt(s): "
                f"{type(error).__name__}."
            ) from error

        delay = min(8, 2**attempt)
        retry_after = getattr(getattr(error, "response", None), "headers", {}).get(
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
            ) from error
        self.emit(
            "provider_retry",
            {
                "attempt": attempt + 1,
                "purpose": purpose,
                "delay_seconds": delay,
                "error_type": type(error).__name__,
                **({"request_id": request_id} if request_id else {}),
            },
        )
        await self.sleep(delay)

    async def close(self) -> None:
        """Close the underlying provider client when it owns resources."""

        if hasattr(self.client, "close"):
            await self.client.close()
