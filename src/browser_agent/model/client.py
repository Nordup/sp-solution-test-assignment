"""Native Responses client with explicit retries and per-task accounting."""

import asyncio
import math
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from openai import APIConnectionError, APIStatusError, AsyncOpenAI

from ..config import Settings
from ..context import ContextOverflow
from ..telemetry import diagnostic_span
from .budget import Budget, Reservation
from .diagnostics import (
    StreamCapture,
    bounded_error_fields,
    response_details,
    visible_response_capture,
)
from .errors import ProviderFailure
from .pricing import (
    PRICES,
    AggregateUsage,
    PricedUsage,
    UsageTotals,
    conservative_cost_microusd,
    price_usage,
)
from .stream import response_stream_events

Purpose = Literal["actor", "security"]
Emit = Callable[[str, dict[str, Any]], None]
Sleep = Callable[[float], Awaitable[Any]]

_TERMINAL_EVENTS = {
    "response.completed",
    "response.incomplete",
    "response.failed",
}
_RETRYABLE_STATUS_CODES = {408, 409, 429, 500, 502, 503, 504}


def _discard_event(_event: str, _data: dict[str, Any]) -> None:
    pass


@dataclass(frozen=True, slots=True)
class _RequestPlan:
    """Provider payload and stable identity for one logical model call."""

    request_id: str
    purpose: Purpose
    effort: str
    payload: dict[str, Any]

    @classmethod
    def build(
        cls,
        request: Mapping[str, Any],
        *,
        purpose: Purpose,
        model: str,
        actor_effort: str,
    ) -> "_RequestPlan":
        effort = "medium" if purpose == "security" else actor_effort
        payload = {**request, "model": model, "reasoning": {"effort": effort}}
        if purpose == "security":
            payload.pop("context_management", None)
        return cls(str(uuid4()), purpose, effort, payload)

    def count_payload(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in self.payload.items()
            if key != "context_management"
        }


@dataclass(frozen=True, slots=True)
class _GenerationAttempt:
    """Identity, timing, and budget state for one dispatched generation."""

    request_id: str
    call_id: str
    purpose: Purpose
    model: str
    effort: str
    number: int
    reservation: Reservation
    started_at: float

    @classmethod
    def start(
        cls,
        plan: _RequestPlan,
        *,
        model: str,
        number: int,
        reservation: Reservation,
    ) -> "_GenerationAttempt":
        return cls(
            request_id=plan.request_id,
            call_id=str(uuid4()),
            purpose=plan.purpose,
            model=model,
            effort=plan.effort,
            number=number,
            reservation=reservation,
            started_at=time.monotonic(),
        )

    def event_fields(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "call_id": self.call_id,
            "purpose": self.purpose,
            "model": self.model,
            "effort": self.effort,
            "attempt": self.number,
        }

    @property
    def elapsed_seconds(self) -> float:
        return time.monotonic() - self.started_at


@dataclass(frozen=True, slots=True)
class _StreamResult:
    response: Any
    first_visible_seconds: float | None


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
        self._usage = UsageTotals()
        self.attempts = 0

    @property
    def cost_usd(self) -> float:
        return self.budget.cost_usd

    @property
    def reported_cost_usd(self) -> float:
        return self._usage.reported_cost_usd

    @property
    def usage(self) -> AggregateUsage:
        return self._usage.metadata()

    async def call(
        self,
        request: Mapping[str, Any],
        purpose: Purpose = "actor",
    ) -> Any:
        """Count once, then dispatch and account for a bounded set of attempts."""

        plan = _RequestPlan.build(
            request,
            purpose=purpose,
            model=self.settings.model,
            actor_effort=self.settings.reasoning,
        )
        input_tokens = await self._count_input(plan)
        estimated_microusd = conservative_cost_microusd(
            self.settings.model,
            input_tokens,
            self.settings.max_output_tokens,
        )

        for attempt_number in range(1, self.settings.max_retries + 2):
            reservation = self.budget.reserve(estimated_microusd)
            self.attempts += 1
            attempt = _GenerationAttempt.start(
                plan,
                model=self.settings.model,
                number=attempt_number,
                reservation=reservation,
            )
            context = attempt.event_fields()
            self.emit(
                "model_admitted",
                {
                    **context,
                    "input_tokens": input_tokens,
                    "reserved_microusd": reservation.microusd,
                },
            )
            try:
                result = await self._generate(plan, attempt)
                native_usage = getattr(result.response, "usage", None)
                if native_usage is None:
                    raise ProviderFailure(
                        "Provider omitted usage; conservative reservation retained."
                    )
                priced_usage = price_usage(native_usage, self.settings.model)
            except (APIConnectionError, APIStatusError) as error:
                self._emit_attempt_error(attempt, error)
                await self._retry_or_raise(
                    error,
                    attempt_number,
                    purpose=purpose,
                    request_id=plan.request_id,
                )
                continue
            except BaseException as error:
                self._emit_attempt_error(attempt, error)
                raise

            self._complete_attempt(attempt, result, priced_usage)
            return result.response

        raise ProviderFailure("Retries exhausted.")

    async def _count_input(self, plan: _RequestPlan) -> int:
        for attempt_number in range(1, self.settings.max_retries + 2):
            context = {
                "request_id": plan.request_id,
                "purpose": plan.purpose,
                "attempt": attempt_number,
            }
            try:
                async with diagnostic_span(
                    self.emit, "input_count", **context
                ) as progress:
                    progress["request_type"] = "responses.input_tokens.count"
                    count = await self.client.responses.input_tokens.count(
                        **plan.count_payload()
                    )
                    tokens = getattr(count, "input_tokens", None)
                    progress["input_tokens"] = tokens
            except (APIConnectionError, APIStatusError) as error:
                await self._retry_or_raise(
                    error,
                    attempt_number,
                    purpose="input_count",
                    request_id=plan.request_id,
                )
                continue

            if type(tokens) is not int or tokens < 0:
                raise ProviderFailure("Provider returned an invalid input count.")
            if tokens > self.settings.max_input_tokens:
                raise ContextOverflow(
                    f"Request needs {tokens} input tokens; "
                    f"cap is {self.settings.max_input_tokens}."
                )
            return tokens

        raise ProviderFailure("Input count retries exhausted.")

    async def _generate(
        self, plan: _RequestPlan, attempt: _GenerationAttempt
    ) -> _StreamResult:
        context = attempt.event_fields()
        async with diagnostic_span(self.emit, "response_create", **context) as progress:
            progress["request_type"] = "responses.create"
            stream = await self.client.responses.create(
                **plan.payload,
                max_output_tokens=self.settings.max_output_tokens,
                store=False,
                stream=True,
                service_tier="default",
            )
            progress["stream_opened"] = True
        return await self._consume_stream(stream, attempt)

    async def _consume_stream(
        self, stream: Any, attempt: _GenerationAttempt
    ) -> _StreamResult:
        response = None
        first_visible = None
        context = attempt.event_fields()
        capture = StreamCapture()
        async with diagnostic_span(self.emit, "stream_read", **context) as progress:
            progress.update(capture.snapshot())
            async with response_stream_events(stream, self.emit, context) as iterator:
                while True:
                    try:
                        event = await anext(iterator)
                    except StopAsyncIteration:
                        break

                    observed = capture.record(event)
                    progress.update(capture.snapshot())
                    if first_visible is None and observed.has_visible_delta:
                        first_visible = attempt.elapsed_seconds
                        self.emit(
                            "model_first_token",
                            {
                                **context,
                                "ttft_seconds": first_visible,
                                "first_token_time": datetime.now(UTC).isoformat(),
                            },
                        )

                    if observed.type in _TERMINAL_EVENTS:
                        response = getattr(event, "response", None)
                        progress.update(response_details(response))
                        break
                    if observed.type == "error":
                        progress["stream_error"] = bounded_error_fields(event)
                        raise ProviderFailure(
                            "Provider stream reported an error; reservation retained."
                        )
            if response is not None:
                progress["response_capture"] = visible_response_capture(response)

        if response is None:
            raise ProviderFailure(
                "Provider stream ended without a final response; reservation retained."
            )
        return _StreamResult(response, first_visible)

    def _complete_attempt(
        self,
        attempt: _GenerationAttempt,
        result: _StreamResult,
        priced_usage: PricedUsage,
    ) -> None:
        metadata = priced_usage.metadata()
        self._usage.add(priced_usage)
        response_status = getattr(result.response, "status", "completed")
        self.emit(
            "model_usage",
            {
                **attempt.event_fields(),
                **metadata,
                "usage_metadata": metadata,
                "response_id": getattr(result.response, "id", None),
                "response_status": response_status,
                "latency_seconds": attempt.elapsed_seconds,
                "ttft_seconds": result.first_visible_seconds,
                "cost_microusd": priced_usage.conservative_microusd,
                "cost_usd": self.budget.projected_cost_usd(
                    attempt.reservation, priced_usage.conservative_microusd
                ),
            },
        )
        # Usage is observable even when settlement crosses the task cap.
        self.budget.reconcile(attempt.reservation, priced_usage.conservative_microusd)
        if response_status == "failed":
            raise ProviderFailure("Provider returned a failed response.")

    def _emit_attempt_error(
        self, attempt: _GenerationAttempt, error: BaseException
    ) -> None:
        # Failed attempts have unknown usage, so their reservation stays charged.
        self.emit(
            "model_error",
            {
                **attempt.event_fields(),
                "error_type": type(error).__name__,
                "latency_seconds": attempt.elapsed_seconds,
                "usage_unknown": True,
            },
        )

    async def _retry_or_raise(
        self,
        error: APIConnectionError | APIStatusError,
        attempt_number: int,
        *,
        purpose: str,
        request_id: str,
    ) -> None:
        status_code = getattr(error, "status_code", 0)
        retryable = (
            isinstance(error, APIConnectionError)
            or status_code in _RETRYABLE_STATUS_CODES
        )
        if not retryable or attempt_number > self.settings.max_retries:
            raise ProviderFailure(
                f"{purpose} request failed after {attempt_number} attempt(s): "
                f"{type(error).__name__}."
            ) from error

        delay = min(8, 2 ** (attempt_number - 1))
        headers = getattr(getattr(error, "response", None), "headers", None)
        retry_after = headers.get("retry-after") if headers is not None else None
        if retry_after:
            try:
                requested_delay = float(retry_after)
            except (TypeError, ValueError):
                pass
            else:
                if math.isfinite(requested_delay):
                    delay = max(delay, requested_delay)
        if delay > 30:
            raise ProviderFailure(
                "Provider requests a long wait; stopped without retry."
            ) from error
        self.emit(
            "provider_retry",
            {
                "attempt": attempt_number,
                "purpose": purpose,
                "delay_seconds": delay,
                "error_type": type(error).__name__,
                "request_id": request_id,
            },
        )
        await self.sleep(delay)

    async def close(self) -> None:
        """Close the underlying provider client when it owns resources."""

        close = getattr(self.client, "close", None)
        if callable(close):
            await close()
