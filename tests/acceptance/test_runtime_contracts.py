"""Graph-level protocol and effect gates, beyond isolated unit tests."""

import pytest
from test_graph_resume import approval_answer, choose_ref, graph_case, pending

from browser_agent.storage import BudgetExceeded


@pytest.mark.parametrize(
    "response",
    [
        {"status": "incomplete", "output": []},
        {
            "status": "completed",
            "output": [
                {
                    "type": "function_call",
                    "name": "click",
                    "call_id": "bad",
                    "arguments": '{"ref":"e1","safe":true}',
                }
            ],
        },
        {
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "refusal", "refusal": "Cannot"}],
                }
            ],
        },
    ],
)
async def test_p01_p02_f08_invalid_native_output_repairs_are_bounded_without_effects(
    tmp_path, response
):
    async with graph_case(tmp_path) as (
        browser,
        gateway,
        store,
        _runtime,
        graph,
        config,
        initial,
        _events,
    ):
        calls = []

        async def invalid(req):
            calls.append(req)
            return response

        gateway.call = invalid
        result = await graph.ainvoke(initial, config)
        assert len(calls) == 3
        assert result["result"]["status"] == "failed"
        assert (
            "schema" in result["result"]["summary"]
            or "Native" in result["result"]["summary"]
            or "refused" in result["result"]["summary"]
        )
        assert await browser.page.evaluate("window.effects || 0") == 0
        assert not store.unresolved_actions(initial["run_id"])
        assert gateway.reviews == 0


async def test_p05_f11_budget_stop_uses_saved_facts_without_final_paid_call(tmp_path):
    async with graph_case(tmp_path) as (
        browser,
        gateway,
        _store,
        _runtime,
        graph,
        config,
        initial,
        _events,
    ):
        calls = []

        async def exhausted(req):
            calls.append(req)
            raise BudgetExceeded("Remaining allowance cannot fund request")

        gateway.call = exhausted
        result = await graph.ainvoke(initial, config)
        assert len(calls) == 1 and gateway.completion_reviews == 0
        assert result["result"]["status"] == "budget_exhausted"
        assert await browser.page.evaluate("window.effects || 0") == 0


async def test_f14_autosave_fill_is_approved_before_the_effect(tmp_path):
    html = '<h1>Profile editor</h1><form action="https://fixture.test/profile" method="post"><label>Public biography<textarea oninput="window.effects=(window.effects||0)+1" aria-label="Public biography"></textarea></label></form>'

    def fill(obs):
        return (
            "fill",
            {
                "ref": choose_ref(obs, "Public biography"),
                "value": "New public biography",
            },
        )

    async with graph_case(tmp_path, html, [fill]) as (
        browser,
        _gateway,
        _store,
        _runtime,
        graph,
        config,
        initial,
        _events,
    ):
        paused = await graph.ainvoke(initial, config)
        assert pending(paused)["kind"] == "approval"
        assert await browser.page.evaluate("window.effects || 0") == 0
        result = await graph.ainvoke(approval_answer(paused, False), config)
        assert result["result"]["status"] == "partial"
        assert await browser.page.get_by_role("textbox").input_value() == ""
        assert await browser.page.evaluate("window.effects || 0") == 0


async def test_p12_historical_checkpoint_does_not_rewind_money(tmp_path):
    async with graph_case(tmp_path) as (
        _browser,
        _gateway,
        store,
        _runtime,
        graph,
        config,
        initial,
        _events,
    ):
        # A real completed graph checkpoint is rewound after independent durable usage.
        result = await graph.ainvoke(initial, config)
        saved = await graph.aget_state(config)
        store.reserve(initial["run_id"], "completed-helper", 5000)
        store.settle("completed-helper", 2500)
        await graph.aupdate_state(
            saved.config, {"notes": "Old checkpoint state"}, as_node="ask"
        )
        await graph.ainvoke(None, config)
        assert store.budget(initial["run_id"])["settled"] == 2500
        assert result["result"]["status"] == "partial"


async def test_observed_select_label_resolves_exactly_and_ambiguity_has_no_effect(
    tmp_path,
):
    from browser_agent.browser import BrowserError

    html = '<select aria-label="Size" onchange="window.effects=(window.effects||0)+1"><option value="s">Small</option><option value="l">Large</option></select>'
    async with graph_case(tmp_path, html) as (
        browser,
        _gateway,
        _store,
        _runtime,
        _graph,
        _config,
        _initial,
        _events,
    ):
        obs = await browser.observe()
        ref = choose_ref(obs, "Size")
        await browser.execute("select", {"ref": ref, "value": "Large"}, obs["id"])
        assert await browser.page.get_by_role("combobox").input_value() == "l"
        assert await browser.page.evaluate("window.effects") == 1
        await browser.page.set_content(
            '<select aria-label="Size"><option value="a">Same</option><option value="b">Same</option></select>'
        )
        obs = await browser.observe()
        admissions = []
        with pytest.raises(BrowserError, match="one enabled"):
            await browser.execute(
                "select",
                {"ref": choose_ref(obs, "Size"), "value": "Same"},
                obs["id"],
                before_dispatch=lambda: admissions.append(True),
            )
        assert admissions == []


async def test_browser_result_retains_observed_content_for_next_decision(tmp_path):
    from test_graph_resume import click

    html = "<h1>Notebook</h1><button onclick=\"document.querySelector('output').textContent='Observed fact: coral at noon'\">Inspect</button><output></output>"
    async with graph_case(
        tmp_path, html, [click("Inspect")], classification="ordinary"
    ) as (_browser, _gateway, _store, _runtime, graph, config, initial, _events):
        result = await graph.ainvoke(initial, config)
        assert "Observed fact: coral at noon" in str(result["history"])
        assert result["visited"][0]["title"] is not None
        assert result["visited"][0]["evidence_id"] in result["evidence_ids"]


async def test_recall_serves_only_registered_evidence_without_actionable_old_refs(
    tmp_path,
):
    from test_graph_resume import finish

    async with graph_case(tmp_path, script=[]) as (
        browser,
        gateway,
        _store,
        _runtime,
        graph,
        config,
        initial,
        _events,
    ):

        def recall(obs):
            return "recall", {"evidence_id": obs["id"], "offset": 0}

        gateway.script = [recall, finish]
        result = await graph.ainvoke(initial, config)
        history = str(result["history"])
        assert "historical; not actionable" in history
        assert "Send application" in history
        assert await browser.page.evaluate("window.effects || 0") == 0


def test_user_constraints_and_page_receipts_survive_history_compaction():
    from browser_agent.context import build_input

    state = {
        "task": "Original task",
        "clarifications": ["Retain the selected original set"],
        "visited": [
            {
                "url": "https://example.com/read",
                "title": "Already inspected",
                "evidence_id": "obs-known",
            }
        ],
        "history": [],
        "observation": {"text": "Current page"},
    }
    serialized = str(build_input(state))
    assert "Retain the selected original set" in serialized
    assert "Already inspected" in serialized and "obs-known" in serialized


async def test_returning_from_distinct_pages_is_progress_not_a_repeated_loop(tmp_path):
    from test_graph_resume import click

    html = """<h1>Collection</h1><button onclick="document.querySelector('h1').textContent='Entry A'">First</button><button onclick="document.querySelector('h1').textContent='Entry B'">Second</button><button onclick="document.querySelector('h1').textContent='Entry C'">Third</button><button onclick="document.querySelector('h1').textContent='Collection'">Return</button>"""
    script = [
        click("First"),
        click("Return"),
        click("Second"),
        click("Return"),
        click("Third"),
        click("Return"),
    ]
    async with graph_case(tmp_path, html, script, classification="ordinary") as (
        _browser,
        gateway,
        _store,
        _runtime,
        graph,
        config,
        initial,
        _events,
    ):
        result = await graph.ainvoke(initial, config)
        assert "__interrupt__" not in result
        assert gateway.calls == 7
        assert (
            result["result"]["status"] == "partial"
        )  # scripted test finish, not task-quality claim


LOOP_PAGES = """<h1>Collection</h1>
<button onclick="document.querySelector('h1').textContent='Entry A'">First</button>
<button onclick="document.querySelector('h1').textContent='Entry B'">Second</button>
<button onclick="document.querySelector('h1').textContent='Collection'">Return</button>
"""


async def test_novel_pages_reset_loop_epoch_before_revisit_and_approved_effect(
    tmp_path,
):
    from test_graph_resume import FORM, click

    # The third Collection -> A transition is legitimate after inspecting B.
    script = [
        click(label)
        for label in [
            "First",
            "Return",
            "First",
            "Return",
            "Second",
            "Return",
            "First",
            "Send application",
        ]
    ]
    async with graph_case(
        tmp_path, LOOP_PAGES + FORM, script, classification="ordinary"
    ) as (browser, gateway, store, _runtime, graph, config, initial, _events):
        paused = await graph.ainvoke(initial, config)
        assert pending(paused)["kind"] == "approval"
        assert gateway.calls == 8
        assert await browser.page.evaluate("window.effects || 0") == 0
        result = await graph.ainvoke(approval_answer(paused), config)
        assert result["result"]["status"] == "partial"
        assert await browser.page.evaluate("window.effects") == 1
        assert store.approval(pending(paused)["request_id"])["status"] == "consumed"
        assert not store.unresolved_actions(initial["run_id"])
        # Continuing a terminal checkpoint cannot replay the approved mutation.
        await graph.ainvoke(None, config)
        assert await browser.page.evaluate("window.effects") == 1


async def test_known_page_cycle_stops_despite_each_transition_changing_page(tmp_path):
    from test_graph_resume import click

    script = [click("First"), click("Return")] * 5
    async with graph_case(tmp_path, LOOP_PAGES, script, classification="ordinary") as (
        _browser,
        gateway,
        _store,
        _runtime,
        graph,
        config,
        initial,
        _events,
    ):
        result = await graph.ainvoke(initial, config)
        assert "three times" in pending(result)["question"]
        assert gateway.calls == 5
        assert len(result["loop_seen_states"]) == 2
        assert max(result["repetitions"].values()) == 3


@pytest.mark.parametrize("change_notes", [False, True])
async def test_noop_reference_focus_and_actor_note_churn_do_not_reset_loop(
    tmp_path, change_notes
):
    from test_graph_resume import click

    def remember(obs):
        return "remember", {"notes": "I claim new progress " + obs["id"], "scope": None}

    script = [click("Inspect")]
    for _ in range(2):
        script += ([remember] if change_notes else []) + [click("Inspect")]
    async with graph_case(
        tmp_path,
        "<h1>Unchanged</h1><button>Inspect</button>",
        script,
        classification="ordinary",
    ) as (browser, gateway, _store, _runtime, graph, config, initial, _events):
        result = await graph.ainvoke(initial, config)
        assert "three times" in pending(result)["question"]
        assert gateway.calls == (5 if change_notes else 3)
        assert len(result["loop_seen_states"]) == 1
        assert max(result["repetitions"].values()) == 3
        assert await browser.page.get_by_role("button").evaluate(
            "element => document.activeElement === element"
        )
        # Actual observations have fresh IDs/refs, but one semantic state.
        assert len(set(result["evidence_ids"])) >= 4


async def test_loop_epoch_survives_new_graph_and_sqlite_connection_resume(tmp_path):
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from langgraph.types import Command
    from test_graph_resume import click

    def ask(_obs):
        return "ask_user", {
            "question": "Which label should the result use?",
            "kind": "clarification",
        }

    script = [click("First"), click("Return")] * 2 + [ask, click("First")]
    async with graph_case(tmp_path, LOOP_PAGES, script, classification="ordinary") as (
        _browser,
        gateway,
        _store,
        runtime,
        graph,
        config,
        initial,
        _events,
    ):
        paused = await graph.ainvoke(initial, config)
        assert "Which label" in pending(paused)["question"]
        saved = await graph.aget_state(config)
        before = saved.values["loop_seen_states"]
        assert len(before) == 2
        assert max(saved.values["repetitions"].values()) == 2
        async with AsyncSqliteSaver.from_conn_string(
            str(tmp_path / "checkpoints.sqlite")
        ) as saver:
            resumed_graph = runtime.compile(saver)
            restored = await resumed_graph.aget_state(config)
            assert restored.values["loop_seen_states"] == before
            assert restored.values["repetitions"] == saved.values["repetitions"]
            result = await resumed_graph.ainvoke(
                Command(resume={"answer": "Use the default label."}), config
            )
        assert "three times" in pending(result)["question"]
        assert result["loop_seen_states"] == before
        assert gateway.calls == 6


async def test_full_seen_state_memory_never_evicts_or_resets_for_untracked_pages(
    tmp_path,
):
    async with graph_case(tmp_path, "<h1>Untracked page</h1>") as (
        browser,
        _gateway,
        _store,
        runtime,
        _graph,
        _config,
        initial,
        _events,
    ):
        observation = await browser.observe()
        original = [f"{index:064x}" for index in range(240)]
        state = initial | {
            "observation": observation,
            "action": {"tool": "click", "args": {"ref": "old"}},
            "metadata": {"name": "Inspect"},
            "loop_seen_states": original,
        }
        for _ in range(3):
            state |= await runtime.verify(state)
        assert state["route"] == "ask"
        assert state["loop_seen_states"] == original
        assert max(state["repetitions"].values()) == 3
