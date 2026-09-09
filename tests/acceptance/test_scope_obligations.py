"""Actual graph/browser boundary tests; only model judgements are scripted.

These prove durability and enforcement, not autonomous model ambiguity detection.
"""

import json

import pytest
from langgraph.types import Command
from test_graph_resume import approval_answer, click, graph_case, pending

from browser_agent.context import build_input
from browser_agent.tools import ProtocolError

PAGE = """<h1>Two possible destinations</h1>
<p id="choices">North and South both match. The owner must choose before sending.</p>
<a href="#detail" onclick="document.querySelector('#choices').remove()">Explore North</a>
<form action="https://fixture.test/send" method="post" onsubmit="event.preventDefault();window.effects=(window.effects||0)+1;document.querySelector('output').textContent='Delivery recorded'">
<input aria-label="Destination" value="North"><button>Send record</button></form><output></output>"""
AMBIGUITY = "North and South both match. The owner must choose before sending."


def review(*, ordinary=False, uncertain=False, new=(), resolved=(), unaffected=()):
    return {
        "classification": "ordinary" if ordinary else "consequential",
        "scope_status": "uncertain" if uncertain else "in_scope",
        "effect_summary": "Explore a candidate"
        if ordinary
        else "Send the record to North",
        "reason": AMBIGUITY if uncertain else "Current destination is North",
        "new_obligations": list(new),
        "scope_resolutions": list(resolved),
        "unaffected_obligation_ids": list(unaffected),
    }


def install_reviewer(
    gateway, *, answer=False, unaffected=False, legacy_uncertain=False
):
    async def risk(task, action, metadata):
        context = metadata["task_context"]
        obligations = context["scope_obligations"]
        if metadata.get("tag") == "a":
            source_id = next(
                key
                for key, value in metadata["scope_sources"].items()
                if key.startswith("obs-") and AMBIGUITY in value
            )
            if legacy_uncertain:
                return review(ordinary=True, uncertain=True)
            return review(
                ordinary=True,
                new=[
                    {
                        "description": AMBIGUITY,
                        "affects_collection_selection": True,
                        "evidence": [{"source_id": source_id, "quote": AMBIGUITY}],
                    }
                ],
            )
        resolutions = []
        if answer and context["user_clarifications"] == ["Choose North."]:
            resolutions = [
                {
                    "obligation_id": item["id"],
                    "reason": "The actual owner selected North.",
                    "evidence": [
                        {"source_id": "user_answer:0", "quote": "Choose North."}
                    ],
                }
                for item in obligations
                if item["status"] == "open"
            ]
        return review(
            resolved=resolutions,
            unaffected=[item["id"] for item in obligations] if unaffected else [],
        )

    gateway.review = risk


@pytest.mark.parametrize("legacy_uncertain", [False, True])
async def test_uncertain_choice_survives_navigation_notes_and_old_checkpoint(
    tmp_path, legacy_uncertain
):
    async with graph_case(
        tmp_path, html=PAGE, script=[click("Explore North"), click("Send record")]
    ) as (browser, gateway, store, runtime, graph, config, initial, events):
        install_reviewer(gateway, legacy_uncertain=legacy_uncertain)
        result = await graph.ainvoke(
            initial | {"task": "Send a record to the matching destination."}, config
        )
        assert pending(result)["kind"] == "clarification"
        assert AMBIGUITY in pending(result)["question"]
        assert await browser.page.evaluate("window.effects||0") == 0
        assert not any(event == "approval" for event, _ in events)
        # The ambiguity survives a stale checkpoint and a misleading memory rewrite.
        runtime.persist_memory(initial | {"notes": "North is selected.", "scope": None})
        restored = runtime.restore_memory(initial | {"scope_obligations": []})
        assert restored["scope_obligations"][0]["status"] == "open"
        assert AMBIGUITY in json.dumps(build_input(restored))
        assert store.unresolved_actions(initial["run_id"]) == []
        # Ambiguous classification must not forbid preserving observed membership.
        source = restored["scope_obligations"][0]["evidence"][0]["source_id"]
        state = restored | {"evidence_ids": [source]}
        scope = {
            "boundary": "Original observed candidates",
            "items": [{"identity": "North", "evidence_id": source, "quote": "North"}],
        }
        with pytest.raises(ProtocolError):
            runtime.validate_scope(scope, state)
        # Classification uncertainty about an already selected member is different.
        state["scope_obligations"] = [
            state["scope_obligations"][0] | {"affects_collection_selection": False}
        ]
        assert runtime.validate_scope(scope, state) == scope


@pytest.mark.parametrize("approved", [False, True])
async def test_actual_answer_resolves_choice_but_still_requires_exact_approval(
    tmp_path,
    approved,
):
    async with graph_case(
        tmp_path,
        html=PAGE,
        script=[click("Explore North"), click("Send record"), click("Send record")],
    ) as (browser, gateway, _store, runtime, graph, config, initial, _events):
        install_reviewer(gateway, answer=True)
        first = await graph.ainvoke(initial, config)
        assert pending(first)["kind"] == "clarification"
        second = await graph.ainvoke(
            Command(resume={"answer": "Choose North."}), config
        )
        assert pending(second)["kind"] == "approval"
        assert await browser.page.evaluate("window.effects||0") == 0
        result = await graph.ainvoke(approval_answer(second, approved=approved), config)
        assert "__interrupt__" not in result
        assert await browser.page.evaluate("window.effects||0") == int(approved)
        restored = runtime.restore_memory(initial)
        assert restored["scope_obligations"][0]["status"] == "resolved"
        assert (
            restored["scope_obligations"][0]["resolution"]["evidence"][0]["source_id"]
            == "user_answer:0"
        )
        # The actual journal includes the action and post-dispatch evidence, unlike notes.
        records = runtime.action_journal(restored)["records"]
        assert (
            any(
                record["approval_id"] and record["result_evidence_id"]
                for record in records
            )
            == approved
        )


async def test_irrelevant_human_reply_does_not_clear_obligation(tmp_path):
    async with graph_case(
        tmp_path,
        html=PAGE,
        script=[click("Explore North"), click("Send record"), click("Send record")],
    ) as (browser, gateway, _store, _runtime, graph, config, initial, _events):
        install_reviewer(gateway, answer=True)
        await graph.ainvoke(initial, config)
        result = await graph.ainvoke(Command(resume={"answer": "Continue."}), config)
        assert pending(result)["kind"] == "clarification"
        assert await browser.page.evaluate("window.effects||0") == 0


async def test_observed_resolution_quotes_validated_and_persisted(
    tmp_path,
):
    async with graph_case(tmp_path, html=PAGE) as (
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
        evidence_id = state["observation"]["id"]
        open_review = review(
            new=[
                {
                    "description": AMBIGUITY,
                    "affects_collection_selection": True,
                    "evidence": [{"source_id": evidence_id, "quote": AMBIGUITY}],
                }
            ]
        )
        obligations = runtime.update_obligations(
            state, open_review, runtime.obligation_sources(state)
        )
        state |= {"scope_obligations": obligations}
        resolution = {
            "obligation_id": obligations[0]["id"],
            "reason": "A claimed new observation rules out South.",
            "evidence": [{"source_id": "forged", "quote": "South is unavailable."}],
        }
        with pytest.raises(ProtocolError):
            runtime.update_obligations(
                state, review(resolved=[resolution]), runtime.obligation_sources(state)
            )
        # Actual new observed facts can resolve without inventing a human answer.
        await browser.page.set_content(
            "<p>South is unavailable. North is the only matching destination.</p>"
        )
        update = await runtime.observe(state)
        state |= update
        resolution["evidence"] = [
            {
                "source_id": state["observation"]["id"],
                "quote": "South is unavailable. North is the only matching destination.",
            }
        ]
        resolved = runtime.update_obligations(
            state, review(resolved=[resolution]), runtime.obligation_sources(state)
        )
        assert resolved[0]["status"] == "resolved"
        assert runtime.restore_memory(initial)["scope_obligations"] == resolved


async def test_final_review_gets_uncertainty_and_empty_authorship_then_repairs_report(
    tmp_path,
):
    def proposal(obs):
        return "finish", {
            "status": "completed",
            "summary": "I sent the record.",
            "claims": [
                {
                    "claim": "Record is present.",
                    "evidence_id": obs["id"],
                    "quote": "Record already delivered.",
                }
            ],
            "remaining": [],
        }

    def corrected(obs):
        name, args = proposal(obs)
        return name, args | {
            "summary": "Record was already delivered before this run; no new delivery. North versus South remains uncertain and no further change was made."
        }

    html = (
        "<p>Record already delivered.</p><p>North versus South remains uncertain.</p>"
    )
    async with graph_case(tmp_path, html=html, script=[proposal, corrected]) as (
        _browser,
        gateway,
        _store,
        runtime,
        graph,
        config,
        initial,
        _events,
    ):
        state = initial | await runtime.observe(initial)
        source_id = state["observation"]["id"]
        state["scope_obligations"] = runtime.update_obligations(
            state,
            review(
                new=[
                    {
                        "description": "North versus South remains uncertain.",
                        "affects_collection_selection": False,
                        "evidence": [
                            {
                                "source_id": source_id,
                                "quote": "North versus South remains uncertain.",
                            }
                        ],
                    }
                ]
            ),
            runtime.obligation_sources(state),
        )
        observed = []

        async def verify(task, result, evidence):
            observed.append(result)
            assert result["action_journal"]["records"] == []
            assert result["task_context"]["scope_obligations"][0]["status"] == "open"
            assert "Record already delivered." in "\n".join(evidence.values())
            return {
                "next_visible_action": {
                    "kind": "not_applicable",
                    "effect": "No explicit stop-before effect in this synthetic goal.",
                    "evidence": [],
                },
                "boundary_status": "not_applicable",
                "remaining_permitted_steps": [],
                "supported": len(observed) == 2,
                "reason": "Existing state does not prove this run sent anything; disclose retained uncertainty.",
            }

        gateway.verify_completion = verify
        result = await graph.ainvoke(state, config)
        assert result["result"]["status"] == "completed"
        assert len(observed) == 2
        assert result["result"]["unresolved_decisions"] == [
            "North versus South remains uncertain."
        ]
        assert "already delivered before this run" in result["result"]["summary"]
        assert not runtime.action_journal(runtime.restore_memory(result))["records"]


async def test_independent_effect_can_proceed_while_another_choice_remains_open(
    tmp_path,
):
    html = PAGE.replace('value="North"', 'value="West"')
    async with graph_case(
        tmp_path, html=html, script=[click("Explore North"), click("Send record")]
    ) as (browser, gateway, _store, runtime, graph, config, initial, _events):
        install_reviewer(gateway, unaffected=True)
        result = await graph.ainvoke(
            initial
            | {
                "task": "Send the record to West. Separately inspect North and South without choosing either."
            },
            config,
        )
        assert pending(result)["kind"] == "approval"
        assert "West" in json.dumps(pending(result)["details"])
        result = await graph.ainvoke(approval_answer(result), config)
        assert await browser.page.evaluate("window.effects||0") == 1
        assert (
            runtime.restore_memory(initial)["scope_obligations"][0]["status"] == "open"
        )
        assert AMBIGUITY in result["result"]["summary"]


async def test_opposite_user_choice_is_not_locked_out_by_forced_candidate_memory(
    tmp_path,
):
    html = (
        PAGE
        + '<form action="https://fixture.test/south" method="post" onsubmit="event.preventDefault();window.effects=(window.effects||0)+1;window.chosen=\'South\'"><input aria-label="Destination" value="South"><button>Send South</button></form>'
    )
    async with graph_case(
        tmp_path,
        html=html,
        script=[click("Explore North"), click("Send record"), click("Send South")],
    ) as (browser, gateway, _store, runtime, graph, config, initial, _events):
        install_reviewer(gateway)
        original_call, original_review = gateway.call, gateway.review
        attempted_freeze = False

        async def actor(request, purpose="actor"):
            nonlocal attempted_freeze
            response = await original_call(request, purpose)
            if purpose == "memory" and not attempted_freeze:
                attempted_freeze = True
                current = next(
                    item["content"]
                    for item in reversed(request["input"])
                    if isinstance(item.get("content"), str)
                    and item["content"].startswith("Current browser observation")
                )
                obs = json.loads(current.split("\n", 1)[1])
                response["output"][0]["arguments"] = json.dumps(
                    {
                        "notes": "North candidate explored.",
                        "scope": {
                            "boundary": "Selected candidate",
                            "items": [
                                {
                                    "identity": "North",
                                    "evidence_id": obs["id"],
                                    "quote": "North",
                                }
                            ],
                        },
                    }
                )
            return response

        async def risk(task, action, metadata):
            if metadata["task_context"]["user_clarifications"] == ["Choose South."]:
                return review(
                    resolved=[
                        {
                            "obligation_id": item["id"],
                            "reason": "Actual owner selected South.",
                            "evidence": [
                                {"source_id": "user_answer:0", "quote": "Choose South."}
                            ],
                        }
                        for item in metadata["task_context"]["scope_obligations"]
                        if item["status"] == "open"
                    ]
                )
            return await original_review(task, action, metadata)

        gateway.call, gateway.review = actor, risk
        result = await graph.ainvoke(initial, config)
        assert pending(result)["kind"] == "clarification"
        assert attempted_freeze and gateway.memory_calls >= 2
        assert runtime.restore_memory(initial).get("scope") is None
        result = await graph.ainvoke(
            Command(resume={"answer": "Choose South."}), config
        )
        assert pending(result)["kind"] == "approval"
        assert "South" in json.dumps(pending(result)["details"])
        assert await browser.page.evaluate("window.effects||0") == 0
        await graph.ainvoke(approval_answer(result), config)
        assert await browser.page.evaluate("window.effects") == 1
        assert await browser.page.evaluate("window.chosen") == "South"


@pytest.mark.parametrize("failure_kind", ["bad_quote", "bad_source"])
async def test_reviewer_repairs_its_own_rejected_scope_evidence_before_dispatch(
    tmp_path, failure_kind
):
    resolved_fact = "South is unavailable. North is the only matching destination."
    html = PAGE.replace(
        "document.querySelector('#choices').remove()",
        "document.querySelector('#choices').textContent='" + resolved_fact + "'",
    )
    async with graph_case(
        tmp_path, html=html, script=[click("Explore North"), click("Send record")]
    ) as (browser, gateway, _store, runtime, graph, config, initial, events):
        install_reviewer(gateway)
        original_review = gateway.review
        attempts = []

        async def risk(task, action, metadata):
            if metadata.get("tag") == "a":
                return await original_review(task, action, metadata)
            assert await browser.page.evaluate("window.effects||0") == 0
            if not any(
                item["status"] == "open"
                for item in metadata["task_context"]["scope_obligations"]
            ):
                return review()
            attempts.append(metadata)
            source_id = next(
                key
                for key, text in metadata["scope_sources"].items()
                if resolved_fact in text
            )
            quote = resolved_fact
            if len(attempts) == 1:
                if failure_kind == "bad_quote":
                    quote = "South unavailable; North only matching destination."
                else:
                    source_id = "invented-observation"
            else:
                feedback = metadata["scope_review_feedback"]
                assert feedback["errors"][0]["error"] == (
                    "quote_not_exact_substring"
                    if failure_kind == "bad_quote"
                    else "unknown_source_id"
                )
                assert any(
                    resolved_fact in fragment
                    for item in metadata["scope_quote_candidates"]
                    for fragment in item["exact_fragments"]
                )
            open_items = [
                item
                for item in metadata["task_context"]["scope_obligations"]
                if item["status"] == "open"
            ]
            return review(
                resolved=[
                    {
                        "obligation_id": item["id"],
                        "reason": "Observed source eliminates the alternative.",
                        "evidence": [{"source_id": source_id, "quote": quote}],
                    }
                    for item in open_items
                ]
            )

        gateway.review = risk
        result = await graph.ainvoke(initial, config)
        assert pending(result)["kind"] == "approval"
        assert result["scope_review_repairs"] == 1
        assert result.get("failures", 0) == 0
        assert (
            gateway.calls == 2
        )  # Actor never had to guess what the reviewer got wrong.
        assert any(name == "scope_review_rejected" for name, _ in events)
        assert (
            runtime.restore_memory(initial)["scope_obligations"][0]["status"]
            == "resolved"
        )
        await graph.ainvoke(approval_answer(result), config)
        assert await browser.page.evaluate("window.effects||0") == 1


async def test_reviewer_quote_repair_exhaustion_is_bounded_handover_without_actor_retry(
    tmp_path,
):
    async with graph_case(
        tmp_path, html=PAGE, script=[click("Explore North"), click("Send record")]
    ) as (browser, gateway, _store, runtime, graph, config, initial, events):
        install_reviewer(gateway)
        original_review = gateway.review
        attempts = 0

        async def risk(task, action, metadata):
            nonlocal attempts
            if metadata.get("tag") == "a":
                return await original_review(task, action, metadata)
            attempts += 1
            return review(
                resolved=[
                    {
                        "obligation_id": metadata["task_context"]["scope_obligations"][
                            0
                        ]["id"],
                        "reason": "Unsupported fabricated resolution.",
                        "evidence": [
                            {
                                "source_id": "invented-source",
                                "quote": "The user selected North.",
                            }
                        ],
                    }
                ]
            )

        gateway.review = risk
        result = await graph.ainvoke(initial, config)
        assert pending(result)["kind"] == "clarification"
        assert "independent reviewer" in pending(result)["question"]
        assert attempts == 3 and gateway.calls == 2
        assert result["scope_review_repairs"] == 2
        assert len([name for name, _ in events if name == "scope_review_rejected"]) == 3
        assert (
            runtime.restore_memory(initial)["scope_obligations"][0]["status"] == "open"
        )
        assert await browser.page.evaluate("window.effects||0") == 0


async def test_scope_repair_budget_failure_stops_before_effect(tmp_path):
    from browser_agent.storage import BudgetExceeded

    async with graph_case(
        tmp_path, html=PAGE, script=[click("Explore North"), click("Send record")]
    ) as (browser, gateway, _store, runtime, graph, config, initial, _events):
        install_reviewer(gateway)
        original_review = gateway.review

        async def risk(task, action, metadata):
            if metadata.get("tag") == "a":
                return await original_review(task, action, metadata)
            if metadata.get("scope_review_feedback"):
                raise BudgetExceeded("No budget for reviewer repair.")
            return review(
                resolved=[
                    {
                        "obligation_id": metadata["task_context"]["scope_obligations"][
                            0
                        ]["id"],
                        "reason": "Unsupported resolution.",
                        "evidence": [{"source_id": "missing", "quote": "Chosen."}],
                    }
                ]
            )

        gateway.review = risk
        result = await graph.ainvoke(initial, config)
        assert result["result"]["status"] == "partial"
        assert "No budget" in result["result"]["summary"]
        assert result["scope_review_repairs"] == 1
        assert (
            runtime.restore_memory(initial)["scope_obligations"][0]["status"] == "open"
        )
        assert await browser.page.evaluate("window.effects||0") == 0


def test_scope_quote_copy_aid_is_bounded_and_never_reformats_sources():
    from browser_agent.graph import AgentGraph

    sources = {
        "observed": "  First fact.\n\n  Second\tfact.\n" + ("é" * 1000 + "\n") * 50,
        "other": "A different source.",
    }
    candidates = AgentGraph.scope_quote_candidates(sources)
    assert len(json.dumps(candidates, ensure_ascii=False).encode()) <= 6000
    assert candidates[0]["exact_fragments"][:2] == ["First fact.", "Second\tfact."]
    assert all(
        fragment in sources[item["source_id"]]
        for item in candidates
        for fragment in item["exact_fragments"]
    )


async def test_action_review_receives_actual_prior_page_after_navigation_and_memory(
    tmp_path,
):
    fact = "Prior period record: North is the sole matching destination."
    html = PAGE.replace(AMBIGUITY, fact)
    async with graph_case(
        tmp_path, html=html, script=[click("Explore North"), click("Send record")]
    ) as (browser, gateway, _store, runtime, graph, config, initial, _events):
        prior_id = None
        action_reviews = []

        async def risk(task, action, metadata):
            nonlocal prior_id
            if metadata.get("tag") == "a":
                prior_id = next(
                    key
                    for key, text in metadata["scope_sources"].items()
                    if fact in text
                )
                return review(ordinary=True)
            # The current page and compressed notes no longer contain the fact.
            assert fact not in metadata["page_text"]
            assert fact not in metadata["task_context"]["working_notes"]
            assert fact in metadata["scope_sources"][prior_id]
            manifest = metadata["scope_evidence_manifest"]["sources"][prior_id]
            assert (
                manifest["saved_at_unix"]
                and manifest["provenance"] == "registered_browser_observation"
            )
            archived = json.loads(
                (runtime.run_dir / "evidence" / f"{prior_id}.json").read_text()
            )
            assert metadata["scope_sources"][prior_id] == archived["text"]
            action_reviews.append(metadata)
            return review()

        gateway.review = risk
        result = await graph.ainvoke(
            initial
            | {
                "task": "Use the prior period record to select the matching destination and send the record."
            },
            config,
        )
        assert pending(result)["kind"] == "approval"
        assert gateway.memory_calls == 1 and len(action_reviews) == 2
        assert await browser.page.evaluate("window.effects||0") == 0
        await graph.ainvoke(approval_answer(result), config)
        assert await browser.page.evaluate("window.effects||0") == 1


@pytest.mark.parametrize("resolve", [True, False])
async def test_preask_known_fact_enriches_risk_review_but_cannot_resolve_or_approve_itself(
    tmp_path, resolve
):
    fact = "Prior period record: North is the sole matching destination."
    html = PAGE.replace(AMBIGUITY, fact)
    async with graph_case(
        tmp_path, html=html, script=[click("Explore North"), click("Send record")]
    ) as (browser, gateway, _store, runtime, graph, config, initial, events):
        check_count = 0

        async def risk(task, action, metadata):
            if metadata.get("tag") == "a":
                return review(ordinary=True)
            if metadata.get("scope_evidence_feedback") and resolve:
                feedback = metadata["scope_evidence_feedback"]
                return review(
                    resolved=[
                        {
                            "obligation_id": item["id"],
                            "reason": "The independently reviewed actual earlier record uniquely establishes the choice.",
                            "evidence": feedback["evidence"],
                        }
                        for item in metadata["task_context"]["scope_obligations"]
                        if item["status"] == "open"
                    ]
                )
            if not metadata["task_context"]["scope_obligations"]:
                return review(
                    new=[
                        {
                            "description": "The prior matching destination has not been established in this view.",
                            "affects_collection_selection": True,
                            "evidence": [{"source_id": "user_task", "quote": task}],
                        }
                    ]
                )
            return review()

        async def clarification(task, question, context, evidence):
            nonlocal check_count
            check_count += 1
            source_id = next(key for key, text in evidence.items() if fact in text)
            assert context["task_context"]["scope_obligations"][0]["status"] == "open"
            assert await browser.page.evaluate("window.effects||0") == 0
            return {
                "classification": "already_available",
                "reason": "Actual previously read record contains the requested fact.",
                "evidence": [{"source_id": source_id, "quote": fact}],
            }

        gateway.review, gateway.review_clarification = risk, clarification
        result = await graph.ainvoke(initial, config)
        assert gateway.calls == 2
        assert not any(
            name == "tool_proposed" and data["tool"] == "ask_user"
            for name, data in events
        )
        assert await browser.page.evaluate("window.effects||0") == 0
        if resolve:
            assert check_count == 1 and pending(result)["kind"] == "approval"
            assert (
                runtime.restore_memory(initial)["scope_obligations"][0]["status"]
                == "resolved"
            )
            await graph.ainvoke(approval_answer(result), config)
            assert await browser.page.evaluate("window.effects||0") == 1
        else:
            assert check_count == 2 and pending(result)["kind"] == "clarification"
            assert (
                runtime.restore_memory(initial)["scope_obligations"][0]["status"]
                == "open"
            )


@pytest.mark.parametrize("failure", ["forged", "budget"])
async def test_preask_invalid_existing_fact_or_budget_failure_preserves_real_choice(
    tmp_path, failure
):
    from browser_agent.storage import BudgetExceeded

    async with graph_case(
        tmp_path, html=PAGE, script=[click("Explore North"), click("Send record")]
    ) as (browser, gateway, _store, runtime, graph, config, initial, _events):
        install_reviewer(gateway)

        async def clarification(task, question, context, evidence):
            assert any(AMBIGUITY in text for text in evidence.values())
            if failure == "budget":
                raise BudgetExceeded("Synthetic admission stop.")
            return {
                "classification": "already_available",
                "reason": "Unsupported alleged answer.",
                "evidence": [{"source_id": "forged", "quote": "Choose North."}],
            }

        gateway.review_clarification = clarification
        result = await graph.ainvoke(initial, config)
        assert pending(result)["kind"] == "clarification"
        assert (
            runtime.restore_memory(initial)["scope_obligations"][0]["status"] == "open"
        )
        assert await browser.page.evaluate("window.effects||0") == 0


async def test_action_archive_packet_prioritizes_recalls_resolution_sources_and_reports_omissions(
    tmp_path,
):
    async with graph_case(tmp_path, html=PAGE) as (
        browser,
        _gateway,
        _store,
        runtime,
        _graph,
        _config,
        initial,
        _events,
    ):
        state = initial
        ids = []
        for index in range(6):
            await browser.page.set_content(
                f"<p>Record {index}: " + "evidence " * 650 + "</p>"
            )
            state |= await runtime.observe(state)
            ids.append(state["observation"]["id"])
        # Source refs cannot grant access to an unregistered file.
        runtime.save_observation({"id": "forged", "text": "Secret unsupported data."})
        state |= {
            "recalled_evidence_ids": [ids[0]],
            "scope_obligations": [
                {
                    "id": "resolved",
                    "status": "resolved",
                    "description": "Previously selected record.",
                    "evidence": [],
                    "resolution": {
                        "evidence": [
                            {"source_id": ids[1], "quote": "Record 1"},
                            {"source_id": "forged", "quote": "Secret"},
                        ]
                    },
                }
            ],
        }
        sources, manifest = runtime.scope_packet(state)
        assert (
            len(
                json.dumps(
                    {"sources": sources, "manifest": manifest}, ensure_ascii=False
                ).encode()
            )
            <= 32000
        )
        assert ids[0] in sources and ids[1] in sources
        assert "forged" not in sources
        assert any(
            item["evidence_id"] == "forged"
            and item["reason"] == "unregistered_observation"
            for item in manifest["omitted"]
        )
        assert manifest["omitted"]
        assert all(
            text
            == json.loads((runtime.run_dir / "evidence" / f"{key}.json").read_text())[
                "text"
            ]
            for key, text in sources.items()
            if key not in manifest["user_source_ids"]
        )
