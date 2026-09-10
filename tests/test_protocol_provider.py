"""Native schemas, bounded requests, and real provider retry/budget code; no API calls."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from openai import APIConnectionError, APIStatusError

from browser_agent.budget import BudgetExceeded
from browser_agent.config import Settings
from browser_agent.context import HISTORY_MESSAGES, ContextOverflow, build_request
from browser_agent.llm import Gateway, ProviderFailure
from browser_agent.tools import ProtocolError, parse_call


def native(name="back", arguments='{"notebook": "No completed work yet."}'):
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


@pytest.mark.parametrize("bad", ["{broken", '{"unexpected": true}'])
def test_native_json_rejects_malformed_or_extra_fields(bad):
    assert parse_call(native())["arguments"] == {}
    with pytest.raises(ProtocolError):
        parse_call(native(arguments=bad))
    with pytest.raises(ProtocolError):
        parse_call(native("execute_javascript", '{"code":"danger()"}'))


def test_context_keeps_task_notebook_current_page_and_only_recent_exchanges():
    history = [
        {"type": "function_call_output", "call_id": str(i), "output": "old" + str(i)}
        for i in range(HISTORY_MESSAGES + 8)
    ]
    request = build_request(
        "Original task",
        {"id": "current", "text": "Current facts"},
        "Remembered selected items",
        history,
    )
    assert "Original task" in request["input"][0]["content"]
    assert "Remembered selected items" in request["input"][0]["content"]
    assert HISTORY_MESSAGES == 20
    assert request["input"][1:-1] == history[-HISTORY_MESSAGES:]
    assert "Current facts" in json.dumps(request["input"][-1])
    assert (
        request["truncation"] == "disabled" and request["parallel_tool_calls"] is False
    )


def fake_client(count=100, replies=()):
    response = SimpleNamespace(
        usage=SimpleNamespace(input_tokens=100, output_tokens=20)
    )
    create = AsyncMock(
        side_effect=list(replies) if replies else None, return_value=response
    )
    return SimpleNamespace(
        responses=SimpleNamespace(
            input_tokens=SimpleNamespace(
                count=AsyncMock(return_value=SimpleNamespace(input_tokens=count))
            ),
            create=create,
        ),
        close=AsyncMock(),
    )


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
    gateway = Gateway(Settings(reasoning="low"), client=client, emit=lambda e, d: events.append((e, d)))
    await gateway.call({"input": "actor"})
    await gateway.call({"input": "security"}, purpose="security")
    calls = client.responses.create.await_args_list
    assert calls[0].kwargs["reasoning"] == {"effort": "low"}
    assert calls[1].kwargs["reasoning"] == {"effort": "medium"}
    usages = [data for event, data in events if event == "model_usage"]
    assert [item["purpose"] for item in usages] == ["actor", "security"]
    assert [item["effort"] for item in usages] == ["low", "medium"]
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
    client = fake_client(count=20001 if reason == "tokens" else 100)
    gateway = Gateway(settings, client=client)
    with pytest.raises(ContextOverflow if reason == "tokens" else BudgetExceeded):
        await gateway.call({"input": "test"})
    client.responses.create.assert_not_awaited()
    assert gateway.cost_usd == 0


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


@pytest.mark.parametrize("payload", [{}, {"notebook": "x" * 6001}, {"notebook": ""}])
def test_runtime_tool_requires_bounded_notebook(payload):
    with pytest.raises(ProtocolError):
        parse_call(native(arguments=json.dumps(payload)))


def test_notebook_is_not_executable_argument_and_native_history_is_lossless():
    from browser_agent.tools import protocol_pair, tool_specs

    original = {"ref": "e7", "notebook": "Inspected A; B remains. No effects yet."}
    call = parse_call(native("click", json.dumps(original)))
    assert call["arguments"] == {"ref": "e7"}
    assert call["notebook"] == original["notebook"]
    assert (
        json.loads(protocol_pair(call, {"executed": True})[0]["arguments"]) == original
    )
    specs = tool_specs()
    assert "remember" not in {item["name"] for item in specs}
    assert all("notebook" in item["parameters"]["required"] for item in specs)


def test_custom_strict_eval_schema_does_not_require_or_extract_notebook():
    from browser_agent.tools import Strict, protocol_pair, tool_specs

    class Review(Strict):
        supported: bool

    registry = {"review": (Review, "Review the supplied report")}
    call = parse_call(native("review", '{"supported": true}'), registry)
    assert call["arguments"] == {"supported": True} and "notebook" not in call
    assert "notebook" not in tool_specs(registry)[0]["parameters"]["properties"]
    assert json.loads(protocol_pair(call, {})[0]["arguments"]) == {"supported": True}
