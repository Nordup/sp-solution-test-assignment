"""Per-response iterator ownership for OpenAI 3.10 / HTTPX2 2.12 streams.

These versions close sockets but omit iterator closure at the SDK's SSE
boundary and HTTPX2's httpcore boundary. Keep the compatibility code here;
the local HTTP regression tests exercise the actual pinned dependency stack.
"""

from contextlib import AsyncExitStack, aclosing, asynccontextmanager

from openai import AsyncStream


class _OwnedHTTPBody:
    """Keep the httpcore iterator alive and close it before its connection."""

    def __init__(self, body):
        self.body = body
        self.iterator = body.__aiter__()

    def __aiter__(self):
        return self.iterator

    async def aclose(self):
        try:
            await self.iterator.aclose()
        finally:
            await self.body.aclose()


@asynccontextmanager
async def own_stream_iterators(stream):
    """Close nested iterators in the request task, including on early exit.

    Only this response is adapted; SDK and transport classes are never patched
    globally. Fake/custom provider streams keep their existing context protocol.
    """

    if not isinstance(stream, AsyncStream):
        yield
        return

    # HTTPX2 wraps AsyncResponseStream in BoundAsyncStream. The transport's
    # async-for does not close its httpcore iterator, even when aiter_raw is
    # explicitly closed. Retain that iterator and attach its cleanup to the
    # response's existing aclose chain. Other transports have no httpcore body.
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
        # AsyncStream.__stream__ resolves this method on its first iteration.
        # Supply the owned decoder directly instead of the SDK's unowned
        # _iter_events wrapper; retain native event parsing and error handling.
        stream._iter_events = lambda: events
        yield
