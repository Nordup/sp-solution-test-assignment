"""Own the complete iterator stack behind an OpenAI response stream."""

import sys
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import AsyncExitStack, aclosing, asynccontextmanager
from types import TracebackType
from typing import Any

from openai import AsyncStream

from ..telemetry import diagnostic_span

Emit = Callable[[str, dict[str, Any]], None]
type ExceptionInfo = tuple[
    type[BaseException] | None,
    BaseException | None,
    TracebackType | None,
]


class _OwnedHTTPBody:
    """Close httpcore's iterator before closing its response body."""

    def __init__(self, body: Any) -> None:
        self._body = body
        self._iterator = body.__aiter__()
        self._closed = False

    def __aiter__(self) -> Any:
        return self._iterator

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await _close_iterator(self._iterator)
        finally:
            await self._body.aclose()


@asynccontextmanager
async def response_stream_events(
    stream: Any,
    emit: Emit,
    context: Mapping[str, Any],
) -> AsyncIterator[Any]:
    """Yield one owned event iterator and close every layer on every exit path."""

    await stream.__aenter__()
    try:
        async with _owned_sdk_iterators(stream):
            iterator = stream.__aiter__()
            try:
                yield iterator
            finally:
                await _close_event_iterators(stream, iterator)
    except BaseException:
        exception = sys.exc_info()
        suppressed = await _close_response(stream, emit, context, exception)
        if not suppressed:
            raise
    else:
        await _close_response(stream, emit, context, (None, None, None))


@asynccontextmanager
async def _owned_sdk_iterators(stream: Any) -> AsyncIterator[None]:
    """Own SDK-private iterators omitted by OpenAI 3.10 / HTTPX2 2.12 cleanup.

    This per-response adapter keeps native event decoding and provider error
    handling intact. It is intentionally limited to the pinned ``AsyncStream``;
    model doubles and future SDK implementations retain their normal behavior.
    """

    if not isinstance(stream, AsyncStream):
        yield
        return

    transport = getattr(stream.response.stream, "_stream", None)
    body = getattr(transport, "_httpcore_stream", None)
    if body is not None:
        transport._httpcore_stream = _OwnedHTTPBody(body)

    async with AsyncExitStack() as cleanup:
        response_bytes = await cleanup.enter_async_context(
            aclosing(stream.response.aiter_bytes())
        )
        decoded_events = await cleanup.enter_async_context(
            aclosing(stream._decoder.aiter_bytes(response_bytes))
        )
        # AsyncStream resolves this method at first iteration. Supplying the
        # owned decoder preserves the SDK parser while giving this task custody.
        stream._iter_events = lambda: decoded_events
        yield


async def _close_event_iterators(stream: Any, iterator: Any) -> None:
    try:
        await _close_iterator(iterator)
    finally:
        nested = getattr(stream, "_iterator", None)
        if nested is not None and nested is not iterator:
            await _close_iterator(nested)


async def _close_iterator(iterator: Any) -> None:
    close = getattr(iterator, "aclose", None)
    if callable(close):
        await close()


async def _close_response(
    stream: Any,
    emit: Emit,
    context: Mapping[str, Any],
    exception: ExceptionInfo,
) -> bool:
    async with diagnostic_span(emit, "stream_close", **context) as progress:
        progress["close_attempted"] = True
        suppressed = await stream.__aexit__(*exception)
        progress["stream_closed"] = True
        progress["exception_suppressed"] = bool(suppressed)
    return bool(suppressed)
