"""Native schemas, bounded requests, and real provider retry/budget code; no API calls."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from openai import APIConnectionError

from browser_agent.config import Settings
from browser_agent.context import ContextOverflow, build_request
from browser_agent.llm import Gateway, ProviderFailure
from browser_agent.storage import BudgetExceeded
from browser_agent.tools import ProtocolError, parse_call


def native(name="back", arguments="{}"):
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
        for i in range(20)
    ]
    request = build_request(
        "Original task",
        {"id": "current", "text": "Current facts"},
        "Remembered selected items",
        history,
    )
    assert "Original task" in request["input"][0]["content"]
    assert "Remembered selected items" in request["input"][0]["content"]
    assert request["input"][1:-1] == history[-6:]
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
