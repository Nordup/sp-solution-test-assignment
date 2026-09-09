"""Real browser/graph endpoint enforcement; scripted semantic judgements only."""

import pytest
from test_graph_resume import approval_answer, click, graph_case, pending

PAGE = """<section id="draft"><h1>Draft prepared</h1><p>Document title: Quarterly report.</p>
<button type="button" onclick="window.preparations=(window.preparations||0)+1;document.querySelector('#draft').hidden=true;document.querySelector('#final').hidden=false">Open final review</button></section>
<section id="final" hidden><h1>Final signature review</h1><p>The reviewed document is ready for signature.</p>
<button onclick="window.commitments=(window.commitments||0)+1">Sign and publish</button></section>"""
TASK = "Prepare the document through the final signature review and stop immediately before signing or publishing it."


def finish(quote):
    def proposal(obs):
        return "finish", {
            "status": "completed",
            "summary": "Document preparation is complete; nothing was signed or published.",
            "claims": [
                {
                    "claim": "Current preparation state is visible.",
                    "evidence_id": obs["id"],
                    "quote": quote,
                }
            ],
            "remaining": [],
        }

    return proposal


@pytest.mark.parametrize("approved", [False, True])
async def test_premature_truthful_finish_repairs_to_requested_endpoint_under_exact_approval(
    tmp_path, approved
):
    script = [
        finish("Draft prepared"),
        click("Open final review"),
        finish("Final signature review"),
    ]
    async with graph_case(tmp_path, html=PAGE, script=script) as (
        browser,
        gateway,
        _store,
        _runtime,
        graph,
        config,
        initial,
        events,
    ):
        checks = []

        async def verify(task, proposal, evidence):
            assert task == TASK
            text = evidence[proposal["claims"][0]["evidence_id"]]
            reached = "Final signature review" in text
            assert await browser.page.evaluate("window.commitments||0") == 0
            checks.append(reached)
            # Facts can be true while the endpoint remains unmet. Runtime must
            # reject inconsistent supported=True plus not_reached/remaining work.
            return {
                "supported": True,
                "next_visible_action": {
                    "kind": "excluded_final_effect"
                    if reached
                    else "permitted_preparation",
                    "effect": "Sign and publish."
                    if reached
                    else "Open another review stage.",
                    "evidence": [
                        {
                            "evidence_id": proposal["current_observation_id"],
                            "quote": "Sign and publish"
                            if reached
                            else "Open final review",
                        }
                    ],
                },
                "boundary_status": "reached" if reached else "not_reached",
                "remaining_permitted_steps": []
                if reached
                else ["Open the final signature review."],
                "reason": "The observed stage "
                + ("is" if reached else "is not")
                + " the user-requested stopping point.",
            }

        gateway.verify_completion = verify
        result = await graph.ainvoke(initial | {"task": TASK}, config)
        assert pending(result)["kind"] == "approval"
        assert result["completion_repairs"] == 1
        assert await browser.page.evaluate("window.preparations||0") == 0
        assert any(
            name == "completion_repair"
            and any("final signature review" in problem for problem in data["problems"])
            for name, data in events
        )
        result = await graph.ainvoke(approval_answer(result, approved=approved), config)
        assert result["result"]["status"] == ("completed" if approved else "partial")
        assert checks == ([False, True] if approved else [False])
        assert await browser.page.evaluate("window.preparations||0") == int(approved)
        assert await browser.page.evaluate("window.commitments||0") == 0


@pytest.mark.parametrize("assessment", ["not_reached", "uncertain", "remaining"])
async def test_boundary_review_exhaustion_stays_truthfully_partial(
    tmp_path, assessment
):
    async with graph_case(
        tmp_path, html=PAGE, script=[finish("Draft prepared")] * 3
    ) as (browser, gateway, _store, _runtime, graph, config, initial, _events):
        calls = 0

        async def verify(task, proposal, evidence):
            nonlocal calls
            calls += 1
            return {
                "supported": True,
                "next_visible_action": {
                    "kind": "not_applicable",
                    "effect": "No explicit stop-before effect in this synthetic goal.",
                    "evidence": [],
                },
                "boundary_status": "reached"
                if assessment == "remaining"
                else assessment,
                "remaining_permitted_steps": [
                    "Complete the required intermediate review."
                ]
                if assessment == "remaining"
                else [],
                "reason": "The requested endpoint has not been established.",
            }

        gateway.verify_completion = verify
        result = await graph.ainvoke(initial | {"task": TASK}, config)
        assert calls == 3 and result["completion_repairs"] == 2
        assert result["result"]["status"] == "partial"
        assert result["result"]["summary"].startswith("Completion was not verified:")
        assert result["result"]["claims"] == []
        assert await browser.page.evaluate("window.preparations||0") == 0
        assert await browser.page.evaluate("window.commitments||0") == 0


async def test_read_only_goal_does_not_require_visible_workflow_actions(tmp_path):
    async with graph_case(
        tmp_path, html=PAGE, script=[finish("Document title: Quarterly report.")]
    ) as (browser, gateway, _store, _runtime, graph, config, initial, _events):

        async def verify(task, proposal, evidence):
            assert (
                task == "Find and report the document title without modifying anything."
            )
            assert any(
                "Document title: Quarterly report." in text
                for text in evidence.values()
            )
            return {
                "supported": True,
                "next_visible_action": {
                    "kind": "not_applicable",
                    "effect": "No explicit stop-before effect in this synthetic goal.",
                    "evidence": [],
                },
                "boundary_status": "not_applicable",
                "remaining_permitted_steps": [],
                "reason": "The requested finding is observed; additional workflow actions are outside this read-only goal.",
            }

        gateway.verify_completion = verify
        result = await graph.ainvoke(
            initial
            | {
                "task": "Find and report the document title without modifying anything."
            },
            config,
        )
        assert result["result"]["status"] == "completed"
        assert not result.get("completion_repairs")
        assert await browser.page.evaluate("window.preparations||0") == 0
        assert await browser.page.evaluate("window.commitments||0") == 0


@pytest.mark.parametrize(
    "bad_proof",
    ["historical", "fabricated_quote", "fabricated_id", "missing", "intermediate_kind"],
)
async def test_reached_endpoint_requires_consistent_positive_current_source_proof(
    tmp_path, bad_proof
):
    async with graph_case(
        tmp_path, html=PAGE, script=[finish("Draft prepared")] * 3
    ) as (browser, gateway, _store, runtime, graph, config, initial, _events):
        state = initial | {"task": TASK}
        await browser.page.evaluate(
            "document.querySelector('#draft').hidden=true;document.querySelector('#final').hidden=false"
        )
        state |= await runtime.observe(state)
        historical_id = state["observation"]["id"]
        assert "Sign and publish" in state["observation"]["text"]
        await browser.page.set_content(PAGE)
        reviews = 0

        async def verify(task, proposal, evidence):
            nonlocal reviews
            reviews += 1
            current_id = proposal["current_observation_id"]
            assert (
                current_id != historical_id and "Draft prepared" in evidence[current_id]
            )
            source_id = (
                historical_id
                if bad_proof == "historical"
                else "fabricated"
                if bad_proof == "fabricated_id"
                else current_id
            )
            quote = (
                "Open final review"
                if bad_proof == "intermediate_kind"
                else "Sign and publish"
            )
            if bad_proof == "historical":
                assert quote in evidence[historical_id]  # Authentic, but stale.
            return {
                "next_visible_action": {
                    "effect": "Alleged excluded final effect.",
                    "kind": "permitted_preparation"
                    if bad_proof == "intermediate_kind"
                    else "excluded_final_effect",
                    "evidence": []
                    if bad_proof == "missing"
                    else [{"evidence_id": source_id, "quote": quote}],
                },
                "boundary_status": "reached",
                "remaining_permitted_steps": [],
                "supported": True,
                "reason": "Synthetic overconfident endpoint assessment.",
            }

        gateway.verify_completion = verify
        result = await graph.ainvoke(state, config)
        assert result["result"]["status"] == "partial"
        assert result["completion_repairs"] == 2 and reviews == 3
        assert await browser.page.evaluate("window.preparations||0") == 0
        assert await browser.page.evaluate("window.commitments||0") == 0
        assert result["result"]["summary"].startswith("Completion was not verified:")


async def test_completed_requested_effect_without_stop_before_needs_no_extra_endpoint(
    tmp_path,
):
    from test_graph_resume import FORM

    async with graph_case(
        tmp_path,
        html=FORM,
        script=[click("Send application"), finish("Application recorded")],
    ) as (browser, _gateway, _store, _runtime, graph, config, initial, events):
        waiting = await graph.ainvoke(
            initial
            | {"task": "Send my prepared application to Acme and report the outcome."},
            config,
        )
        assert pending(waiting)["kind"] == "approval"
        result = await graph.ainvoke(approval_answer(waiting), config)
        assert result["result"]["status"] == "completed"
        assert await browser.page.evaluate("window.effects") == 1
        review = next(data for name, data in events if name == "completion_review")
        assert review["boundary_status"] == "not_applicable"
        assert review["next_visible_action"]["kind"] == "not_applicable"
