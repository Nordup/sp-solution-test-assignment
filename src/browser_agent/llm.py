"""Native Responses calls with bounded retries and an in-memory spending cap."""

import asyncio
import json
import math
import sys
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import uuid4

from openai import APIConnectionError, APIStatusError, AsyncOpenAI

from .budget import Budget
from .context import ContextOverflow
from .telemetry import diagnostic_span

# Official Luna pricing checked 2026-09-09; reserve the cache-write premium.
# Requests above 272k input tokens use the published long-context multipliers.
# Reservation rates are conservative; usage_metadata reports standard rates.
LONG_CONTEXT_THRESHOLD = 272000
PRICES = {"gpt-5.6-luna": (0.25, 1.2)}
LONG_PRICES = {"gpt-5.6-luna": (0.5, 1.8)}
# Standard USD per million tokens, including explicit cache writes/reads.
USAGE_PRICES = {"gpt-5.6-luna": (0.20, 0.02, 0.25, 1.2)}
LONG_USAGE_PRICES = {"gpt-5.6-luna": (0.40, 0.04, 0.50, 1.8)}


class ProviderFailure(RuntimeError):
    pass


_DIAGNOSTIC_TEXT_LIMIT = 12000
_DIAGNOSTIC_ITEM_LIMIT = 16
_PARTIAL_HEAD_LIMIT = 4000
_PARTIAL_TAIL_LIMIT = 4000


def _object_dict(value):
    """Return a shallow provider object mapping without serializing secrets."""

    if isinstance(value, dict):
        return value
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        try:
            return dump(exclude_none=True)
        except (TypeError, ValueError):
            return {}
    values = getattr(value, "__dict__", None)
    return values if isinstance(values, dict) else {}


def _bounded_visible(value, limit=_DIAGNOSTIC_TEXT_LIMIT):
    """Capture visible model text with an explicit length and truncation marker."""

    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = str(value)
    return {
        "value": text[:limit],
        "length": len(text),
        "truncated": len(text) > limit,
    }


def _visible_response_capture(response):
    """Keep function arguments and user-visible text, excluding hidden content."""

    data = _object_dict(response)
    output = data.get("output")
    if not isinstance(output, (list, tuple)):
        output = getattr(response, "output", ()) or ()
    items = []
    for item in list(output)[:_DIAGNOSTIC_ITEM_LIMIT]:
        item_data = _object_dict(item)
        item_type = item_data.get("type")
        if item_type == "function_call":
            captured = {"type": "function_call"}
            for key in ("name", "call_id"):
                value = item_data.get(key)
                if isinstance(value, str):
                    captured[key] = value[:240]
            if "arguments" in item_data:
                captured["arguments"] = _bounded_visible(item_data["arguments"])
            items.append(captured)
            continue
        if item_type not in {"message", "output_text", "text"}:
            continue
        texts = []
        content = item_data.get("content")
        if isinstance(content, (list, tuple)):
            for part in content:
                part_data = _object_dict(part)
                if part_data.get("type") in {"output_text", "text"}:
                    text = part_data.get("text")
                    if isinstance(text, str):
                        texts.append(text)
        text = item_data.get("text")
        if isinstance(text, str):
            texts.append(text)
        if texts:
            items.append({"type": str(item_type)[:80], "text": _bounded_visible("".join(texts))})
    output_text = data.get("output_text")
    if not isinstance(output_text, str):
        output_text = getattr(response, "output_text", None)
    capture = {"items": items}
    if isinstance(output_text, str) and output_text:
        capture["output_text"] = _bounded_visible(output_text)
    return capture


def _response_details(response):
    data = _object_dict(response)
    response_id = data.get("id", getattr(response, "id", None))
    status = data.get("status", getattr(response, "status", None))
    details = data.get(
        "incomplete_details", getattr(response, "incomplete_details", None)
    )
    result = {
        "response_id": str(response_id)[:240] if response_id is not None else None,
        "response_status": str(status)[:120] if status is not None else None,
    }
    detail_data = _object_dict(details)
    if detail_data:
        result["incomplete_details"] = {
            key: str(detail_data[key])[:240]
            for key in ("reason", "type")
            if detail_data.get(key) is not None
        }
    elif details is not None:
        result["incomplete_details"] = str(details)[:240]
    error = data.get("error", getattr(response, "error", None))
    error_details = _bounded_error_fields(error)
    if error_details:
        result["response_error"] = error_details
    return result


def _bounded_error_fields(value):
    data = _object_dict(value)
    nested = _object_dict(data.get("error"))
    if nested:
        data = {**nested, **data}
    result = {}
    for key in ("code", "message", "param"):
        field = data.get(key)
        if isinstance(field, str):
            result[key] = field[:4000]
            result[f"{key}_truncated"] = len(field) > 4000
    return result


def _append_partial_visible(progress, key, value):
    if not isinstance(value, str) or not value:
        return
    partial = progress.setdefault("partial_visible", {})
    captured = partial.setdefault(
        key,
        {"length": 0, "head": "", "tail": "", "truncated": False},
    )
    captured["length"] += len(value)
    if len(captured["head"]) < _PARTIAL_HEAD_LIMIT:
        room = _PARTIAL_HEAD_LIMIT - len(captured["head"])
        captured["head"] += value[:room]
    captured["tail"] = (captured["tail"] + value)[-_PARTIAL_TAIL_LIMIT:]
    captured["truncated"] = captured["length"] > _PARTIAL_HEAD_LIMIT


def _record_stream_event(progress, event, sequence, last_event_at):
    now = time.monotonic()
    event_type = str(getattr(event, "type", "unknown"))[:120]
    counts = progress.setdefault("event_type_counts", {})
    if event_type in counts or len(counts) < 64:
        counts[event_type] = counts.get(event_type, 0) + 1
    delta_chars = progress.setdefault("delta_chars_by_type", {})
    delta = getattr(event, "delta", None)
    if isinstance(delta, str) and (event_type in delta_chars or len(delta_chars) < 64):
        delta_chars[event_type] = delta_chars.get(event_type, 0) + len(delta)
    if "function_call_arguments.delta" in event_type:
        _append_partial_visible(progress, "function_call_arguments", delta)
    elif "output_text.delta" in event_type:
        _append_partial_visible(progress, "output_text", delta)
    progress["event_count"] = sequence
    provider_sequence = getattr(event, "sequence_number", None)
    if not isinstance(provider_sequence, int) or isinstance(provider_sequence, bool):
        provider_sequence = sequence
    if progress.get("first_sequence") is None:
        progress["first_sequence"] = provider_sequence
    progress["last_sequence"] = provider_sequence
    progress["last_event_type"] = event_type
    progress["last_event_at_monotonic"] = now
    progress["inter_event_gap_seconds"] = now - last_event_at
    return now


@asynccontextmanager
async def _diagnostic_stream_context(stream, emit, context):
    """Close a provider stream while recording the real ``__aexit__`` phase."""

    await stream.__aenter__()
    exception = None
    try:
        yield
    except BaseException:  # noqa: BLE001 - preserve exact exception for stream __aexit__
        exception = sys.exc_info()
    async with diagnostic_span(emit, "stream_close", **context) as progress:
        progress["close_attempted"] = True
        suppressed = await stream.__aexit__(*(exception or (None, None, None)))
        progress["stream_closed"] = True
        progress["exception_suppressed"] = bool(suppressed)
    if exception is not None and not suppressed:
        raise exception[1].with_traceback(exception[2])


async def _close_async_iterator(iterator):
    """Close the iterator returned by a provider stream before its response.

    The OpenAI async stream wraps an SSE async generator.  Breaking an
    ``async for`` at ``response.completed`` leaves that wrapper alive until
    loop shutdown; explicitly closing it here prevents httpcore's async
    generator finalizer from racing the response close.
    """

    close = getattr(iterator, "aclose", None)
    if close is not None:
        await close()


def reservation_rates(model, tokens):
    return (LONG_PRICES if tokens > LONG_CONTEXT_THRESHOLD else PRICES)[model]


def usage_rates(model, tokens):
    return (LONG_USAGE_PRICES if tokens > LONG_CONTEXT_THRESHOLD else USAGE_PRICES)[
        model
    ]


def extract_compaction(response):
    """Return the opaque native compaction item from a completed actor response."""
    latest = None
    output = (
        response.get("output", ())
        if isinstance(response, dict)
        else getattr(response, "output", ())
    )
    for item in output or ():
        if isinstance(item, dict):
            data = item
        else:
            dump = getattr(item, "model_dump", None)
            if dump is not None:
                data = dump(exclude_none=True)
            elif hasattr(item, "__dict__"):
                data = vars(item)
            else:
                continue
        if data.get("type") != "compaction":
            continue
        encrypted = data.get("encrypted_content")
        if not isinstance(encrypted, str) or not encrypted:
            continue
        # Responses output items may include ``created_by`` metadata.  The
        # input schema accepts only this opaque payload (and its optional id),
        # so preserve the ciphertext byte-for-byte while stripping output-only
        # fields before carrying the item into the next request.
        latest = {"type": "compaction", "encrypted_content": encrypted}
        if isinstance(data.get("id"), str) and data["id"]:
            latest["id"] = data["id"]
    return latest


def usage_metadata(usage, model):
    """Normalize native Responses usage to LangSmith's token/cost schema."""

    def count(value):
        if type(value) is not int or value < 0:
            raise ProviderFailure(
                "Provider returned invalid usage; reservation retained."
            )
        return value

    inputs, outputs = count(usage.input_tokens), count(usage.output_tokens)
    details = getattr(usage, "input_tokens_details", None)
    cached = count(getattr(details, "cached_tokens", 0))
    written = count(getattr(details, "cache_write_tokens", 0))
    reasoning = count(
        getattr(getattr(usage, "output_tokens_details", None), "reasoning_tokens", 0)
    )
    if cached + written > inputs or reasoning > outputs:
        raise ProviderFailure(
            "Provider returned inconsistent usage; reservation retained."
        )
    regular_rate, cache_rate, write_rate, output_rate = usage_rates(model, inputs)
    input_cost = (
        (inputs - cached - written) * regular_rate
        + cached * cache_rate
        + written * write_rate
    ) / 1_000_000
    output_cost = outputs * output_rate / 1_000_000
    return {
        "input_tokens": inputs,
        "output_tokens": outputs,
        "total_tokens": inputs + outputs,
        "input_token_details": {"cache_read": cached, "cache_creation": written},
        "output_token_details": {"reasoning": reasoning},
        "input_cost": input_cost,
        "output_cost": output_cost,
        "total_cost": input_cost + output_cost,
    }


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
        self.usage = {
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
    def cost_usd(self):
        return self.budget.cost_usd

    async def call(self, request, purpose="actor"):
        request_id = str(uuid4())
        effort = "medium" if purpose == "security" else self.settings.reasoning
        req = dict(
            request,
            model=self.settings.model,
            reasoning={"effort": effort},
        )
        if purpose == "security":
            req.pop("context_management", None)
        count_request = {
            key: value for key, value in req.items() if key != "context_management"
        }
        for attempt in range(self.settings.max_retries + 1):
            count_context = {
                "request_id": request_id,
                "purpose": purpose,
                "attempt": attempt + 1,
            }
            try:
                async with diagnostic_span(
                    self.emit, "input_count", **count_context
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
                f"Request needs {tokens} input tokens; cap is {self.settings.max_input_tokens}."
            )
        rates = reservation_rates(self.settings.model, tokens)
        reserved = math.ceil(
            tokens * rates[0] + self.settings.max_output_tokens * rates[1]
        )
        for attempt in range(self.settings.max_retries + 1):
            self.budget.reserve(reserved)
            self.attempts += 1
            call_id = str(uuid4())
            started = time.monotonic()
            first_token = None
            context = {
                "request_id": request_id,
                "call_id": call_id,
                "purpose": purpose,
                "model": self.settings.model,
                "effort": effort,
                "attempt": attempt + 1,
            }
            self.emit(
                "model_admitted",
                {
                    **context,
                    "input_tokens": tokens,
                    "reserved_microusd": reserved,
                },
            )
            try:
                async with diagnostic_span(
                    self.emit, "response_create", **context
                ) as progress:
                    progress["request_type"] = "responses.create"
                    stream = await self.client.responses.create(
                        **req,
                        max_output_tokens=self.settings.max_output_tokens,
                        store=False,
                        stream=True,
                        service_tier="default",
                    )
                    progress["stream_opened"] = True
                response = None
                stream_progress = {
                    "event_count": 0,
                    "event_type_counts": {},
                    "delta_chars_by_type": {},
                    "partial_visible": {},
                    "first_sequence": None,
                    "last_sequence": None,
                    "last_event_type": None,
                    "last_event_at_monotonic": None,
                    "inter_event_gap_seconds": None,
                }
                async with diagnostic_span(
                    self.emit, "stream_read", **context
                ) as progress:
                    progress.update(stream_progress)
                    sequence = 0
                    last_event_at = time.monotonic()
                    async with _diagnostic_stream_context(stream, self.emit, context):
                        iterator = stream.__aiter__()
                        try:
                            while True:
                                try:
                                    event = await anext(iterator)
                                except StopAsyncIteration:
                                    break
                                sequence += 1
                                last_event_at = _record_stream_event(
                                    progress, event, sequence, last_event_at
                                )
                                if (
                                    first_token is None
                                    and str(getattr(event, "type", "")).endswith(
                                        ".delta"
                                    )
                                    and isinstance(getattr(event, "delta", None), str)
                                    and event.delta
                                ):
                                    first_token = time.monotonic() - started
                                    self.emit(
                                        "model_first_token",
                                        {
                                            **context,
                                            "ttft_seconds": first_token,
                                            "first_token_time": datetime.now(UTC).isoformat(),
                                        },
                                    )
                                if getattr(event, "type", None) in {
                                    "response.completed",
                                    "response.incomplete",
                                    "response.failed",
                                }:
                                    response = getattr(event, "response", None)
                                    progress.update(_response_details(response))
                                    break
                                if getattr(event, "type", None) == "error":
                                    progress["stream_error"] = _bounded_error_fields(event)
                                    raise ProviderFailure(
                                        "Provider stream reported an error; reservation retained."
                                    )
                        finally:
                            await _close_async_iterator(iterator)
                            nested = getattr(stream, "_iterator", None)
                            if nested is not None and nested is not iterator:
                                await _close_async_iterator(nested)
                    if response is not None:
                        progress["response_capture"] = _visible_response_capture(
                            response
                        )
                if response is None:
                    raise ProviderFailure(
                        "Provider stream ended without a final response; reservation retained."
                    )
                usage = getattr(response, "usage", None)
                if usage is None:
                    raise ProviderFailure(
                        "Provider omitted usage; conservative reservation retained."
                    )
                metrics = usage_metadata(usage, self.settings.model)
            except (APIConnectionError, APIStatusError) as exc:
                # Unknown usage remains fully charged; never refund a lost response.
                self.emit(
                    "model_error",
                    {
                        **context,
                        "error_type": type(exc).__name__,
                        "latency_seconds": time.monotonic() - started,
                        "usage_unknown": True,
                    },
                )
                await self._wait_to_retry(
                    exc,
                    attempt,
                    purpose=purpose,
                    request_id=request_id,
                )
                continue
            except BaseException as exc:
                self.emit(
                    "model_error",
                    {
                        **context,
                        "error_type": type(exc).__name__,
                        "latency_seconds": time.monotonic() - started,
                        "usage_unknown": True,
                    },
                )
                raise
            actual_rates = reservation_rates(self.settings.model, usage.input_tokens)
            actual = math.ceil(
                usage.input_tokens * actual_rates[0]
                + usage.output_tokens * actual_rates[1]
            )
            self.usage["input_tokens"] += usage.input_tokens
            self.usage["output_tokens"] += usage.output_tokens
            self.usage["total_tokens"] += metrics["total_tokens"]
            for field in ("input_token_details", "output_token_details"):
                for key, value in metrics[field].items():
                    self.usage[field][key] += value
            self.usage["calls"] += 1
            self.reported_cost_usd += metrics["total_cost"]
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
                    "cost_microusd": actual,
                    "cost_usd": (self.budget.spent - reserved + actual) / 1_000_000,
                },
            )
            # Log completed usage even if provider usage exceeds the reserved bound.
            self.budget.reconcile(reserved, actual)
            if getattr(response, "status", None) == "failed":
                raise ProviderFailure("Provider returned a failed response.")
            return response
        raise ProviderFailure("Retries exhausted.")

    async def _wait_to_retry(self, exc, attempt, *, purpose, request_id=None):
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
                **({"request_id": request_id} if request_id else {}),
            },
        )
        await self.sleep(delay)

    async def close(self):
        if hasattr(self.client, "close"):
            await self.client.close()
