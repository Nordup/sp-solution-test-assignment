"""Native schemas, bounded requests, and real provider retry/budget code; no API calls."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from openai import APIConnectionError, APIStatusError

from browser_agent.budget import BudgetExceeded
from browser_agent.config import Settings
from browser_agent.context import ContextOverflow, build_request
from browser_agent.llm import (
    LONG_CONTEXT_THRESHOLD,
    Gateway,
    ProviderFailure,
    extract_compaction,
    usage_metadata,
)
from browser_agent.prompts import ACTOR
from browser_agent.tools import ProtocolError, parse_call


def native(
    name="playwright",
    arguments='{"command": "snapshot", "args": []}',
):
    return {
        "status": "completed",
        "output": [
            {
                "type": "function_call",
                "name": name,
                "call_id": "one",
                "arguments": arguments,
            }
        ],
    }


def test_luna_context_limits_and_compaction_defaults():
    settings = Settings()
    assert settings.max_input_tokens == 200000
    assert settings.max_output_tokens == 32768
    assert settings.compact_threshold == 150000


@pytest.mark.parametrize("bad", ["{broken", '{"unexpected": true}'])
def test_native_json_rejects_malformed_or_extra_fields(bad):
    assert parse_call(native())["arguments"] == {"command": "snapshot", "args": []}
    with pytest.raises(ProtocolError):
        parse_call(native(arguments=bad))
    with pytest.raises(ProtocolError):
        parse_call(native("run_code", '{"command":"run_code","args":[]}'))


def test_incomplete_native_response_feedback_preserves_prior_results():
    with pytest.raises(ProtocolError) as caught:
        parse_call({"status": "incomplete", "output": []})
    message = str(caught.value)
    assert "next model decision was incomplete" in message
    assert "no new browser call was dispatched" in message
    assert "Prior browser results remain valid" in message


def test_actual_cli_image_is_native_input_image_and_text_is_preserved():
    from browser_agent.tools import protocol_pair

    call = parse_call(native("playwright", '{"command":"screenshot","args":[]}'))
    pair = protocol_pair(
        call,
        {
            "status": "executed",
            "tool": "playwright",
            "output": {"path": "shot.png"},
            "content": [{"type": "image", "data": "aGk=", "mimeType": "image/png"}],
        },
    )
    output = pair[1]["output"]
    assert {block["type"] for block in output} == {"input_text", "input_image"}
    assert "shot.png" in output[0]["text"]
    assert output[1]["image_url"].startswith("data:image/png;base64,")


@pytest.mark.parametrize(
    ("name", "payload", "expected"),
    [
        ("playwright", {"command": "", "args": []}, "command"),
        ("playwright", {"command": "click", "args": [1]}, "args"),
        ("read_browser_artifact", {"path": "", "offset": 0}, "path"),
    ],
)
def test_invalid_tool_arguments_report_safe_actionable_validation(name, payload, expected):
    with pytest.raises(ProtocolError) as caught:
        parse_call(
            native(
                name,
                json.dumps(payload),
            )
        )
    message = str(caught.value)
    assert name in message and expected in message
    assert "private-secret-selector" not in message
    assert "Native arguments failed" not in message


def test_context_keeps_task_evidence_and_full_history_until_compaction():
    history = [
        {"type": "function_call_output", "call_id": str(i), "output": "old" + str(i)}
        for i in range(28)
    ]
    request = build_request(
        "Original task",
        "Current facts",
        history,
    )
    assert "Original task" in request["input"][0]["content"]
    assert "Current facts" not in request["input"][0]["content"]
    assert request["input"][1:] == history
    encoded = json.dumps(request["input"][0])
    assert "Current facts" not in encoded
    assert (
        request["truncation"] == "disabled"
        and request["parallel_tool_calls"] is False
        and request["context_management"] == [
            {"type": "compaction", "compact_threshold": 150000}
        ]
    )


def test_context_carries_opaque_compaction_before_pinned_facts():
    compaction = {
        "type": "compaction",
        "id": "cmp-1",
        "encrypted_content": "opaque-ciphertext",
    }
    request = build_request(
        "Original task",
        "Fresh facts",
        [{"type": "function_call_output", "call_id": "new", "output": "done"}],
        compaction=compaction,
    )
    assert request["input"][0] == compaction
    assert "Original task" in request["input"][1]["content"]
    assert "Fresh facts" not in request["input"][1]["content"]


def test_compaction_extraction_requires_opaque_payload():
    first = SimpleNamespace(
        type="compaction", id="cmp-1", encrypted_content="old-ciphertext"
    )
    valid = SimpleNamespace(
        type="compaction",
        id="cmp-2",
        encrypted_content="opaque-ciphertext",
        created_by="server",
    )
    malformed = SimpleNamespace(type="compaction", encrypted_content="")
    response = SimpleNamespace(output=[first, malformed, valid])
    assert extract_compaction(response) == {
        "type": "compaction",
        "id": "cmp-2",
        "encrypted_content": "opaque-ciphertext",
    }
    assert extract_compaction(SimpleNamespace(output=[malformed])) is None


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
    gateway = Gateway(
        Settings(),
        client=client,
        emit=lambda event, data: events.append((event, data)),
    )
    client.responses.create.side_effect = None
    client.responses.create.return_value = FakeStream(response, stream_events)
    returned = await gateway.call({"input": "diagnostic test"})
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
        and data["phase"] in {"input_count", "response_create", "stream_read", "stream_close"}
    }
    assert len(request_ids) == 1


async def test_stream_diagnostics_capture_sse_and_response_error_fields():
    async def run_case(response, stream_events):
        stream = FakeStream(response, stream_events)
        client = fake_client()
        client.responses.create.side_effect = None
        client.responses.create.return_value = stream
        events = []
        gateway = Gateway(
            Settings(),
            client=client,
            emit=lambda event, data: events.append((event, data)),
        )
        with pytest.raises(ProviderFailure):
            await gateway.call({"input": "error diagnostics"})
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
            status="completed", usage=SimpleNamespace(input_tokens=100, output_tokens=20)
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
    gateway = Gateway(
        Settings(), client=fake_client(), emit=lambda e, d: events.append((e, d))
    )
    await gateway.call({"input": "private prompt"})
    assert any(event == "model_first_token" for event, _ in events)
    metrics = next(data for event, data in events if event == "model_usage")
    assert metrics["total_tokens"] == 120
    assert 0 <= metrics["ttft_seconds"] <= metrics["latency_seconds"]
    assert metrics["usage_metadata"]["total_cost"] > 0


async def test_langsmith_parentage_metrics_and_private_content(tmp_path, monkeypatch):
    from uuid import uuid4

    from langsmith import tracing_context

    from browser_agent.telemetry import Events

    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    client = MagicMock()
    monkeypatch.setattr("browser_agent.telemetry.Client", lambda: client)
    events = Events(tmp_path / "events.jsonl")
    run_id = str(uuid4())
    events(
        "run_started",
        {
            "run_id": run_id,
            "model": "gpt-5.6-luna",
            "budget_usd": 5,
            "task": "PRIVATE TASK",
            "url": "https://private.example",
        },
    )
    with tracing_context(enabled=False), events.span("decide", step=1):
        gateway = Gateway(Settings(), client=fake_client(), emit=events)
        await gateway.call({"input": "PRIVATE PROMPT"})
        await gateway.call({"input": "PRIVATE REVIEW"}, purpose="security")
        events(
            "tool_proposed",
            {"step": 1, "tool": "click", "arguments": {"text": "PRIVATE ARGUMENT"}},
        )
    events(
        "result", {"status": "completed", "summary": "PRIVATE RESULT", "run_id": run_id}
    )
    events.close()

    assert events.root.id == events.root.trace_id
    node = events.root.child_runs[0]
    assert node.name == "decide" and len(node.child_runs) == 2
    for span in node.child_runs:
        assert span.run_type == "llm" and span.parent_run_id == node.id
        assert span.outputs["usage_metadata"]["total_tokens"] == 120
        assert len(span.events) == 1 and span.events[0]["name"] == "new_token"
        assert span.end_time is not None
    assert [span.metadata["reasoning_effort"] for span in node.child_runs] == [
        "max",
        "medium",
    ]
    sent = str(client.mock_calls)
    assert "PRIVATE" not in sent and "private.example" not in sent
    assert client.create_run.call_count == 4
    client.flush.assert_called_once_with(timeout=5)


def test_usage_pricing_accounts_for_cache_and_reasoning():
    usage = SimpleNamespace(
        input_tokens=1000,
        output_tokens=200,
        input_tokens_details=SimpleNamespace(cached_tokens=400, cache_write_tokens=200),
        output_tokens_details=SimpleNamespace(reasoning_tokens=150),
    )
    metrics = usage_metadata(usage, "gpt-5.6-luna")
    assert metrics["input_cost"] == pytest.approx(
        (400 * 0.2 + 400 * 0.02 + 200 * 0.25) / 1_000_000
    )
    assert metrics["output_cost"] == pytest.approx(200 * 1.2 / 1_000_000)
    assert metrics["input_token_details"] == {"cache_read": 400, "cache_creation": 200}
    assert metrics["output_token_details"] == {"reasoning": 150}


def test_long_context_usage_pricing_applies_published_multipliers():
    usage = SimpleNamespace(
        input_tokens=LONG_CONTEXT_THRESHOLD + 1,
        output_tokens=200,
        input_tokens_details=SimpleNamespace(
            cached_tokens=400, cache_write_tokens=200
        ),
        output_tokens_details=SimpleNamespace(reasoning_tokens=150),
    )
    metrics = usage_metadata(usage, "gpt-5.6-luna")
    assert metrics["input_cost"] == pytest.approx(
        ((usage.input_tokens - 600) * 0.4 + 400 * 0.04 + 200 * 0.5)
        / 1_000_000
    )
    assert metrics["output_cost"] == pytest.approx(200 * 1.8 / 1_000_000)


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
    gateway = Gateway(
        Settings(), client=client, emit=lambda e, d: events.append((e, d))
    )
    if outcome in {"incomplete", "no_delta"}:
        assert await gateway.call({"input": "test"}) is response
    else:
        with pytest.raises(
            asyncio.CancelledError if outcome == "cancelled" else ProviderFailure
        ):
            await gateway.call({"input": "test"})
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
    assert gateway.cost_usd > 0


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
    gateway = Gateway(
        Settings(), client=client, emit=lambda event, data: events.append((event, data))
    )
    await gateway.call({"input": "delayed close"})
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
                    yield SimpleNamespace(type="response.completed", response=self.response)
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
    gateway = Gateway(Settings(), client=client)
    assert await gateway.call({"input": "iterator cleanup"}) is response
    assert stream.closed and stream.iterator_closed


async def test_tracing_off_and_export_failure_do_not_stop_model(tmp_path, monkeypatch):
    from uuid import uuid4

    from browser_agent.telemetry import Events

    factory = MagicMock(side_effect=RuntimeError("PRIVATE SECRET"))
    monkeypatch.setattr("browser_agent.telemetry.Client", factory)
    for enabled in ("false", "true"):
        monkeypatch.setenv("LANGSMITH_TRACING", enabled)
        path = tmp_path / (enabled + ".jsonl")
        events = Events(path)
        events(
            "run_started",
            {"run_id": str(uuid4()), "model": "gpt-5.6-luna", "budget_usd": 5},
        )
        gateway = Gateway(Settings(), client=fake_client(), emit=events)
        await gateway.call({"input": "test"})
        events.close()
        records = path.read_text()
        assert "PRIVATE SECRET" not in records and '"model_usage"' in records
        assert ('"tracing_error"' in records) is (enabled == "true")
    assert factory.call_count == 1


async def test_provider_retries_transient_error_and_charges_unknown_attempt():
    lost = APIConnectionError(
        request=httpx.Request("POST", "https://api.openai.com/v1/responses")
    )
    success = SimpleNamespace(usage=SimpleNamespace(input_tokens=100, output_tokens=20))
    client = fake_client(replies=[lost, success])
    sleep = AsyncMock()
    gateway = Gateway(Settings(), client=client, sleep=sleep)
    await gateway.call({"input": "test"})
    assert client.responses.create.await_count == 2 and sleep.await_count == 1
    assert gateway.cost_usd > (100 * 0.25 + 20 * 1.2) / 1_000_000
    assert client.responses.create.call_args.kwargs["store"] is False


async def test_security_review_uses_same_budgeted_client_at_medium_effort():
    client = fake_client()
    events = []
    gateway = Gateway(
        Settings(), client=client, emit=lambda e, d: events.append((e, d))
    )
    await gateway.call({"input": "actor"})
    await gateway.call({"input": "security"}, purpose="security")
    calls = client.responses.create.await_args_list
    assert calls[0].kwargs["reasoning"] == {"effort": "max"}
    assert calls[1].kwargs["reasoning"] == {"effort": "medium"}
    usages = [data for event, data in events if event == "model_usage"]
    assert [item["purpose"] for item in usages] == ["actor", "security"]
    assert [item["effort"] for item in usages] == ["max", "medium"]
    assert gateway.usage["total_tokens"] == 240
    assert gateway.usage["calls"] == 2
    assert gateway.reported_cost_usd == pytest.approx(
        sum(item["total_cost"] for item in usages)
    )
    assert gateway.cost_usd > 0


async def test_provider_retry_exhaustion_is_bounded_and_not_refunded():
    lost = APIConnectionError(
        request=httpx.Request("POST", "https://api.openai.com/v1/responses")
    )
    client = fake_client(replies=[lost, lost, lost])
    gateway = Gateway(Settings(max_retries=2), client=client, sleep=AsyncMock())
    with pytest.raises(ProviderFailure):
        await gateway.call({"input": "test"})
    assert client.responses.create.await_count == 3 and gateway.cost_usd > 0


@pytest.mark.parametrize("reason", ["tokens", "money"])
async def test_admission_blocks_generation_before_call(reason):
    settings = Settings(budget_usd=0.000001 if reason == "money" else 5)
    client = fake_client(count=200001 if reason == "tokens" else 100)
    gateway = Gateway(settings, client=client)
    with pytest.raises(ContextOverflow if reason == "tokens" else BudgetExceeded):
        await gateway.call({"input": "test"})
    client.responses.create.assert_not_awaited()
    assert gateway.cost_usd == 0


async def test_long_input_is_admitted_and_compaction_is_create_only():
    client = fake_client(count=200000)
    gateway = Gateway(Settings(), client=client)
    request = build_request(
        "Task",
        {"id": "current", "text": "facts"},
        "notebook",
        [],
    )
    await gateway.call(request)
    assert client.responses.input_tokens.count.call_args.kwargs.get(
        "context_management"
    ) is None
    assert client.responses.create.call_args.kwargs["context_management"] == [
        {"type": "compaction", "compact_threshold": 150000}
    ]


async def test_model_input_cap_rejects_before_generation():
    client = fake_client(count=200001)
    gateway = Gateway(Settings(), client=client)
    with pytest.raises(ContextOverflow):
        await gateway.call({"input": "too large"})
    client.responses.create.assert_not_awaited()


@pytest.mark.parametrize("error_kind", ["connection", "rate_limit"])
async def test_count_transient_retry_admits_only_after_success(error_kind):
    request = httpx.Request("POST", "https://api.openai.com/v1/responses/input_tokens")
    failure = (
        APIConnectionError(request=request)
        if error_kind == "connection"
        else APIStatusError(
            "Rate limited",
            response=httpx.Response(429, request=request, headers={"retry-after": "2"}),
            body=None,
        )
    )
    client = fake_client()
    client.responses.input_tokens.count.side_effect = [
        failure,
        SimpleNamespace(input_tokens=100),
    ]
    sleep = AsyncMock()
    gateway = Gateway(Settings(), client=client, sleep=sleep)

    async def check_no_dispatch(_delay):
        client.responses.create.assert_not_awaited()
        assert gateway.cost_usd == 0

    sleep.side_effect = check_no_dispatch
    await gateway.call({"input": "test"})
    assert client.responses.input_tokens.count.await_count == 2
    assert client.responses.create.await_count == 1
    sleep.assert_awaited_once_with(2 if error_kind == "rate_limit" else 1)
    assert gateway.cost_usd == (100 * 0.25 + 20 * 1.2) / 1_000_000


async def test_count_retry_exhaustion_never_dispatches_or_charges_generation():
    client = fake_client()
    client.responses.input_tokens.count.side_effect = APIConnectionError(
        request=httpx.Request(
            "POST", "https://api.openai.com/v1/responses/input_tokens"
        )
    )
    sleep = AsyncMock()
    gateway = Gateway(Settings(max_retries=2), client=client, sleep=sleep)
    with pytest.raises(ProviderFailure, match="input_count request failed after 3"):
        await gateway.call({"input": "test"})
    assert client.responses.input_tokens.count.await_count == 3
    assert [call.args[0] for call in sleep.await_args_list] == [1, 2]
    client.responses.create.assert_not_awaited()
    assert gateway.cost_usd == 0


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"notebook": "x" * 6001, "message": ""},
        {"notebook": "", "message": ""},
    ],
)
def test_runtime_tool_requires_bounded_notebook(payload):
    with pytest.raises(ProtocolError):
        parse_call(native(arguments=json.dumps(payload)))


def test_native_cli_arguments_are_lossless_and_no_notebook_is_required():
    from browser_agent.tools import protocol_pair, tool_specs

    original = {"command": "fill", "args": ["e5", "Armenia"]}
    call = parse_call(native("playwright", json.dumps(original)))
    assert call["arguments"] == original
    assert json.loads(protocol_pair(call, {"executed": True})[0]["arguments"]) == original
    specs = tool_specs()
    assert {"playwright", "read_browser_artifact", "ask_user", "finish"} == {
        item["name"] for item in specs
    }


def test_cli_tools_are_explicit_and_no_custom_selector_tools_remain():
    from browser_agent.tools import tool_specs

    spec = next(item for item in tool_specs() if item["name"] == "playwright")
    assert set(spec["parameters"]["required"]) == {"command", "args"}
    names = {item["name"] for item in tool_specs()}
    assert names == {"playwright", "read_browser_artifact", "ask_user", "finish"}
    read_spec = next(item for item in tool_specs() if item["name"] == "read_browser_artifact")
    assert set(read_spec["parameters"]["required"]) >= {"path", "offset"}
    assert "PUBLIC PROGRESS" not in ACTOR


def test_security_review_has_only_boolean_approval_contract():
    from browser_agent.tools import SECURITY_REGISTRY, tool_specs

    spec = tool_specs(SECURITY_REGISTRY)[0]
    assert set(spec["parameters"]["properties"]) == {"needs_approval"}
    assert spec["parameters"]["required"] == ["needs_approval"]


def test_custom_strict_eval_schema_does_not_require_or_extract_notebook():
    from browser_agent.tools import Strict, protocol_pair, tool_specs

    class Review(Strict):
        supported: bool

    registry = {"review": (Review, "Review the supplied report")}
    call = parse_call(native("review", '{"supported": true}'), registry)
    assert call["arguments"] == {"supported": True} and "notebook" not in call
    assert "message" not in call
    assert "notebook" not in tool_specs(registry)[0]["parameters"]["properties"]
    assert "message" not in tool_specs(registry)[0]["parameters"]["properties"]
    assert json.loads(protocol_pair(call, {})[0]["arguments"]) == {"supported": True}
