"""Shared model doubles for provider unit tests."""

from types import SimpleNamespace
from unittest.mock import AsyncMock


def fake_client(count=100, replies=()):
    response = SimpleNamespace(
        usage=SimpleNamespace(input_tokens=100, output_tokens=20)
    )

    pending = list(replies)

    async def create_response(**kwargs):
        reply = pending.pop(0) if pending else response
        if isinstance(reply, BaseException):
            raise reply
        return FakeStream(reply) if kwargs.get("stream") else reply

    create = AsyncMock(side_effect=create_response)
    return SimpleNamespace(
        responses=SimpleNamespace(
            input_tokens=SimpleNamespace(
                count=AsyncMock(return_value=SimpleNamespace(input_tokens=count))
            ),
            create=create,
        ),
        close=AsyncMock(),
    )


class FakeStream:
    def __init__(self, response, events=None):
        self.events = (
            events
            if events is not None
            else [
                SimpleNamespace(type="response.created"),
                SimpleNamespace(
                    type="response.function_call_arguments.delta", delta=""
                ),
                SimpleNamespace(
                    type="response.function_call_arguments.delta",
                    delta="private arguments",
                ),
                SimpleNamespace(type="response.completed", response=response),
            ]
        )
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.closed = True

    async def __aiter__(self):
        for event in self.events:
            if isinstance(event, BaseException):
                raise event
            yield event
