"""Model admission, retries, and shared budget behavior."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from openai import APIConnectionError, APIStatusError

from browser_agent.config import Settings
from browser_agent.context import ContextOverflow, build_request
from browser_agent.model import BudgetExceeded, ModelClient, ProviderFailure
from tests.support.model import fake_client


async def test_provider_retries_transient_error_and_charges_unknown_attempt():
    lost = APIConnectionError(
        request=httpx.Request("POST", "https://api.openai.com/v1/responses")
    )
    success = SimpleNamespace(usage=SimpleNamespace(input_tokens=100, output_tokens=20))
    client = fake_client(replies=[lost, success])
    sleep = AsyncMock()
    model = ModelClient(Settings(), client=client, sleep=sleep)
    await model.call({"input": "test"})
    assert client.responses.create.await_count == 2 and sleep.await_count == 1
    assert model.cost_usd > (100 * 0.25 + 20 * 1.2) / 1_000_000
    assert client.responses.create.call_args.kwargs["store"] is False


async def test_security_review_uses_same_budgeted_client_at_medium_effort():
    client = fake_client()
    events = []
    model = ModelClient(
        Settings(), client=client, emit=lambda e, d: events.append((e, d))
    )
    await model.call({"input": "actor"})
    await model.call({"input": "security"}, purpose="security")
    calls = client.responses.create.await_args_list
    assert calls[0].kwargs["reasoning"] == {"effort": "max"}
    assert calls[1].kwargs["reasoning"] == {"effort": "medium"}
    usages = [data for event, data in events if event == "model_usage"]
    assert [item["purpose"] for item in usages] == ["actor", "security"]
    assert [item["effort"] for item in usages] == ["max", "medium"]
    assert model.usage["total_tokens"] == 240
    assert model.usage["calls"] == 2
    assert model.reported_cost_usd == pytest.approx(
        sum(item["total_cost"] for item in usages)
    )
    assert model.cost_usd > 0


async def test_provider_retry_exhaustion_is_bounded_and_not_refunded():
    lost = APIConnectionError(
        request=httpx.Request("POST", "https://api.openai.com/v1/responses")
    )
    client = fake_client(replies=[lost, lost, lost])
    model = ModelClient(Settings(max_retries=2), client=client, sleep=AsyncMock())
    with pytest.raises(ProviderFailure):
        await model.call({"input": "test"})
    assert client.responses.create.await_count == 3 and model.cost_usd > 0


@pytest.mark.parametrize("reason", ["tokens", "money"])
async def test_admission_blocks_generation_before_call(reason):
    settings = Settings(budget_usd=0.000001 if reason == "money" else 5)
    client = fake_client(count=200001 if reason == "tokens" else 100)
    model = ModelClient(settings, client=client)
    with pytest.raises(ContextOverflow if reason == "tokens" else BudgetExceeded):
        await model.call({"input": "test"})
    client.responses.create.assert_not_awaited()
    assert model.cost_usd == 0


async def test_long_input_is_admitted_and_compaction_is_create_only():
    client = fake_client(count=200000)
    model = ModelClient(Settings(), client=client)
    request = build_request("Task", history=[])
    await model.call(request)
    assert (
        client.responses.input_tokens.count.call_args.kwargs.get("context_management")
        is None
    )
    assert client.responses.create.call_args.kwargs["context_management"] == [
        {"type": "compaction", "compact_threshold": 150000}
    ]


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
    model = ModelClient(Settings(), client=client, sleep=sleep)

    async def check_no_dispatch(_delay):
        client.responses.create.assert_not_awaited()
        assert model.cost_usd == 0

    sleep.side_effect = check_no_dispatch
    await model.call({"input": "test"})
    assert client.responses.input_tokens.count.await_count == 2
    assert client.responses.create.await_count == 1
    sleep.assert_awaited_once_with(2 if error_kind == "rate_limit" else 1)
    assert model.cost_usd == (100 * 0.25 + 20 * 1.2) / 1_000_000


async def test_count_retry_exhaustion_never_dispatches_or_charges_generation():
    client = fake_client()
    client.responses.input_tokens.count.side_effect = APIConnectionError(
        request=httpx.Request(
            "POST", "https://api.openai.com/v1/responses/input_tokens"
        )
    )
    sleep = AsyncMock()
    model = ModelClient(Settings(max_retries=2), client=client, sleep=sleep)
    with pytest.raises(ProviderFailure, match="input_count request failed after 3"):
        await model.call({"input": "test"})
    assert client.responses.input_tokens.count.await_count == 3
    assert [call.args[0] for call in sleep.await_args_list] == [1, 2]
    client.responses.create.assert_not_awaited()
    assert model.cost_usd == 0
