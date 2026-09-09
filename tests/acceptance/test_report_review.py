"""Real graph/browser/ledger boundaries; scripted verdicts do not prove model quality."""

import json
from types import SimpleNamespace

import pytest
from test_graph_resume import FORM, approval_answer, click, graph_case, pending
from test_provider import error, gateway, native_success

from browser_agent.context import ContextOverflow
from browser_agent.graph import AgentGraph
from browser_agent.llm import ProviderFailure
from browser_agent.storage import BudgetExceeded
from browser_agent.tools import ProtocolError


def finish(summary, quote="Application recorded"):
    return lambda obs: (
        "finish",
        {
            "status": "completed",
            "summary": summary,
            "claims": [{"claim": summary, "evidence_id": obs["id"], "quote": quote}],
            "remaining": [],
        },
    )


@pytest.mark.parametrize("preexisting", [False, True])
@pytest.mark.parametrize("accurate", [False, True])
async def test_actual_dispatch_provenance_and_prior_state_reports_are_distinct(
    tmp_path, preexisting, accurate
):
    correct = (
        "The result preexisted; I submitted nothing."
        if preexisting
        else "I submitted exactly once in this run."
    )
    wrong = (
        "I submitted this result in this run."
        if preexisting
        else "This result existed before this run."
    )
    html = (
        FORM.replace("<output></output>", "<output>Application recorded</output>")
        if preexisting
        else FORM
    )
    report = finish(correct if accurate else wrong)
    script = ([] if preexisting else [click("Send application")]) + [report] * (
        1 if accurate else 3
    )
    async with graph_case(tmp_path, html, script) as (
        browser,
        actor,
        store,
        _runtime,
        graph,
        config,
        initial,
        events,
    ):
        packets = []

        async def audit(task, proposal, evidence):
            packets.append(proposal)
            ledger = proposal["current_run_dispatches"]
            assert ledger["complete_dispatch_inventory"] and not ledger["omitted_count"]
            assert ledger["dispatch_count"] == int(not preexisting)
            assert ledger["approved_dispatch_count"] == int(not preexisting)
            assert proposal["report"]["summary"] == (correct if accurate else wrong)
            assert "working_notes" not in proposal and "notes" not in proposal
            assert "Application recorded" in "\n".join(evidence.values())
            if not preexisting:
                item = ledger["records"][0]
                row = store.action(item["action_id"])
                assert item["approval_id"] == row["approval_id"]
                assert item["created"] == row["created"]
                assert item["operation"] == "form_change"
                assert "Send application" in evidence[item["source_evidence_id"]]
                assert "Application recorded" in evidence[item["result_evidence_id"]]
                assert (
                    item["executor_resolved_effect"]["target"]["name"]
                    == "Send application"
                )
            return {
                "issues": []
                if accurate
                else ["Report contradicts the actual dispatch history."],
                "supported": accurate,
                "reason": "Scripted factual attribution decision.",
            }

        actor.verify_report = audit
        result = await graph.ainvoke(initial, config)
        if not preexisting:
            assert pending(result)["kind"] == "approval"
            result = await graph.ainvoke(approval_answer(result), config)
        assert result["result"]["status"] == ("completed" if accurate else "partial")
        assert len(packets) == (1 if accurate else 3)
        assert await browser.page.evaluate("window.effects||0") == int(not preexisting)
        assert sum(name == "completion_repair" for name, _ in events) == (
            0 if accurate else 2
        )


async def test_factual_rejection_repairs_report_without_repeating_approved_mutation(
    tmp_path,
):
    async with graph_case(
        tmp_path,
        script=[
            click("Send application"),
            finish("It preexisted."),
            finish("I submitted once."),
        ],
    ) as (browser, actor, _store, _runtime, graph, config, initial, _events):
        calls = []

        async def audit(task, proposal, evidence):
            calls.append(proposal["report"]["summary"])
            return {
                "issues": ["Incorrect attribution"] if len(calls) == 1 else [],
                "supported": True,
                "reason": "Issues veto supported=True too.",
            }

        actor.verify_report = audit
        paused = await graph.ainvoke(initial, config)
        result = await graph.ainvoke(approval_answer(paused), config)
        assert result["result"]["status"] == "completed"
        assert calls == ["It preexisted.", "I submitted once."]
        assert await browser.page.evaluate("window.effects") == 1


async def test_endpoint_rejection_skips_factual_audit(tmp_path):
    async with graph_case(
        tmp_path, html="<h1>Observed</h1>", script=[finish("Done", "Observed")] * 3
    ) as (_browser, actor, _store, _runtime, graph, config, initial, _events):
        original = actor.verify_completion

        async def reject(*args):
            return await original(*args) | {
                "supported": False,
                "reason": "Endpoint unmet.",
            }

        actor.verify_completion = reject
        result = await graph.ainvoke(initial, config)
        assert result["result"]["status"] == "partial"
        assert actor.report_reviews == 0


@pytest.mark.parametrize(
    "failure",
    [
        None,
        {},
        {"supported": True, "issues": [], "reason": ""},
        ProviderFailure("Unavailable"),
        BudgetExceeded("No allowance"),
    ],
)
async def test_missing_malformed_or_unavailable_factual_verdict_never_completes(
    tmp_path, failure
):
    async with graph_case(
        tmp_path, html="<h1>Observed</h1>", script=[finish("Done", "Observed")]
    ) as (_browser, actor, _store, _runtime, graph, config, initial, _events):

        async def unavailable(*args):
            if isinstance(failure, Exception):
                raise failure
            return failure

        actor.verify_report = unavailable
        result = await graph.ainvoke(initial, config)
        assert result["result"]["status"] == "partial"
        assert result["result"]["claims"] == []
        assert actor.calls == 1


async def test_factual_packet_never_discards_report_or_ledger_to_fit(tmp_path):
    async with graph_case(tmp_path, html="<h1>Observed</h1>") as (
        _browser,
        _actor,
        store,
        runtime,
        _graph,
        _config,
        initial,
        _events,
    ):
        observed = await runtime.observe(initial)
        proposal = finish("x" * 32000, "Observed")(observed["observation"])[1]
        with pytest.raises(ContextOverflow, match="nothing was silently omitted"):
            runtime.report_packet(initial | observed, proposal)
        actual = store.actions_for_run
        store.actions_for_run = lambda _run: [{}] * 241
        with pytest.raises(ContextOverflow, match="no omitted action"):
            runtime.report_packet(initial | observed, proposal)
        store.actions_for_run = actual


async def test_legacy_ambiguous_source_is_explicit_and_actor_notes_cannot_fill_it(
    tmp_path,
):
    html = FORM.replace(
        "'Application recorded'",
        "'Application recorded';this.querySelector('button').remove()",
    )
    async with graph_case(tmp_path, html=html, script=[click("Send application")]) as (
        _browser,
        _actor,
        _store,
        runtime,
        graph,
        config,
        initial,
        _events,
    ):
        paused = await graph.ainvoke(initial, config)
        state = runtime.restore_memory(
            await graph.ainvoke(approval_answer(paused), config)
        )
        source = state["progress"][0].pop("source_evidence_id")
        proposal = finish("Recorded once.")(state["observation"])[1]
        unique, _evidence, _manifest = runtime.report_packet(state, proposal)
        assert (
            unique["current_run_dispatches"]["records"][0]["source_evidence_id"]
            == source
        )
        original = json.loads(
            (runtime.run_dir / "evidence" / f"{source}.json").read_text()
        )
        duplicate_id = "obs-ambiguous-copy"
        (runtime.run_dir / "evidence" / f"{duplicate_id}.json").write_text(
            json.dumps(original | {"id": duplicate_id})
        )
        state["evidence_ids"].append(duplicate_id)
        state["notes"] = "Actor claims source evidence is " + source
        proposal = finish("Recorded once.")(state["observation"])[1]
        packet, _evidence, _manifest = runtime.report_packet(state, proposal)
        assert (
            packet["current_run_dispatches"]["records"][0]["source_evidence_id"] is None
        )
        assert "Actor claims" not in json.dumps(packet)


@pytest.mark.parametrize("details", ["not JSON", "{}", '{"action": [], "effect": {}}'])
async def test_corrupt_dispatch_details_produce_controlled_partial_result(
    tmp_path, details
):
    async with graph_case(tmp_path, script=[click("Send application")]) as (
        _browser,
        _actor,
        store,
        runtime,
        graph,
        config,
        initial,
        _events,
    ):
        waiting = await graph.ainvoke(initial, config)
        state = await graph.ainvoke(approval_answer(waiting), config)
        original = store.actions_for_run
        store.actions_for_run = lambda run: [
            row | {"details": details} for row in original(run)
        ]
        state["result"] = finish("Recorded once.")(state["observation"])[1]
        result = await runtime.finalize(state)
        assert result["result"]["status"] == "partial"
        assert "malformed" in result["result"]["summary"]


async def test_factual_input_repacking_is_bounded_and_keeps_full_report(tmp_path):
    async with graph_case(
        tmp_path, html="<h1>Observed</h1>", script=[finish("Exact report", "Observed")]
    ) as (_browser, actor, _store, _runtime, graph, config, initial, events):
        calls = []

        async def too_large(task, proposal, evidence):
            calls.append(proposal)
            raise ContextOverflow("Exact request exceeds 20000 tokens")

        actor.verify_report = too_large
        result = await graph.ainvoke(initial, config)
        assert result["result"]["status"] == "partial"
        assert len(calls) == 2
        assert calls[0]["report"] == calls[1]["report"]
        assert calls[0]["current_run_dispatches"] == calls[1]["current_run_dispatches"]
        assert [
            data["packet_byte_limit"]
            for name, data in events
            if name == "report_context_adaptation"
        ] == [32000, 24000]


async def test_native_report_review_retry_has_same_ledger_and_completion_effort(tmp_path):
    verdict = native_success(
        "report_review",
        {"issues": [], "supported": True, "reason": "Exact report supported."},
    )
    client, store, transport, sleeps, events = gateway(tmp_path, [error(), verdict])
    result = await client.verify_report(
        "Task", {"report": {"summary": "Exact"}}, {"obs": "Exact"}
    )
    assert result["supported"]
    assert len(transport.create_calls) == 2 and len(sleeps) == 1
    assert all(
        item["reasoning"] == {"effort": "medium"}
        for item in transport.count_calls + transport.create_calls
    )
    assert all(
        item["purpose"] == "report_reviewer"
        for name, item in events
        if name == "model_admitted"
    )
    assert store.budget("run")["settled"] == 37 and store.budget("run")["unknown"] > 0
    assert transport.create_calls[0]["tools"][0]["name"] == "report_review"
    assert len(transport.create_calls[0]["tools"]) == 1


async def test_native_report_review_budget_and_schema_fail_closed(tmp_path):
    verdict = native_success(
        "report_review", {"supported": True, "reason": "Missing issues field"}
    )
    client, store, _transport, _sleeps, _events = gateway(tmp_path, [verdict])
    with pytest.raises(ProtocolError):
        await client.verify_report("Task", {}, {})
    assert store.budget("run")["settled"] == 37
    client, _store, limited, _sleeps, _events = gateway(
        tmp_path / "limited", [verdict], cap=178
    )
    with pytest.raises(BudgetExceeded):
        await client.verify_report("Task", {}, {})
    assert limited.create_calls == []


def test_factual_archive_covers_distinct_pages_before_intermediate_revisits(tmp_path):
    runtime = AgentGraph.__new__(AgentGraph)
    runtime.run_dir = tmp_path
    (tmp_path / "evidence").mkdir()
    snapshots = []

    def observed(key, url, text):
        observation = {
            "id": key, "url": url, "title": key, "text": text,
            "saved_at_unix": len(snapshots),
        }
        snapshots.append(observation)
        (tmp_path / "evidence" / f"{key}.json").write_text(json.dumps(observation))
        return key

    observed("scope", "https://example.test/list", "Original selected collection")
    for index in range(10):
        observed(f"read-{index}", f"https://example.test/item/{index}", "Actual item body " * 10)
    rows, receipts = [], []
    for index in range(3):
        result_id = observed(f"result-{index}", f"https://example.test/return/{index}", "Repeated list page " * 200)
        rows.append({
            "id": f"action-{index}", "run_id": "run", "created": index,
            "status": "executed", "approval_id": f"approval-{index}",
            "details": json.dumps({"action": {"name": "click", "args": {}},
                                   "effect": {"operation": "form_change", "destination": "https://example.test"}}),
        })
        receipts.append({"action_id": f"action-{index}", "source_evidence_id": f"read-{index}", "evidence_id": result_id})
    observed("current", "https://example.test/done", "Three changes recorded")
    runtime.store = SimpleNamespace(actions_for_run=lambda _run: rows)
    state = {
        "run_id": "run", "observation": snapshots[-1],
        "evidence_ids": [item["id"] for item in snapshots],
        "progress": receipts,
        "scope": {"items": [{"evidence_id": "scope", "identity": "Selected", "quote": "selected"}]},
    }
    result = {"status": "completed", "summary": "Inspected ten pages and changed three.",
              "claims": [{"evidence_id": "current", "quote": "Three changes recorded", "claim": "Three changes recorded"}], "remaining": []}
    proposal, evidence, manifest = runtime.report_packet(state, result, max_bytes=10000)
    assert {"current", "scope"} | {f"read-{index}" for index in range(10)} <= evidence.keys()
    assert all(f"result-{index}" not in evidence for index in range(3))
    records = proposal["current_run_dispatches"]["records"]
    assert [item["result_evidence_id"] for item in records] == [f"result-{index}" for index in range(3)]
    assert {f"result-{index}" for index in range(3)} <= {item["evidence_id"] for item in manifest["omitted"]}
    assert proposal["report"] == result
    assert len(json.dumps({"proposal": proposal, "evidence": evidence}, ensure_ascii=False).encode()) <= 10000
