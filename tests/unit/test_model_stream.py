"""Model streaming diagnostics, metrics, and cleanup."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from browser_agent.config import Settings
from browser_agent.model import ModelClient, ProviderFailure
from tests.support.model import FakeStream, fake_client


async def test_stream_diagnostics_capture_progress_and_visible_response_details():
    response = SimpleNamespace(
        id="response-visible-1",
        status="incomplete",
        incomplete_details=SimpleNamespace(reason="max_output_tokens"),
        usage=SimpleNamespace(input_tokens=100, output_tokens=20),
        output=[
            SimpleNamespace(
                type="function_call",
                name="click",
                call_id="call-visible-1",
                arguments='{"selector":"#send"}',
            ),
            SimpleNamespace(
                type="reasoning", encrypted_content="do-not-capture-reasoning"
            ),
            SimpleNamespace(
                type="message",
                content=[SimpleNamespace(type="output_text", text="Visible answer")],
            ),
        ],
    )
    stream_events = [
        SimpleNamespace(type="response.created", sequence_number=10),
        SimpleNamespace(
            type="response.function_call_arguments.delta",
            sequence_number=11,
            delta='{"selector":"#send"}',
        ),
        SimpleNamespace(
            type="response.output_text.delta",
            sequence_number=12,
            delta="Visible answer",
        ),
        SimpleNamespace(
            type="response.incomplete", sequence_number=13, response=response
        ),
    ]
    events = []
    client = fake_client()
    model = ModelClient(
        Settings(),
        client=client,
        emit=lambda event, data: events.append((event, data)),
    )
    client.responses.create.side_effect = None
    client.responses.create.return_value = FakeStream(response, stream_events)
    returned = await model.call({"input": "diagnostic test"})
    assert returned is response

    stream_read = next(
        data
        for event, data in events
        if event == "diagnostic"
        and data["phase"] == "stream_read"
        and data["state"] == "finished"
    )
    assert stream_read["event_count"] == 4
    assert stream_read["first_sequence"] == 10
    assert stream_read["last_sequence"] == 13
    assert stream_read["last_event_type"] == "response.incomplete"
    assert stream_read["inter_event_gap_seconds"] >= 0
    assert stream_read["delta_chars_by_type"]["response.output_text.delta"] == len(
        "Visible answer"
    )
    assert stream_read["response_id"] == "response-visible-1"
    assert stream_read["response_status"] == "incomplete"
    assert stream_read["incomplete_details"] == {"reason": "max_output_tokens"}
    assert stream_read["partial_visible"]["output_text"]["head"] == "Visible answer"
    capture = stream_read["response_capture"]
    assert capture["items"][0]["arguments"]["value"] == '{"selector":"#send"}'
    assert "Visible answer" in json.dumps(capture)
    assert "do-not-capture-reasoning" not in json.dumps(stream_read)

    close = next(
        data
        for event, data in events
        if event == "diagnostic"
        and data["phase"] == "stream_close"
        and data["state"] == "finished"
    )
    assert close["stream_closed"] is True
    request_ids = {
        data["request_id"]
        for event, data in events
        if event == "diagnostic"
        and data["phase"]
        in {"input_count", "response_create", "stream_read", "stream_close"}
    }
    assert len(request_ids) == 1


async def test_stream_diagnostics_capture_sse_and_response_error_fields():
    async def run_case(response, stream_events):
        stream = FakeStream(response, stream_events)
        client = fake_client()
        client.responses.create.side_effect = None
        client.responses.create.return_value = stream
        events = []
        model = ModelClient(
            Settings(),
            client=client,
            emit=lambda event, data: events.append((event, data)),
        )
        with pytest.raises(ProviderFailure):
            await model.call({"input": "error diagnostics"})
        stream_read = next(
            data
            for event, data in events
            if event == "diagnostic"
            and data["phase"] == "stream_read"
            and data["state"] == "finished"
        )
        return stream_read

    sse_read = await run_case(
        SimpleNamespace(
            status="completed",
            usage=SimpleNamespace(input_tokens=100, output_tokens=20),
        ),
        [
            SimpleNamespace(
                type="error",
                code="rate_limit",
                message="provider stream unavailable",
                param="stream",
            )
        ],
    )
    assert sse_read["stream_error"] == {
        "code": "rate_limit",
        "code_truncated": False,
        "message": "provider stream unavailable",
        "message_truncated": False,
        "param": "stream",
        "param_truncated": False,
    }

    failed_response = SimpleNamespace(
        id="response-failed-1",
        status="failed",
        error=SimpleNamespace(
            code="server_error", message="provider failed", param="response"
        ),
        usage=SimpleNamespace(input_tokens=100, output_tokens=20),
    )
    response_read = await run_case(
        failed_response,
        [SimpleNamespace(type="response.failed", response=failed_response)],
    )
    assert response_read["response_error"] == {
        "code": "server_error",
        "code_truncated": False,
        "message": "provider failed",
        "message_truncated": False,
        "param": "response",
        "param_truncated": False,
    }


async def test_streaming_metrics_include_real_first_token_and_usage():
    events = []
    model = ModelClient(
        Settings(), client=fake_client(), emit=lambda e, d: events.append((e, d))
    )
    await model.call({"input": "private prompt"})
    assert any(event == "model_first_token" for event, _ in events)
    metrics = next(data for event, data in events if event == "model_usage")
    assert metrics["total_tokens"] == 120
    assert 0 <= metrics["ttft_seconds"] <= metrics["latency_seconds"]
    assert metrics["usage_metadata"]["total_cost"] > 0


@pytest.mark.parametrize(
    "outcome",
    ["missing_usage", "truncated", "cancelled", "failed", "incomplete", "no_delta"],
)
async def test_stream_terminal_metrics_and_cleanup(outcome):
    import asyncio

    response = SimpleNamespace(
        status="completed", usage=SimpleNamespace(input_tokens=100, output_tokens=20)
    )
    if outcome == "missing_usage":
        response.usage = None
    if outcome in {"failed", "incomplete"}:
        response.status = outcome
    stream_events = [SimpleNamespace(type="response.created")]
    if outcome == "cancelled":
        stream_events.append(asyncio.CancelledError())
    elif outcome == "truncated":
        stream_events.append(
            SimpleNamespace(type="response.output_text.delta", delta="partial text")
        )
    elif outcome != "truncated":
        stream_events.append(
            SimpleNamespace(type="response." + response.status, response=response)
        )
    stream = FakeStream(response, stream_events)
    client = fake_client()
    client.responses.create.side_effect = None
    client.responses.create.return_value = stream
    events = []
    model = ModelClient(
        Settings(), client=client, emit=lambda e, d: events.append((e, d))
    )
    if outcome in {"incomplete", "no_delta"}:
        assert await model.call({"input": "test"}) is response
    else:
        with pytest.raises(
            asyncio.CancelledError if outcome == "cancelled" else ProviderFailure
        ):
            await model.call({"input": "test"})
    assert stream.closed
    has_first_token = any(event == "model_first_token" for event, _ in events)
    assert has_first_token is (outcome == "truncated")
    stream_read = next(
        data
        for event, data in events
        if event == "diagnostic"
        and data["phase"] == "stream_read"
        and data["state"] == "finished"
    )
    stream_close = next(
        data
        for event, data in events
        if event == "diagnostic"
        and data["phase"] == "stream_close"
        and data["state"] == "finished"
    )
    assert stream_close["stream_closed"] is True
    if outcome == "truncated":
        assert stream_read["partial_visible"]["output_text"]["head"] == "partial text"
    if outcome == "cancelled":
        assert stream_read["outcome"] == "cancelled"
    terminal = [(e, d) for e, d in events if e in {"model_usage", "model_error"}]
    assert len(terminal) == 1
    assert terminal[0][0] == (
        "model_usage"
        if outcome in {"incomplete", "no_delta", "failed"}
        else "model_error"
    )
    assert model.cost_usd > 0


async def test_stream_close_diagnostic_surrounds_real_delayed_close():
    class DelayedCloseStream(FakeStream):
        async def __aexit__(self, *args):
            await asyncio.sleep(0.02)
            return await super().__aexit__(*args)

    response = SimpleNamespace(
        status="completed", usage=SimpleNamespace(input_tokens=100, output_tokens=20)
    )
    stream = DelayedCloseStream(
        response,
        [SimpleNamespace(type="response.completed", response=response)],
    )
    client = fake_client()
    client.responses.create.side_effect = None
    client.responses.create.return_value = stream
    events = []
    model = ModelClient(
        Settings(), client=client, emit=lambda event, data: events.append((event, data))
    )
    await model.call({"input": "delayed close"})
    close = next(
        data
        for event, data in events
        if event == "diagnostic"
        and data["phase"] == "stream_close"
        and data["state"] == "finished"
    )
    assert stream.closed is True
    assert close["stream_closed"] is True
    assert close["elapsed_seconds"] >= 0.015


async def test_terminal_event_closes_provider_iterator_before_response_close():
    class NestedIteratorStream(FakeStream):
        def __init__(self, response):
            super().__init__(response, [])
            self.response = response
            self.iterator_closed = False

        def __aiter__(self):
            async def outer():
                try:
                    yield SimpleNamespace(
                        type="response.completed", response=self.response
                    )
                    # A real provider iterator would continue waiting for EOF.
                    await asyncio.sleep(60)
                finally:
                    self.iterator_closed = True

            self._outer = outer()
            return self._outer

        async def __aexit__(self, *args):
            assert self.iterator_closed
            return await super().__aexit__(*args)

    response = SimpleNamespace(
        status="completed", usage=SimpleNamespace(input_tokens=100, output_tokens=20)
    )
    stream = NestedIteratorStream(response)
    client = fake_client()
    client.responses.create.side_effect = None
    client.responses.create.return_value = stream
    model = ModelClient(Settings(), client=client)
    assert await model.call({"input": "iterator cleanup"}) is response
    assert stream.closed and stream.iterator_closed
