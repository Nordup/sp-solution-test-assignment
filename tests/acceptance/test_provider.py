"""Real gateway + durable ledger, fake wire transport: no credentials or spend."""

from types import SimpleNamespace

import httpx
import pytest
from openai import APIStatusError, APITimeoutError

from browser_agent.config import Settings
from browser_agent.context import ContextOverflow
from browser_agent.llm import Gateway, ProviderFailure
from browser_agent.storage import BudgetExceeded, Store


def error(status=503, retry_after=None):
    request = httpx.Request("POST", "https://example.invalid/responses")
    response = httpx.Response(
        status,
        request=request,
        headers={"retry-after": retry_after} if retry_after else {},
    )
    return APIStatusError("Synthetic failure", response=response, body=None)


def success():
    return SimpleNamespace(usage=SimpleNamespace(input_tokens=100, output_tokens=10))


class Transport:
    def __init__(self, outcomes, input_tokens=100, count_error=None):
        self.outcomes = iter(outcomes)
        self.input_count = input_tokens
        self.count_error = count_error
        self.count_calls = []
        self.create_calls = []
        self.responses = self
        self.input_tokens = SimpleNamespace(count=self.count)

    async def count(self, **kwargs):
        self.count_calls.append(kwargs)
        if self.count_error:
            raise self.count_error
        return SimpleNamespace(input_tokens=self.input_count)

    async def create(self, **kwargs):
        self.create_calls.append(kwargs)
        outcome = next(self.outcomes)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def gateway(tmp_path, outcomes, cap=10_000, **kwargs):
    store = Store(tmp_path / "ledger.db")
    store.create_budget("run", cap)
    transport = Transport(outcomes, **kwargs)
    sleeps, events = [], []

    async def sleep(delay):
        sleeps.append(delay)

    instance = Gateway(
        Settings(max_output_tokens=128),
        store,
        "run",
        client=transport,
        sleep=sleep,
        emit=lambda kind, payload: events.append((kind, payload)),
    )
    return instance, store, transport, sleeps, events


async def test_p13_retry_fail_twice_then_succeed_accounts_every_attempt(tmp_path):
    client, store, transport, sleeps, events = gateway(
        tmp_path, [error(), error(), success()]
    )
    response = await client.call({"input": "A synthetic task"})
    assert response.usage.output_tokens == 10
    assert len(transport.create_calls) == 3
    assert len(sleeps) == 2
    admitted = [payload for event, payload in events if event == "model_admitted"]
    assert [event["attempt"] for event in admitted] == [1, 2, 3]
    assert store.budget("run")["unknown"] == 2 * admitted[0]["reserved_microusd"]
    assert store.budget("run")["settled"] == 37


async def test_p13_auth_error_does_not_retry(tmp_path):
    client, store, transport, sleeps, _ = gateway(tmp_path, [error(401)])
    with pytest.raises(ProviderFailure, match="1 attempt"):
        await client.call({"input": "Synthetic task"})
    assert len(transport.create_calls) == 1
    assert sleeps == []
    assert store.budget("run")["unknown"] > 0


async def test_exhausted_provider_retry_stops_after_three(tmp_path):
    client, _, transport, sleeps, _ = gateway(
        tmp_path, [error(), error(), error(), success()]
    )
    with pytest.raises(ProviderFailure, match="3 attempt"):
        await client.call({"input": "Synthetic task"})
    assert len(transport.create_calls) == 3
    assert len(sleeps) == 2


async def test_p07_unknown_timeout_retains_reservation_across_restart(tmp_path):
    timeout = APITimeoutError(
        request=httpx.Request("POST", "https://example.invalid/responses")
    )
    client, store, transport, _, _ = gateway(tmp_path, [timeout, success()], cap=200)
    with pytest.raises(BudgetExceeded):
        await client.call({"input": "Synthetic task"})
    assert len(transport.create_calls) == 1
    restart = Store(store.path)
    assert restart.budget("run")["unknown"] == 179
    with pytest.raises(BudgetExceeded):
        restart.reserve("run", "retry-after-checkpoint", 179)


async def test_p05_insufficient_reservation_never_dispatches(tmp_path):
    client, store, transport, _, _ = gateway(tmp_path, [success()], cap=178)
    with pytest.raises(BudgetExceeded):
        await client.call({"input": "Synthetic task"})
    assert transport.create_calls == []
    assert store.budget("run")["reserved"] == 0


async def test_count_failure_and_overflow_prevent_generation(tmp_path):
    client, store, transport, _, _ = gateway(
        tmp_path, [success()], count_error=error(400)
    )
    with pytest.raises(ProviderFailure, match="No generation"):
        await client.call({"input": "Synthetic task"})
    assert transport.create_calls == []
    assert store.budget("run")["reserved"] == 0
    transport.count_error = None
    transport.input_count = 20_001
    with pytest.raises(ContextOverflow):
        await client.call({"input": "Synthetic task"})
    assert transport.create_calls == []


async def test_count_covers_serialized_tools_and_input(tmp_path):
    client, _, transport, _, _ = gateway(tmp_path, [success()])
    request = {
        "input": [{"role": "user", "content": "Synthetic task"}],
        "tools": [{"name": "test"}],
        "truncation": "disabled",
    }
    await client.call(request)
    counted = transport.count_calls[0]
    generated = transport.create_calls[0]
    assert {key: generated[key] for key in counted} == counted
    assert generated["max_output_tokens"] == client.settings.max_output_tokens
    assert generated["store"] is False


async def test_missing_usage_retains_unknown_reservation(tmp_path):
    client, store, transport, _, _ = gateway(tmp_path, [SimpleNamespace(usage=None)])
    with pytest.raises(ProviderFailure, match="omitted usage"):
        await client.call({"input": "Synthetic task"})
    assert len(transport.create_calls) == 1
    assert store.budget("run")["unknown"] == 179


async def test_retry_after_honored_and_long_wait_stops(tmp_path):
    client, _, transport, sleeps, _ = gateway(tmp_path, [error(429, "7"), success()])
    await client.call({"input": "Synthetic task"})
    assert sleeps == [7]
    transport.outcomes = iter([error(429, "120"), success()])
    with pytest.raises(ProviderFailure, match="longer than 60"):
        await client.call({"input": "Synthetic task"})
    assert len(transport.create_calls) == 3
    assert sleeps == [7]


def test_closed_case_cannot_spend_released_aggregate_allowance(tmp_path):
    store = Store(tmp_path / "ledger.db")
    store.create_budget("release", 1000, "release")
    store.reserve_case("run", 500, ["release"])
    store.reserve("run", "actor", 100)
    store.settle("actor", 37)
    store.finish_case("run")
    assert store.budget("release")["remaining"] == 963
    with pytest.raises(BudgetExceeded):
        Store(store.path).reserve("run", "old-checkpoint", 1)
    store.finish_case("run")
    assert store.budget("release")["settled"] == 37


def test_unknown_case_retains_aggregate_hold_and_seals_task(tmp_path):
    store = Store(tmp_path / "ledger.db")
    store.create_budget("release", 1000, "release")
    store.reserve_case("run", 500, ["release"])
    store.reserve("run", "actor", 100)
    store.mark_unknown("actor")
    store.finish_case("run")
    assert store.budget("release")["unknown"] == 500
    with pytest.raises(BudgetExceeded):
        store.reserve("run", "new-call", 1)


def native_success(name, arguments):
    import json

    result = success()
    result.model_dump = lambda **_kwargs: {
        "status": "completed",
        "output": [
            {
                "type": "function_call",
                "name": name,
                "call_id": "synthetic-native-id",
                "arguments": json.dumps(arguments),
            }
        ],
    }
    return result


async def test_p06_actual_reviewer_and_completion_wrappers_share_actor_ledger(tmp_path):
    review = native_success(
        "risk_review",
        {
            "classification": "ordinary",
            "effect_summary": "Read visible content",
            "reason": "Read operation",
        },
    )
    completion = native_success(
        "completion_review", {"supported": True, "reason": "Observed matching state"}
    )
    client, store, transport, _sleeps, events = gateway(
        tmp_path, [success(), review, completion]
    )
    await client.call({"input": "Actor request"})
    assert (
        await client.review(
            "Read content",
            {"tool": "click", "args": {"ref": "x"}},
            {"url": "https://example.invalid"},
        )
    )["classification"] == "ordinary"
    assert (
        await client.verify_completion("Read content", {}, {"obs": "Matching result"})
    )["supported"]
    assert len(transport.create_calls) == 3
    assert store.budget("run")["settled"] == 111
    assert [data["purpose"] for kind, data in events if kind == "model_admitted"] == [
        "actor",
        "risk_reviewer",
        "completion_reviewer",
    ]


async def test_p06_completion_helper_cannot_bypass_remaining_actor_cap(tmp_path):
    review = native_success(
        "risk_review",
        {
            "classification": "ordinary",
            "effect_summary": "Read content",
            "reason": "Read operation",
        },
    )
    client, store, transport, _sleeps, _events = gateway(
        tmp_path, [success(), review, success()], cap=220
    )
    await client.call({"input": "Actor request"})
    await client.review("Read content", {}, {})
    with pytest.raises(BudgetExceeded):
        await client.verify_completion("Read content", {}, {})
    assert len(transport.create_calls) == 2
    assert store.budget("run")["settled"] == 74
