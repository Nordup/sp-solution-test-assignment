"""Real graph admission for actor questions; only paid model calls are scripted."""

import json

import pytest
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command
from test_graph_resume import approval_answer, choose_ref, click, graph_case, pending
from test_provider import gateway as paid_gateway
from test_provider import native_success, success

from browser_agent.context import ContextOverflow
from browser_agent.llm import ProviderFailure
from browser_agent.safety import resolved_effect
from browser_agent.storage import BudgetExceeded
from browser_agent.tools import ProtocolError


def ask(question="May I send the prepared application?", kind="clarification"):
    return lambda _obs: ("ask_user", {"kind": kind, "question": question})


def clarification_reviewer(
    gateway, classification="action_approval", error=None, sources=None
):
    calls = []

    async def review(task, question, context, evidence):
        calls.append(
            {
                "task": task,
                "question": question,
                "context": context,
                "evidence": evidence,
            }
        )
        if error:
            raise error
        return {
            "classification": classification,
            "reason": "Synthetic semantic classification; the real graph still enforces admission.",
            "evidence": sources(evidence) if sources else [],
        }

    gateway.review_clarification = review
    return calls


async def test_approval_question_repairs_to_exact_host_approval_before_effect(tmp_path):
    async with graph_case(tmp_path, script=[ask(), click("Send application")]) as (
        browser,
        gateway,
        store,
        _runtime,
        graph,
        config,
        initial,
        _events,
    ):
        calls = clarification_reviewer(gateway)
        paused = await graph.ainvoke(initial, config)
        assert pending(paused)["kind"] == "approval"
        assert paused["clarification_repairs"] == 1
        assert len(calls) == 1
        assert await browser.page.evaluate("window.effects || 0") == 0
        assert store.action(paused["action_id"]) is None
        assert not paused.get("clarifications")
        assert any("question_admitted" in json.dumps(req) for req in gateway.requests)
        assert "user_answer" not in json.dumps(paused["history"])
        done = await graph.ainvoke(approval_answer(paused), config)
        assert await browser.page.evaluate("window.effects") == 1
        assert store.approval(paused["approval_id"])["status"] == "consumed"
        assert len(calls) == 1
        assert done["result"]["status"] == "partial"  # scripted boundary stop


@pytest.mark.parametrize("classification", ["missing_information", "uncertain"])
async def test_genuine_ambiguity_passes_to_human_without_reviewer_replay(
    tmp_path, classification
):
    question = "Which of the two available dates do you prefer?"
    async with graph_case(tmp_path, script=[ask(question)]) as (
        browser,
        gateway,
        _store,
        runtime,
        graph,
        config,
        initial,
        _events,
    ):
        calls = clarification_reviewer(gateway, classification)
        first = await graph.ainvoke(initial, config)
        assert pending(first) == {"kind": "clarification", "question": question}
        assert len(calls) == 1
        async with AsyncSqliteSaver.from_conn_string(
            str(tmp_path / "checkpoints.sqlite")
        ) as saver:
            done = await runtime.compile(saver).ainvoke(
                Command(resume={"answer": "The later date."}), config
            )
        assert len(calls) == 1
        assert done["clarifications"] == ["The later date."]
        assert await browser.page.evaluate("window.effects || 0") == 0


async def test_clarification_repairs_are_bounded_before_truthful_handover(tmp_path):
    async with graph_case(tmp_path, script=[ask()] * 5) as (
        browser,
        gateway,
        _store,
        _runtime,
        graph,
        config,
        initial,
        _events,
    ):
        calls = clarification_reviewer(gateway)
        paused = await graph.ainvoke(initial, config)
        assert pending(paused)["kind"] == "clarification"
        assert "exhausted two" in pending(paused)["question"]
        assert paused["clarification_repairs"] == 2
        assert gateway.calls == 3 and len(calls) == 2
        assert await browser.page.evaluate("window.effects || 0") == 0
        assert not paused.get("clarifications")


@pytest.mark.parametrize("kind", ["login", "challenge"])
async def test_authentication_and_challenge_bypass_clarification_review(tmp_path, kind):
    async with graph_case(
        tmp_path, script=[ask("Manual intervention required.", kind)]
    ) as (_browser, gateway, _store, _runtime, graph, config, initial, _events):
        calls = clarification_reviewer(
            gateway, error=AssertionError("Must not review manual auth")
        )
        paused = await graph.ainvoke(initial, config)
        assert pending(paused)["kind"] == kind
        assert calls == []


@pytest.mark.parametrize(
    "error",
    [
        ProviderFailure("offline"),
        BudgetExceeded("cap reached"),
        ProtocolError("invalid native review"),
        ContextOverflow("request too large"),
    ],
)
async def test_clarification_review_failure_or_budget_hands_over_without_answer(
    tmp_path, error
):
    async with graph_case(tmp_path, script=[ask()]) as (
        browser,
        gateway,
        _store,
        _runtime,
        graph,
        config,
        initial,
        _events,
    ):
        calls = clarification_reviewer(gateway, error=error)
        paused = await graph.ainvoke(initial, config)
        assert pending(paused)["kind"] == "clarification"
        assert "review unavailable" in pending(paused)["question"]
        assert type(error).__name__ in pending(paused)["question"]
        assert str(error) not in pending(paused)["question"]
        assert len(calls) == gateway.calls == 1
        assert not paused.get("clarifications")
        assert await browser.page.evaluate("window.effects || 0") == 0


@pytest.mark.parametrize("forged", [False, True])
async def test_already_available_fact_requires_actual_source_quote(tmp_path, forged):
    async with graph_case(tmp_path, script=[ask("Who is the recipient?")]) as (
        browser,
        gateway,
        _store,
        _runtime,
        graph,
        config,
        initial,
        _events,
    ):
        calls = clarification_reviewer(
            gateway,
            "already_available",
            sources=lambda evidence: [
                {
                    "source_id": "invented" if forged else next(iter(evidence)),
                    "quote": "Acme",
                }
            ],
        )
        result = await graph.ainvoke(initial, config)
        assert len(calls) == 1
        if forged:
            assert pending(result)["kind"] == "clarification"
            assert "could not ground" in pending(result)["question"]
            assert gateway.calls == 1
        else:
            assert result["clarification_repairs"] == 1
            assert result["result"]["status"] == "partial"
            assert any(
                "Acme" in group[-1].get("output", "")
                and "question_admitted" in group[-1].get("output", "")
                for group in result["history"]
            )
        assert not result.get("clarifications")
        assert await browser.page.evaluate("window.effects || 0") == 0


async def test_clarification_repair_cannot_override_prior_effect_denial(tmp_path):
    async with graph_case(
        tmp_path, script=[ask("May I send it anyway?"), click("Send application")]
    ) as (browser, gateway, store, _runtime, graph, config, initial, _events):
        observation = await browser.observe()
        action = {
            "tool": "click",
            "args": {"ref": choose_ref(observation, "Send application")},
        }
        metadata = await browser.action_context(
            action["tool"], action["args"], observation["id"]
        )
        request_id = store.request_approval(
            initial["run_id"],
            "previous-denied-action",
            action,
            resolved_effect(action, metadata),
            metadata["generation"],
        )
        store.decide_approval(request_id, False)
        clarification_reviewer(gateway)
        done = await graph.ainvoke(initial, config)
        assert done["result"]["status"] == "partial"
        assert done["clarification_repairs"] == 1
        assert store.approval(request_id)["status"] == "denied"
        assert await browser.page.evaluate("window.effects || 0") == 0
        assert store.action("previous-denied-action") is None


async def test_p06_native_clarification_reviewer_uses_actor_ledger_and_cap(tmp_path):
    native = native_success(
        "clarification_review",
        {
            "classification": "action_approval",
            "reason": "Permission request only",
            "evidence": [],
        },
    )
    client, store, transport, _sleeps, events = paid_gateway(
        tmp_path, [success(), native], cap=220
    )
    await client.call({"input": "Actor request"})
    assert (
        await client.review_clarification("Task", {"question": "Permission?"}, {}, {})
    )["classification"] == "action_approval"
    assert store.budget("run")["settled"] == 74
    assert [data["purpose"] for kind, data in events if kind == "model_admitted"] == [
        "actor",
        "clarification_reviewer",
    ]
    with pytest.raises(BudgetExceeded):
        await client.review_clarification("Task", {"question": "Permission?"}, {}, {})
    assert len(transport.create_calls) == 2
