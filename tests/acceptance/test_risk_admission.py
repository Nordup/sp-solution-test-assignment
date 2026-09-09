"""Native token admission with real graph/browser/ledger; no paid calls."""

import asyncio
import copy
import json
from types import SimpleNamespace

import pytest
from test_graph_resume import approval_answer, choose_ref, click, graph_case, pending
from test_provider import Transport, error, native_success

from browser_agent.llm import Gateway


def verdict(**changes):
    return native_success(
        "risk_review",
        {
            "classification": "consequential",
            "scope_status": "in_scope",
            "new_obligations": [],
            "scope_resolutions": [],
            "unaffected_obligation_ids": [],
            "effect_summary": "Submit the displayed application.",
            "reason": "Synthetic grounded review.",
        } | changes,
    )


def risk_transport(runtime, actor, run_id, counts, outcomes=None, delay=0):
    class Counting(Transport):
        async def count(self, **kwargs):
            self.count_calls.append(copy.deepcopy(kwargs))
            if delay:
                await asyncio.sleep(delay)
            return SimpleNamespace(input_tokens=next(self.counts))

    transport = Counting(outcomes or [verdict()])
    transport.counts = iter(counts)
    client = Gateway(runtime.settings, runtime.store, run_id, client=transport, emit=runtime.emit)
    actor.review = client.review
    return transport


async def policy_state(runtime, initial):
    state = initial | await runtime.observe(initial)
    state.update(
        action={"tool": "click", "args": {"ref": choose_ref(state["observation"], "Send application")}},
        action_id="not-dispatched",
        memory_step=1,
        notes="Preserve these complete working notes.",
        clarifications=["Use the displayed letter; do not change its qualifications."],
        progress=[{"action_id": "earlier", "evidence_id": "old-result", "status": "executed"}],
    )
    state["call"] = {"name": "click", "call_id": "proposed-call", "arguments": state["action"]["args"]}
    return state


def payloads(transport):
    return [json.loads(item["input"]) for item in transport.count_calls]


async def test_risk_exact_overflow_removes_only_copy_aid_then_approval_resumes_once(tmp_path):
    async with graph_case(tmp_path, script=[click("Send application")]) as (
        browser, actor, store, runtime, graph, config, initial, events
    ):
        initial["memory_step"] = 1
        transport = risk_transport(runtime, actor, initial["run_id"], [20025, 19000])
        paused = await graph.ainvoke(initial, config)
        assert pending(paused)["kind"] == "approval"
        assert len(transport.count_calls) == 2 and len(transport.create_calls) == 1
        first, second = payloads(transport)
        assert first["task"] == second["task"] and first["action"] == second["action"]
        metadata = first["resolved_browser_evidence"]
        assert metadata["scope_quote_candidates"]
        assert second["resolved_browser_evidence"] == {k: v for k, v in metadata.items() if k != "scope_quote_candidates"}
        assert transport.create_calls[0]["input"] == transport.count_calls[1]["input"]
        assert await browser.page.evaluate("window.effects||0") == 0
        assert store.actions_for_run(initial["run_id"]) == []
        result = await graph.ainvoke(approval_answer(paused), config)
        assert result["result"]["status"] == "partial"  # scripted actor's truthful stop
        assert await browser.page.evaluate("window.effects||0") == 1
        assert len(transport.count_calls) == 2
        assert len(store.actions_for_run(initial["run_id"])) == 1
        admissions = [data for name, data in events if name == "model_admitted"]
        assert len(admissions) == 1 and admissions[0]["input_tokens"] == 19000
        assert store.budget(initial["run_id"])["settled"] == 37


async def test_risk_third_variant_preserves_constraints_and_marks_whole_source_omissions(tmp_path):
    async with graph_case(tmp_path) as (_browser, actor, _store, runtime, _graph, _config, initial, _events):
        state = await policy_state(runtime, initial)
        for index in range(7):
            key = f"archive-{index}"
            runtime.save_observation({"id": key, "url": f"https://example.test/{index}", "title": key, "text": "Actual archived body. " * 250})
            state["evidence_ids"].append(key)
        state["scope"] = {"boundary": "Original collection", "items": [{"identity": "Original item", "evidence_id": "archive-0", "quote": "Actual archived body."}]}
        state["scope_obligations"] = [{"id": "resolved", "status": "closed", "description": "Preserve resolved decision", "evidence": []}]
        transport = risk_transport(runtime, actor, initial["run_id"], [21000, 20001, 19000])
        result = await runtime.policy(state)
        assert result["route"] == "approval"
        packets = payloads(transport)
        original = packets[0]["resolved_browser_evidence"]
        final = packets[2]["resolved_browser_evidence"]
        assert packets[0]["task"] == packets[2]["task"]
        assert packets[0]["action"] == packets[2]["action"]
        assert {k: v for k, v in original.items() if k not in {"scope_quote_candidates", "scope_sources", "scope_evidence_manifest"}} == {k: v for k, v in final.items() if k not in {"scope_sources", "scope_evidence_manifest"}}
        assert final["task_context"]["action_receipts"] == state["progress"]
        assert final["scope_evidence_manifest"]["byte_limit"] == 24000
        assert len(final["scope_sources"]) < len(original["scope_sources"])
        assert all(value == original["scope_sources"][key] for key, value in final["scope_sources"].items())
        removed = set(original["scope_sources"]) - set(final["scope_sources"])
        assert removed <= {item["evidence_id"] for item in final["scope_evidence_manifest"]["omitted"]}
        assert state["observation"]["id"] in final["scope_sources"] and "archive-0" in final["scope_sources"]
        assert len(transport.create_calls) == 1


async def test_risk_all_admissions_rejected_returns_partial_without_paid_call_or_effect(tmp_path):
    async with graph_case(tmp_path) as (browser, actor, store, runtime, _graph, _config, initial, events):
        state = await policy_state(runtime, initial)
        transport = risk_transport(runtime, actor, initial["run_id"], [21000, 20500, 20001])
        result = await runtime.policy(state)
        assert result["route"] == "finalize" and result["status"] == "partial"
        assert len(transport.count_calls) == 3 and not transport.create_calls
        assert store.budget(initial["run_id"])["settled"] == 0
        assert store.budget(initial["run_id"])["reserved"] == 0
        assert not store.actions_for_run(initial["run_id"])
        assert await browser.page.evaluate("window.effects||0") == 0
        assert len([1 for name, _ in events if name == "risk_context_adaptation"]) == 3


@pytest.mark.parametrize("failure", ["provider", "budget", "semantic"])
async def test_risk_nonadmission_failure_does_not_restart_repacking(tmp_path, failure):
    async with graph_case(tmp_path) as (browser, actor, store, runtime, _graph, _config, initial, events):
        state = await policy_state(runtime, initial)
        outcome = error(401) if failure == "provider" else verdict(classification="forbidden", scope_status="out_of_scope")
        transport = risk_transport(runtime, actor, initial["run_id"], [20025, 19000], [outcome])
        if failure == "budget":
            # Deplete this isolated test ledger without making a network call.
            store.reserve(initial["run_id"], "existing-hold", 5_000_000)
        result = await runtime.policy(state)
        assert result["route"] in {"finalize", "recover"}
        assert len(transport.count_calls) == 2
        assert len(transport.create_calls) == int(failure != "budget")
        assert len([1 for name, _ in events if name == "risk_context_adaptation"]) == 1
        assert not store.actions_for_run(initial["run_id"])
        assert await browser.page.evaluate("window.effects||0") == 0


async def test_risk_adaptation_honors_active_time_before_recount(tmp_path):
    async with graph_case(tmp_path) as (_browser, actor, store, runtime, _graph, _config, initial, _events):
        state = await policy_state(runtime, initial)
        runtime.settings.active_seconds = 1
        transport = risk_transport(runtime, actor, initial["run_id"], [20025, 19000], delay=1.05)
        result = await runtime.policy(state)
        assert result["status"] == "partial" and "Active-time" in result["feedback"]
        assert len(transport.count_calls) == 1 and not transport.create_calls
        assert store.budget(initial["run_id"])["settled"] == 0


async def test_risk_semantic_repair_keeps_feedback_and_does_not_reset_admission_variant(tmp_path):
    async with graph_case(tmp_path) as (_browser, actor, store, runtime, _graph, _config, initial, events):
        state = await policy_state(runtime, initial)
        invalid = verdict(new_obligations=[{
            "affects_collection_selection": True,
            "description": "A choice asserted with an invented source.",
            "evidence": [{"source_id": "invented", "quote": "not observed"}],
        }])
        transport = risk_transport(runtime, actor, initial["run_id"], [20025, 19000, 20025, 19000], [invalid, verdict()])
        result = await runtime.policy(state)
        assert result["route"] == "approval" and result["scope_review_repairs"] == 1
        packets = [item["resolved_browser_evidence"] for item in payloads(transport)]
        assert "scope_quote_candidates" in packets[0]
        assert all("scope_quote_candidates" not in packet for packet in packets[1:])
        assert packets[2]["scope_review_feedback"] == packets[3]["scope_review_feedback"]
        assert packets[2]["scope_review_feedback"]["errors"]
        assert packets[2]["task_context"] == packets[3]["task_context"]
        assert packets[3]["scope_evidence_manifest"]["byte_limit"] == 24000
        adaptations = [data for name, data in events if name == "risk_context_adaptation"]
        assert [data["attempt"] for data in adaptations] == [1, 2]
        assert len(transport.create_calls) == 2
        assert store.budget(initial["run_id"])["settled"] == 74
        assert not store.actions_for_run(initial["run_id"])
