"""Exercise real SDK/HTTP generators against a local SSE server, without API calls."""

import asyncio
import json
import sys

import httpx2
import pytest
from openai import AsyncOpenAI

from browser_agent.config import Settings
from browser_agent.llm import Gateway, ProviderFailure


@pytest.mark.parametrize(
    "outcome",
    [
        "completed",
        "completed_done",
        "incomplete",
        "failed",
        "error",
        "malformed",
        "truncated",
        "disconnect",
        "cancel_wait",
        "cancel_at_delta",
    ],
)
def test_provider_streams_close_before_event_loop_shutdown(outcome):
    seen = []
    errors = []

    async def scenario():
        loop = asyncio.get_running_loop()
        loop.set_exception_handler(lambda _loop, context: errors.append(context))
        hooks = sys.get_asyncgen_hooks()

        def track(generator):
            # Retain generators to reproduce delayed GC at CLI exit reliably.
            seen.append(generator)
            hooks.firstiter(generator)

        sys.set_asyncgen_hooks(firstiter=track)
        handlers = []

        async def serve(reader, writer):
            try:
                headers = await reader.readuntil(b"\r\n\r\n")
                length = next(
                    int(line.split(b":", 1)[1])
                    for line in headers.split(b"\r\n")
                    if line.lower().startswith(b"content-length:")
                )
                payload = json.loads(await reader.readexactly(length))
                if b"/input_tokens " in headers:
                    body = b'{"input_tokens":100}'
                    writer.write(
                        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                        b"Connection: close\r\nContent-Length: "
                        + str(len(body)).encode()
                        + b"\r\n\r\n"
                        + body
                    )
                else:
                    mode = payload["input"]
                    status = mode if mode in {"incomplete", "failed"} else "completed"
                    event = {
                        "type": "response." + status,
                        "response": {
                            "id": "resp_local",
                            "status": status,
                            "output": [],
                            "usage": {"input_tokens": 100, "output_tokens": 20},
                        },
                    }
                    if mode == "error":
                        event = {
                            "type": "error",
                            "code": "local_error",
                            "message": "test",
                        }
                    if mode in {
                        "truncated",
                        "disconnect",
                        "cancel_wait",
                        "cancel_at_delta",
                    }:
                        event = {"type": "response.output_text.delta", "delta": "test"}
                    body = b"data: " + json.dumps(event).encode() + b"\n\n"
                    if mode == "malformed":
                        body = b"data: {invalid json}\n\n"
                    if mode == "completed_done":
                        body += b"data: [DONE]\n\n"
                    writer.write(
                        b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
                        b"Transfer-Encoding: chunked\r\n\r\n"
                        + f"{len(body):x}\r\n".encode()
                        + body
                        + b"\r\n"
                    )
                    if mode == "truncated":
                        writer.write(b"0\r\n\r\n")
                await writer.drain()
                if b"/input_tokens " not in headers and mode not in {
                    "truncated",
                    "disconnect",
                }:
                    # Completion must not wait for EOF from the provider.
                    await reader.read()
            finally:
                writer.close()
                await writer.wait_closed()

        def accept(reader, writer):
            handlers.append(asyncio.create_task(serve(reader, writer)))

        server = await asyncio.start_server(accept, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        client = AsyncOpenAI(
            api_key="local-test-key",
            base_url=f"http://127.0.0.1:{port}/v1",
            max_retries=0,
            timeout=2,
        )
        first_token = asyncio.Event()

        def emit(event, _data):
            if event == "model_first_token":
                first_token.set()
                if outcome == "cancel_at_delta":
                    # Cancel while the complete generator chain is at a yield.
                    raise asyncio.CancelledError

        gateway = Gateway(Settings(), client=client, emit=emit)
        try:
            for mode in (outcome, outcome, "completed"):
                first_token.clear()
                call = asyncio.create_task(gateway.call({"input": mode}))
                if mode == "cancel_wait":
                    await asyncio.wait_for(first_token.wait(), 2)
                    # Cancel during a network read, as Ctrl+C/double-Escape do.
                    call.cancel()
                if mode in {"failed", "error", "truncated"}:
                    expected = ProviderFailure
                elif mode == "malformed":
                    expected = json.JSONDecodeError
                elif mode == "disconnect":
                    expected = httpx2.RemoteProtocolError
                elif mode.startswith("cancel_"):
                    expected = asyncio.CancelledError
                else:
                    expected = None
                if expected is not None:
                    with pytest.raises(expected):
                        await asyncio.wait_for(call, 2)
                else:
                    response = await asyncio.wait_for(call, 2)
                    assert response.status == (
                        "incomplete" if mode == "incomplete" else "completed"
                    )
                pending = [g.__qualname__ for g in seen if g.ag_frame is not None]
                assert pending == [], f"Generators left after {mode}: {pending}"
        finally:
            await gateway.close()
            server.close()
            await server.wait_closed()
            await asyncio.wait_for(asyncio.gather(*handlers), 2)
            sys.set_asyncgen_hooks(*hooks)
        return [g.__qualname__ for g in seen if g.ag_frame is not None]

    pending = asyncio.run(scenario())
    assert pending == [], f"Generators left for loop shutdown: {pending}"
    assert errors == [], [context.get("exception") for context in errors]
