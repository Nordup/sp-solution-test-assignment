"""No paid API: durable admission, actual usage and aggregate case holds."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from browser_agent.context import build_input, memory_due, request
from browser_agent.storage import AdmissionError, BudgetExceeded, Store
from browser_agent.tools import ProtocolError, tool_specs


def test_p05_next_reservation_refused_before_dispatch(tmp_path):
    store = Store(tmp_path / "ledger.db")
    store.create_budget("run", 100)
    store.reserve("run", "actor", 90)
    dispatched = []
    with pytest.raises(BudgetExceeded):
        store.reserve("run", "reviewer", 11)
        dispatched.append("reviewer")
    assert not dispatched
    assert store.budget("run")["remaining"] == 10


def test_p06_helpers_retries_judge_share_persistent_cap(tmp_path):
    store = Store(tmp_path / "ledger.db")
    store.create_budget("run", 100)
    for stage in ("actor", "reviewer", "retry", "judge"):
        store.reserve("run", stage, 25)
        store.settle(stage, 20)
    assert store.budget("run")["settled"] == 80
    with pytest.raises(BudgetExceeded):
        store.reserve("run", "more", 21)


def test_p07_timeout_restart_checkpoint_cannot_refund(tmp_path):
    path = tmp_path / "ledger.db"
    store = Store(path)
    store.create_budget("run", 100)
    store.reserve("run", "timed-out", 75)
    store.mark_unknown("timed-out")
    restarted = Store(path)
    restarted.create_budget("run", 100)
    assert restarted.budget("run")["unknown"] == 75
    with pytest.raises(BudgetExceeded):
        restarted.reserve("run", "retry", 26)
    with pytest.raises(AdmissionError):
        restarted.reserve("run", "timed-out", 1)
    with pytest.raises(AdmissionError):
        restarted.create_budget("run", 101)


def test_aggregate_admission_is_atomic_and_survives_case_crash(tmp_path):
    store = Store(tmp_path / "ledger.db")
    store.create_budget("release", 100, "release")
    store.create_budget("experiment", 70, "experiment")
    store.reserve_case("run", 60, ["release", "experiment"])
    store.reserve("run", "actor", 30)
    store.settle("actor", 12)
    assert store.budget("release")["reserved"] == 60
    with pytest.raises(BudgetExceeded):
        store.reserve_case("run2", 20, ["release", "experiment"])
    assert store.budget("release")["reserved"] == 60
    store.finish_case("run")
    assert store.budget("release")["settled"] == 12
    assert store.budget("experiment")["remaining"] == 58


def test_concurrent_requests_cannot_overspend(tmp_path):
    store = Store(tmp_path / "ledger.db")
    store.create_budget("run", 100)

    def attempt(index):
        try:
            store.reserve("run", str(index), 60)
            return True
        except BudgetExceeded:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, range(2)))
    assert sum(results) == 1
    assert store.budget("run")["reserved"] == 60


def test_budget_disk_failure_prevents_dispatch(tmp_path):
    store = Store(tmp_path / "ledger.db")
    store.create_budget("run", 100)
    with sqlite3.connect(store.path) as db:
        db.execute(
            "CREATE TRIGGER fail_charge BEFORE INSERT ON charges BEGIN SELECT RAISE(FAIL, 'disk failure'); END"
        )
    dispatched = []
    with pytest.raises(sqlite3.DatabaseError):
        store.reserve("run", "actor", 50)
        dispatched.append(True)
    assert not dispatched
    assert store.budget("run")["remaining"] == 100


def test_actual_usage_is_immutable_and_overrun_remains_visible(tmp_path):
    store = Store(tmp_path / "ledger.db")
    store.create_budget("run", 100)
    store.reserve("run", "actor", 50)
    with pytest.raises(AdmissionError, match="exceeded"):
        store.settle("actor", 101)
    assert store.budget("run")["settled"] == 101
    with pytest.raises(BudgetExceeded):
        store.reserve("run", "next", 1)
    with pytest.raises(AdmissionError):
        store.settle("actor", 0)


@pytest.mark.parametrize("amount", [-1, True, 2.3])
def test_non_integer_or_negative_currency_rejected(tmp_path, amount):
    with pytest.raises(ValueError):
        Store(tmp_path / "ledger.db").create_budget("run", amount)


def test_user_cap_cannot_be_raised_by_configuration(tmp_path):
    with pytest.raises(ValueError, match="cap"):
        Store(tmp_path / "ledger.db").create_budget("run", 5_000_001)


def test_periodic_memory_is_forced_before_rolling_history_eviction():
    history = [
        [
            {
                "type": "function_call_output",
                "call_id": str(index),
                "output": "Original observed fact " + str(index),
            }
        ]
        for index in range(4)
    ]
    state = {
        "task": "Review the originally selected records",
        "steps": 4,
        "history": history,
    }
    assert memory_due(state)
    req = request(state, tool_specs())
    assert req["tool_choice"] == {"type": "function", "name": "remember"}
    assert [tool["name"] for tool in req["tools"]] == ["remember"]
    assert "Original observed fact 0" in json.dumps(req["input"])
    assert not memory_due(state | {"memory_step": 4})
    assert memory_due(state | {"memory_step": 4, "steps": 8})


def test_original_scope_and_progress_are_not_rolling_history():
    scope = {
        "boundary": "Original first two records",
        "items": [
            {
                "identity": "Record Alpha",
                "evidence_id": "obs-initial",
                "quote": "Record Alpha",
            }
        ],
    }
    state = {
        "task": "Review original selection",
        "scope": scope,
        "progress": [{"action_id": "first-action", "target": "Record Alpha"}],
        "history": [[{"role": "user", "content": "later"}]] * 20,
    }
    rendered = json.dumps(build_input(state))
    assert "Original first two records" in rendered
    assert "first-action" in rendered
    assert "Record Alpha" in rendered


QUEUE_HTML = """<h1>Record queue</h1><ul><li>Record Alpha</li><li>Record Beta</li><li>Record Gamma</li></ul><button onclick="window.effects=(window.effects||0)+1">Remove Record Gamma</button>"""


def collection_scope(obs):
    return {
        "boundary": "Only the original first two records, regardless of later queue changes",
        "items": [
            {"identity": label, "evidence_id": obs["id"], "quote": label}
            for label in ("Record Alpha", "Record Beta")
        ],
    }


def native_memory(
    scope, notes="Original records Alpha and Beta; Gamma remains outside the selection."
):
    return {
        "status": "completed",
        "output": [
            {
                "type": "function_call",
                "name": "remember",
                "call_id": "memory-native",
                "arguments": json.dumps({"notes": notes, "scope": scope}),
            }
        ],
    }


async def test_memory_scope_survives_old_sqlite_checkpoint_and_rejects_redefinition(
    tmp_path,
):
    from test_graph_resume import graph_case

    async with graph_case(tmp_path, html=QUEUE_HTML) as (
        _browser,
        gateway,
        _store,
        runtime,
        graph,
        config,
        initial,
        _events,
    ):
        observed = await runtime.observe(initial)
        state = initial | observed | {"steps": 4}
        await graph.aupdate_state(config, state, as_node="observe")
        old_checkpoint = await graph.aget_state(config)
        scope = collection_scope(observed["observation"])

        async def remember(req, purpose="actor"):
            assert purpose == "memory"
            return native_memory(scope)

        gateway.call = remember
        update = await runtime.decide(state)
        assert update["scope"] == scope
        assert runtime.restore_memory(old_checkpoint.values)["scope"] == scope
        assert (
            json.loads((runtime.run_dir / "memory.json").read_text())["scope"] == scope
        )
        replacement = {
            "boundary": "New current queue",
            "items": [
                {
                    "identity": "Record Gamma",
                    "evidence_id": observed["observation"]["id"],
                    "quote": "Record Gamma",
                }
            ],
        }
        with pytest.raises(ProtocolError, match="cannot be replaced"):
            runtime.validate_scope(replacement, runtime.restore_memory(state))
        assert runtime.restore_memory(state)["scope"] == scope


async def test_scope_quotes_must_exist_in_actual_delivered_observation(tmp_path):
    from test_graph_resume import graph_case

    async with graph_case(tmp_path, html=QUEUE_HTML) as (
        _browser,
        _gateway,
        _store,
        runtime,
        _graph,
        _config,
        initial,
        _events,
    ):
        state = initial | await runtime.observe(initial)
        scope = collection_scope(state["observation"])
        scope["items"][0]["quote"] = "Record Alpha has invented attributes not present"
        with pytest.raises(ProtocolError, match="absent"):
            runtime.validate_scope(scope, state)
        assert not (runtime.run_dir / "memory.json").exists()


async def test_scope_identity_cannot_relabel_an_actual_quote(tmp_path):
    from test_graph_resume import graph_case

    async with graph_case(tmp_path, html=QUEUE_HTML) as (
        _browser,
        _gateway,
        _store,
        runtime,
        _graph,
        _config,
        initial,
        _events,
    ):
        state = initial | await runtime.observe(initial)
        scope = collection_scope(state["observation"])
        scope["items"][0]["identity"] = "Record Gamma"
        with pytest.raises(ProtocolError, match="identity must appear"):
            runtime.validate_scope(scope, state)
        assert not (runtime.run_dir / "memory.json").exists()


async def test_ignoring_forced_memory_never_dispatches_browser_action(tmp_path):
    from test_graph_resume import choose_ref, graph_case

    async with graph_case(tmp_path, html=QUEUE_HTML) as (
        browser,
        gateway,
        store,
        runtime,
        _graph,
        _config,
        initial,
        _events,
    ):
        state = initial | await runtime.observe(initial) | {"steps": 4}
        ref = choose_ref(state["observation"], "Remove Record Gamma")

        async def disobedient(req, purpose="actor"):
            return {
                "status": "completed",
                "output": [
                    {
                        "type": "function_call",
                        "name": "click",
                        "call_id": "bad-memory",
                        "arguments": json.dumps({"ref": ref}),
                    }
                ],
            }

        gateway.call = disobedient
        update = await runtime.decide(state)
        assert update["route"] == "decide"
        assert "remember call is required" in update["feedback"]
        assert not store.unresolved_actions(initial["run_id"])
        assert await browser.page.evaluate("window.effects || 0") == 0


async def test_out_of_scope_effect_gets_no_approval_even_when_reviewer_calls_it_consequential(
    tmp_path,
):
    from test_graph_resume import choose_ref, graph_case

    async with graph_case(tmp_path, html=QUEUE_HTML) as (
        browser,
        gateway,
        store,
        runtime,
        _graph,
        _config,
        initial,
        _events,
    ):
        observed = await runtime.observe(initial)
        scope = collection_scope(observed["observation"])
        runtime.persist_memory(
            initial
            | {
                "scope": scope,
                "notes": "Retain Gamma; change only original Alpha and Beta.",
            }
        )
        ref = choose_ref(observed["observation"], "Remove Record Gamma")
        call = {"name": "click", "arguments": {"ref": ref}, "call_id": "outside-call"}
        state = (
            initial
            | observed
            | {
                "steps": 5,
                "memory_step": 4,
                "call": call,
                "action_id": "outside-action",
                "action": {"tool": "click", "args": {"ref": ref}},
            }
        )

        async def review(task, action, metadata):
            assert metadata["task_context"]["original_collection_scope"] == scope
            return {
                "classification": "consequential",
                "scope_status": "out_of_scope",
                "effect_summary": "Remove Record Gamma, which is outside the preserved selection.",
                "reason": "Excluded object",
            }

        gateway.review = review
        outcome = await runtime.policy(state)
        assert outcome["route"] == "recover"
        assert "out_of_scope" in outcome["feedback"]
        assert not outcome.get("approval_id")
        assert store.action("outside-action") is None
        assert await browser.page.evaluate("window.effects || 0") == 0


async def test_first_uncertain_consequence_records_memory_before_asking_for_scope(
    tmp_path,
):
    from test_graph_resume import choose_ref, graph_case

    async with graph_case(tmp_path, html=QUEUE_HTML) as (
        _browser,
        gateway,
        _store,
        runtime,
        _graph,
        _config,
        initial,
        _events,
    ):
        observed = await runtime.observe(initial)
        ref = choose_ref(observed["observation"], "Remove Record Gamma")
        call = {"name": "click", "arguments": {"ref": ref}, "call_id": "first-call"}
        state = (
            initial
            | observed
            | {
                "steps": 1,
                "call": call,
                "action_id": "first-effect",
                "action": {"tool": "click", "args": {"ref": ref}},
            }
        )

        async def review(*args):
            return {
                "classification": "consequential",
                "scope_status": "uncertain",
                "effect_summary": "Actual scope not established",
                "reason": "Read original collection first",
            }

        gateway.review = review
        outcome = await runtime.policy(state)
        assert outcome["route"] == "decide"
        assert outcome["memory_required"]
        assert outcome["pending_call"] == call
        assert not outcome.get("approval_id")


def completion_transport(runtime, run_id, counts, *, outcome=None, delay=0):
    """Actual native Gateway, deterministic token-count/response transport."""
    import asyncio
    from types import SimpleNamespace

    from test_provider import Transport, native_success

    from browser_agent.llm import Gateway

    class CountingTransport(Transport):
        async def count(self, **kwargs):
            self.count_calls.append(kwargs)
            if delay:
                await asyncio.sleep(delay)
            return SimpleNamespace(input_tokens=next(self.scripted_counts))

    response = outcome or native_success(
        "completion_review",
        {
            "supported": True,
            "next_visible_action": {
                "kind": "not_applicable",
                "effect": "No explicit stop-before effect in this synthetic goal.",
                "evidence": [],
            },
            "boundary_status": "not_applicable",
            "remaining_permitted_steps": [],
            "reason": "Synthetic source-supported completed outcome.",
        },
    )
    transport = CountingTransport([response])
    transport.scripted_counts = iter(counts)
    runtime.gateway = Gateway(
        runtime.settings, runtime.store, run_id, client=transport, emit=runtime.emit
    )

    # These tests isolate endpoint request repacking; factual Gateway admission
    # and failure/ledger behavior have their own native transport regressions.
    async def report_pass(*_args):
        return {"issues": [], "supported": True, "reason": "Synthetic factual check."}

    runtime.gateway.verify_report = report_pass
    return transport


def completed_state(initial, observed, quote="Observed result"):
    proposal = {
        "status": "completed",
        "summary": "Requested result observed.",
        "claims": [
            {
                "claim": "The requested result is visible.",
                "evidence_id": observed["observation"]["id"],
                "quote": quote,
            }
        ],
        "remaining": [],
    }
    return (
        initial
        | observed
        | {
            "result": proposal,
            "call": {
                "name": "finish",
                "arguments": proposal,
                "call_id": "synthetic-completion-call",
            },
        }
    )


async def test_completion_overflow_deduplicates_only_exact_included_journal_pairs(
    tmp_path,
):
    from test_graph_resume import approval_answer, click, graph_case

    async with graph_case(tmp_path, script=[click("Send application")]) as (
        browser,
        _gateway,
        store,
        runtime,
        graph,
        config,
        initial,
        events,
    ):
        waiting = await graph.ainvoke(initial, config)
        state = await graph.ainvoke(approval_answer(waiting), config)
        state = runtime.restore_memory(state)
        journal = runtime.action_journal(state)
        matched = state["progress"][0]
        unmatched = [
            matched | {"action_id": "not-in-included-journal"},
            matched | {"evidence_id": state["evidence_ids"][0]},
        ]
        state |= {
            "progress": state["progress"] + unmatched,
            "notes": "Keep all original notes intact.",
            "clarifications": ["Do not send a second time."],
            "scope_obligations": [
                {
                    "id": "preserved-choice",
                    "status": "resolved",
                    "description": "The originally selected recipient remains Acme.",
                }
            ],
        }
        runtime.persist_memory(state)
        # Simulate records excluded from the bounded journal; unmatched receipts
        # must survive even when only one of their identifying fields matches.
        runtime.action_journal = lambda _state: journal | {"omitted_count": 2}
        state = completed_state(
            state, {"observation": state["observation"]}, "Application recorded"
        )
        transport = completion_transport(runtime, initial["run_id"], [20206, 19500])
        result = await runtime.finalize(state)
        assert result["result"]["status"] == "completed"
        assert len(transport.count_calls) == 2 and len(transport.create_calls) == 1
        before, after = [json.loads(item["input"]) for item in transport.count_calls]
        assert before["task"] == after["task"] == state["task"]
        for key, value in state["result"].items():
            assert before["proposal"][key] == after["proposal"][key] == value
        for key, value in before["proposal"]["task_context"].items():
            if key != "action_receipts":
                assert after["proposal"]["task_context"][key] == value
        assert after["proposal"]["task_context"]["action_receipts"] == unmatched
        assert (
            before["proposal"]["action_journal"] == after["proposal"]["action_journal"]
        )
        assert (
            before["proposal"]["evidence_manifest"]
            == after["proposal"]["evidence_manifest"]
        )
        assert before["evidence"] == after["evidence"]
        assert transport.create_calls[0]["input"] == transport.count_calls[-1]["input"]
        assert store.budget(initial["run_id"])["settled"] == 37
        assert store.budget(initial["run_id"])["unknown"] == 0
        assert await browser.page.evaluate("window.effects") == 1
        assert (
            len([name for name, _ in events if name == "completion_context_adaptation"])
            == 1
        )


async def test_completion_third_admission_reduces_whole_sources_with_explicit_omissions(
    tmp_path,
):
    from test_graph_resume import graph_case

    async with graph_case(tmp_path, html="<p>Observed result</p>") as (
        browser,
        _gateway,
        _store,
        runtime,
        _graph,
        _config,
        initial,
        _events,
    ):
        state = initial | await runtime.observe(initial)
        result_source = state["observation"]
        for index in range(6):
            await browser.page.set_content(
                f"<p>Supporting page {index}: " + "actual evidence " * 330 + "</p>"
            )
            state |= await runtime.observe(state)
        state = completed_state(state, {"observation": result_source})
        transport = completion_transport(
            runtime, initial["run_id"], [22000, 21000, 19000]
        )
        result = await runtime.finalize(state)
        assert result["result"]["status"] == "completed"
        assert len(transport.count_calls) == 3 and len(transport.create_calls) == 1
        packets = [json.loads(item["input"]) for item in transport.count_calls]
        assert packets[0]["evidence"] == packets[1]["evidence"]
        last = packets[-1]
        assert last["proposal"]["evidence_manifest"]["byte_limit"] == 24000
        assert last["proposal"]["evidence_manifest"]["omitted"]
        assert len(last["evidence"]) < len(packets[1]["evidence"])
        assert result_source["id"] in last["evidence"]
        assert all(
            text
            == json.loads((runtime.run_dir / "evidence" / f"{key}.json").read_text())[
                "text"
            ]
            for key, text in last["evidence"].items()
        )
        assert all(
            packet["proposal"]["claims"] == state["result"]["claims"]
            for packet in packets
        )
        assert transport.create_calls[0]["input"] == transport.count_calls[-1]["input"]


async def test_completion_admission_exhaustion_never_generates_or_spends(tmp_path):
    from test_graph_resume import graph_case

    async with graph_case(tmp_path, html="<p>Observed result</p>") as (
        _browser,
        _gateway,
        store,
        runtime,
        _graph,
        _config,
        initial,
        _events,
    ):
        state = completed_state(initial, await runtime.observe(initial))
        transport = completion_transport(runtime, initial["run_id"], [20001] * 3)
        result = await runtime.finalize(state)
        assert result["result"]["status"] == "partial"
        assert len(transport.count_calls) == 3 and not transport.create_calls
        assert store.budget(initial["run_id"])["settled"] == 0
        assert store.budget(initial["run_id"])["reserved"] == 0
        assert result["result"]["summary"].startswith("Completion was not verified:")


async def test_completion_recount_checks_active_time_before_next_attempt(tmp_path):
    from test_graph_resume import graph_case

    async with graph_case(tmp_path, html="<p>Observed result</p>") as (
        _browser,
        _gateway,
        store,
        runtime,
        _graph,
        _config,
        initial,
        _events,
    ):
        state = completed_state(initial, await runtime.observe(initial))
        runtime.settings.active_seconds = 1
        transport = completion_transport(
            runtime, initial["run_id"], [20206, 19500], delay=1.05
        )
        result = await runtime.finalize(state)
        assert result["result"]["status"] == "partial"
        assert len(transport.count_calls) == 1 and not transport.create_calls
        assert "Active-time limit" in result["result"]["summary"]
        assert store.budget(initial["run_id"])["settled"] == 0


@pytest.mark.parametrize("failure", ["semantic", "provider"])
async def test_completion_semantic_or_provider_failure_does_not_repack(
    tmp_path, failure
):
    from test_graph_resume import graph_case
    from test_provider import error, native_success

    async with graph_case(tmp_path, html="<p>Observed result</p>") as (
        _browser,
        _gateway,
        _store,
        runtime,
        _graph,
        _config,
        initial,
        events,
    ):
        state = completed_state(initial, await runtime.observe(initial))
        response = (
            error(401)
            if failure == "provider"
            else native_success(
                "completion_review",
                {
                    "supported": False,
                    "next_visible_action": {
                        "kind": "not_applicable",
                        "effect": "No explicit stop-before effect in this synthetic goal.",
                        "evidence": [],
                    },
                    "boundary_status": "not_applicable",
                    "remaining_permitted_steps": [],
                    "reason": "More actual task evidence is required.",
                },
            )
        )
        transport = completion_transport(
            runtime, initial["run_id"], [19500], outcome=response
        )
        result = await runtime.finalize(state)
        assert len(transport.count_calls) == len(transport.create_calls) == 1
        assert not any(name == "completion_context_adaptation" for name, _ in events)
        assert result["route"] == ("done" if failure == "provider" else "decide")
