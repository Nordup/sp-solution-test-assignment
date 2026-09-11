"""Own and close every iterator in an OpenAI response stream."""

import sys
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import AsyncExitStack, aclosing, asynccontextmanager
from types import TracebackType
from typing import Any

from openai import AsyncStream

from ..telemetry import diagnostic_span

Emit = Callable[[str, dict[str, Any]], None]


class _OwnedHTTPBody:
    """Keep the httpcore iterator alive and close it before its connection."""

    def __init__(self, body: Any) -> None:
        self.body = body
        self.iterator = body.__aiter__()

    def __aiter__(self) -> Any:
        return self.iterator

    async def aclose(self) -> None:
        try:
            await self.iterator.aclose()
        finally:
            await self.body.aclose()


@asynccontextmanager
async def own_stream_iterators(stream: Any) -> AsyncIterator[None]:
    """Close nested SDK iterators in the request task, including on early exit.

    OpenAI 3.10 with HTTPX2 2.12 closes sockets but leaves the SSE and httpcore
    iterators open. This per-response adapter avoids global SDK or transport
    patches. Local HTTP regression tests cover the pinned dependency stack.
    """

    if not isinstance(stream, AsyncStream):
        yield
        return

    transport = getattr(stream.response.stream, "_stream", None)
    body = getattr(transport, "_httpcore_stream", None)
    if body is not None:
        transport._httpcore_stream = _OwnedHTTPBody(body)

    async with AsyncExitStack() as cleanup:
        byte_iterator = await cleanup.enter_async_context(
            aclosing(stream.response.aiter_bytes())
        )
        events = await cleanup.enter_async_context(
            aclosing(stream._decoder.aiter_bytes(byte_iterator))
        )
        # AsyncStream resolves this method at first iteration. Supplying the
        # owned decoder retains native event parsing and provider error handling.
        stream._iter_events = lambda: events
        yield


async def close_async_iterator(iterator: Any) -> None:
    """Close a provider iterator before its response and network body."""

    close = getattr(iterator, "aclose", None)
    if close is not None:
        await close()


@asynccontextmanager
async def diagnostic_stream_context(
    stream: Any,
    emit: Emit,
    context: Mapping[str, Any],
) -> AsyncIterator[None]:
    """Close a provider stream while timing its real context exit."""

    await stream.__aenter__()
    exception: tuple[type[BaseException], BaseException, TracebackType] | None = None
    try:
        async with own_stream_iterators(stream):
            yield
    except BaseException:  # noqa: BLE001 - pass the exact error to stream __aexit__
        error_type, error, traceback = sys.exc_info()
        if error_type is not None and error is not None and traceback is not None:
            exception = (error_type, error, traceback)

    async with diagnostic_span(emit, "stream_close", **context) as progress:
        progress["close_attempted"] = True
        suppressed = await stream.__aexit__(*(exception or (None, None, None)))
        progress["stream_closed"] = True
        progress["exception_suppressed"] = bool(suppressed)
    if exception is not None and not suppressed:
        raise exception[1].with_traceback(exception[2])
