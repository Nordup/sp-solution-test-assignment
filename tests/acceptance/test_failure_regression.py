"""Integrated failure regressions; no real credentials, paid calls, or accounts."""

import pytest
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command
from test_graph_resume import click, graph_case, pending

from browser_agent.storage import AdmissionError


async def test_challenge_pause_survives_sqlite_reopen_without_model_polling(tmp_path):
    async with graph_case(tmp_path, html="<h1>Verify you are human</h1>") as (
        browser,
        gateway,
        _store,
        runtime,
        graph,
        config,
        initial,
        _events,
    ):
        first = await graph.ainvoke(initial, config)
        assert pending(first)["kind"] == "challenge"
        assert gateway.calls == gateway.reviews == 0
        async with AsyncSqliteSaver.from_conn_string(
            str(tmp_path / "checkpoints.sqlite")
        ) as reopened:
            resumed_graph = runtime.compile(reopened)
            again = await resumed_graph.ainvoke(
                Command(resume={"answer": "continue"}), config
            )
            assert pending(again)["kind"] == "challenge"
            assert gateway.calls == gateway.reviews == 0
            await browser.page.set_content("<h1>Verification finished</h1>")
            done = await resumed_graph.ainvoke(
                Command(resume={"answer": "continue"}), config
            )
            assert done["result"]["status"] == "partial"
            assert gateway.calls == 1
            assert len(done["evidence_ids"]) == 3


async def test_unsupported_completion_is_downgraded_without_verifier_spend(tmp_path):
    def unsupported(obs):
        return "finish", {
            "status": "completed",
            "summary": "Everything was done",
            "claims": [
                {
                    "claim": "Application sent",
                    "evidence_id": "invented",
                    "quote": "Application sent",
                }
            ],
            "remaining": [],
        }

    async with graph_case(tmp_path, script=[unsupported]) as (
        browser,
        gateway,
        _store,
        _runtime,
        graph,
        config,
        initial,
        _events,
    ):
        result = await graph.ainvoke(initial, config)
        assert result["result"]["status"] == "partial"
        assert gateway.completion_reviews == 0
        assert await browser.page.evaluate("window.effects || 0") == 0


async def test_completion_with_no_claims_is_partial(tmp_path):
    def unsupported(obs):
        return "finish", {
            "status": "completed",
            "summary": "Completed",
            "claims": [],
            "remaining": [],
        }

    async with graph_case(tmp_path, script=[unsupported]) as (
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
        assert result["result"]["status"] == "partial"
        assert gateway.completion_reviews == 0


async def test_three_ineffective_actions_pause_instead_of_looping(tmp_path):
    async with graph_case(
        tmp_path,
        html="<h1>Unchanged</h1><button>Inspect</button>",
        script=[click("Inspect")] * 8,
        classification="ordinary",
    ) as (_browser, gateway, _store, _runtime, graph, config, initial, _events):
        result = await graph.ainvoke(initial, config)
        assert pending(result)["kind"] == "clarification"
        assert gateway.calls == gateway.reviews == 3
        assert "three times" in pending(result)["question"]


async def test_unresolved_effect_blocks_new_dispatch_and_repeated_continue(tmp_path):
    async with graph_case(
        tmp_path, script=[click("Send application")] * 3, classification="ordinary"
    ) as (browser, _gateway, store, _runtime, graph, config, initial, _events):
        store.dispatch(
            initial["run_id"],
            "interrupted-effect",
            {"tool": "click", "args": {"ref": "old"}},
            {
                "operation": "page_change",
                "destination": "https://fixture.test/apply",
                "objects": [],
            },
            "old-generation",
            requires_approval=False,
        )
        store.finish_action(
            "interrupted-effect", "uncertain", {"reason": "Harness crash boundary"}
        )
        first = await graph.ainvoke(initial, config)
        assert pending(first)["kind"] == "uncertain"
        again = await graph.ainvoke(Command(resume={"answer": "continue"}), config)
        assert pending(again)["kind"] == "uncertain"
        assert await browser.page.evaluate("window.effects || 0") == 0
        assert len(store.unresolved_actions(initial["run_id"])) == 1


async def test_native_dialog_after_effect_causes_uncertainty_without_replay(tmp_path):
    html = "<button onclick=\"window.effects=(window.effects||0)+1;alert('Unexpected notice')\">Continue</button>"
    async with graph_case(
        tmp_path, html=html, script=[click("Continue")] * 2, classification="ordinary"
    ) as (browser, _gateway, store, _runtime, graph, config, initial, _events):
        paused = await graph.ainvoke(initial, config)
        assert pending(paused)["kind"] == "uncertain"
        assert await browser.page.evaluate("window.effects") == 1
        assert len(store.unresolved_actions(initial["run_id"])) == 1
        assert store.unresolved_actions(initial["run_id"])[0]["status"] == "uncertain"


async def test_graph_admission_failure_has_zero_external_effects(tmp_path, monkeypatch):
    async with graph_case(
        tmp_path,
        html='<button onclick="window.effects=1">Inspect</button>',
        script=[click("Inspect")],
        classification="ordinary",
    ) as (browser, _gateway, store, _runtime, graph, config, initial, _events):

        def fail(*args, **kwargs):
            raise AdmissionError("Synthetic storage failure")

        monkeypatch.setattr(store, "dispatch", fail)
        result = await graph.ainvoke(initial, config)
        assert result["result"]["status"] == "partial"
        assert await browser.page.evaluate("window.effects || 0") == 0


async def test_closed_browser_pauses_without_model_call(tmp_path):
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
        await browser.close()
        result = await graph.ainvoke(initial, config)
        assert pending(result)["kind"] == "browser"
        assert gateway.calls == gateway.reviews == 0


async def test_reconciliation_rejects_fabricated_evidence_before_review(tmp_path):
    def fabricated(obs):
        return "reconcile", {
            "action_id": "interrupted-effect",
            "evidence_id": obs["id"],
            "quote": "Invented confirmation not on page",
            "claim": "Submission succeeded",
        }

    async with graph_case(tmp_path, script=[fabricated]) as (
        _browser,
        gateway,
        store,
        _runtime,
        graph,
        config,
        initial,
        _events,
    ):
        store.dispatch(
            initial["run_id"],
            "interrupted-effect",
            {"tool": "click"},
            {
                "operation": "page_change",
                "destination": "https://fixture.test/apply",
                "objects": [],
            },
            "old-generation",
            requires_approval=False,
        )
        store.finish_action("interrupted-effect", "uncertain")
        result = await graph.ainvoke(initial, config)
        assert result["result"]["status"] == "partial"
        assert gateway.completion_reviews == 0
        assert store.action("interrupted-effect")["status"] == "uncertain"


async def test_login_expires_midtask_manual_login_resumes_without_secret_observation(
    tmp_path,
):
    import json

    from browser_agent.browser import BrowserError

    authenticated = True
    secret = "SYNTHETIC_LOGIN_SECRET_NEVER_TO_MODEL"

    def ask_login(obs):
        assert "Session expired" in obs["text"]
        return "ask_user", {
            "kind": "login",
            "question": "Log in manually, then continue.",
        }

    html = '<a href="https://fixture.test/private">Open workspace</a>'
    async with graph_case(
        tmp_path,
        html=html,
        script=[click("Open workspace"), ask_login],
        classification="ordinary",
    ) as (browser, gateway, _store, _runtime, graph, config, initial, _events):

        async def auth_route(route):
            nonlocal authenticated
            if route.request.method == "POST":
                authenticated = True
                await route.fulfill(
                    content_type="text/html",
                    headers={
                        "Set-Cookie": "session=fixture-authenticated; Path=/; Max-Age=3600; Secure"
                    },
                    body="<h1>Authenticated workspace</h1>",
                )
            elif (
                authenticated
                or "session=fixture-authenticated"
                in route.request.headers.get("cookie", "")
            ):
                await route.fulfill(
                    content_type="text/html", body="<h1>Authenticated workspace</h1>"
                )
            else:
                await route.fulfill(
                    content_type="text/html",
                    body='<h1>Session expired</h1><form action="/session" method="post"><input aria-label="Username" name="username"><input type="password" aria-label="Password" name="password"><button>Log in</button></form>',
                )

        await browser.context.route("https://fixture.test/**", auth_route)
        authenticated = False  # Deterministic expiry before the next protected request.
        paused = await graph.ainvoke(initial, config)
        assert pending(paused)["kind"] == "login"
        assert gateway.calls == 2 and gateway.reviews == 1
        before_login = paused["observation"]
        # The human interaction is simulated only by this test harness, never an actor tool.
        await browser.page.get_by_role("textbox", name="Username").fill("fixture-user")
        await browser.page.get_by_label("Password", exact=True).fill(secret)
        assert gateway.calls == 2
        await browser.page.get_by_role("button", name="Log in", exact=True).click()
        assert (
            "Authenticated workspace" in await browser.page.locator("body").inner_text()
        )
        result = await graph.ainvoke(
            Command(resume={"answer": "Manual login completed"}), config
        )
        assert result["observation"]["id"] != before_login["id"]
        assert "Authenticated workspace" in result["observation"]["text"]
        assert secret not in json.dumps(gateway.requests)
        with pytest.raises(BrowserError):
            await browser.describe(before_login["refs"][0], before_login["id"])
        await browser.close()
        await browser.start()
        authenticated = False
        await browser.context.route("https://fixture.test/**", auth_route)
        await browser.page.goto("https://fixture.test/private")
        assert "Authenticated workspace" in (await browser.observe())["text"]


async def test_browser_retry_after_deadline_prevents_early_resume_polling(
    tmp_path, monkeypatch
):
    import time
    from types import SimpleNamespace

    now = [time.time()]
    monkeypatch.setattr(
        "browser_agent.graph.time",
        SimpleNamespace(time=lambda: now[0], monotonic=time.monotonic),
    )
    request_count = []
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

        async def rate_limited(route):
            request_count.append(route.request.url)
            await route.fulfill(
                status=429,
                content_type="text/html",
                headers={"Retry-After": "7"},
                body="<h1>Too many requests</h1>",
            )

        await browser.context.route("https://fixture.test/limited", rate_limited)
        await browser.page.goto("https://fixture.test/limited")
        first = await graph.ainvoke(initial, config)
        assert pending(first)["kind"] == "challenge"
        assert first["retry_not_before"] == now[0] + 7
        evidence_count = len(first["evidence_ids"])
        early = await graph.ainvoke(Command(resume={"answer": "continue"}), config)
        assert "still active" in pending(early)["question"]
        assert len(early["evidence_ids"]) == evidence_count
        assert gateway.calls == gateway.reviews == 0
        assert len(request_count) == 1
        now[0] += 8
        # Explicit stop/re-pause remains valid when the old 429 is still displayed;
        # no automatic request or LLM call may be issued merely on resume.
        expired = await graph.ainvoke(Command(resume={"answer": "continue"}), config)
        assert pending(expired)["kind"] == "challenge"
        assert len(expired["evidence_ids"]) == evidence_count + 1
        assert gateway.calls == gateway.reviews == 0 and len(request_count) == 1


async def test_challenge_after_navigation_stops_all_further_actor_calls(tmp_path):
    html = '<a href="https://fixture.test/verification">Open workspace</a>'
    async with graph_case(
        tmp_path, html=html, script=[click("Open workspace")], classification="ordinary"
    ) as (browser, gateway, _store, _runtime, graph, config, initial, _events):
        requests = []

        async def challenge_route(route):
            requests.append(route.request.url)
            await route.fulfill(
                content_type="text/html", body="<h1>Verify you are human</h1>"
            )

        await browser.context.route(
            "https://fixture.test/verification", challenge_route
        )
        result = await graph.ainvoke(initial, config)
        assert pending(result)["kind"] == "challenge"
        assert gateway.calls == gateway.reviews == 1
        again = await graph.ainvoke(Command(resume={"answer": "continue"}), config)
        assert pending(again)["kind"] == "challenge"
        assert gateway.calls == gateway.reviews == 1 and len(requests) == 1


def completed_with_quote(quote):
    def proposal(obs):
        return "finish", {
            "status": "completed",
            "summary": "The requested outcome is complete.",
            "claims": [
                {"claim": "Requested outcome", "evidence_id": obs["id"], "quote": quote}
            ],
            "remaining": [],
        }

    return proposal


def rejecting_completion(gateway, *, once=False, error=None):
    proposals = []

    async def review(task, proposal, evidence):
        gateway.completion_reviews += 1
        proposals.append((proposal, evidence))
        if error:
            raise error
        return {
            "supported": once and gateway.completion_reviews > 1,
            "reason": "Inspect the resulting receipt; the heading alone does not establish the requested outcome.",
        }

    gateway.verify_completion = review
    return proposals


async def test_completion_repair_inspects_receipt_without_replaying_effect(tmp_path):
    from test_graph_resume import FORM, approval_answer

    html = (
        FORM
        + '<button type="button" onclick="document.querySelector(\'#receipt\').hidden=false">View receipt</button><p id="receipt" hidden>Recorded for Acme: one application.</p>'
    )
    script = [
        click("Send application"),
        completed_with_quote("Application"),
        click("View receipt"),
        completed_with_quote("Recorded for Acme: one application."),
    ]
    async with graph_case(
        tmp_path, html=html, script=script, classification="ordinary"
    ) as (browser, gateway, _store, runtime, graph, config, initial, events):
        proposals = rejecting_completion(gateway, once=True)
        first = await graph.ainvoke(initial, config)
        done = await graph.ainvoke(approval_answer(first), config)
        assert done["result"]["status"] == "completed"
        assert done["completion_repairs"] == 1
        assert gateway.completion_reviews == 2
        assert await browser.page.evaluate("window.effects") == 1
        assert len([event for event in events if event[0] == "result"]) == 1
        assert proposals[1][0]["task_context"]["action_receipts"]
        assert "not proof" in proposals[1][0]["context_limitations"]
        assert any(
            "completion_accepted" in str(req["input"])
            and "resulting receipt" in str(req["input"])
            for req in gateway.requests
        )
        assert (runtime.run_dir / "result.json").exists()


async def test_completion_repair_can_finish_missing_work_after_approval_and_resume(
    tmp_path,
):
    from test_graph_resume import approval_answer

    script = [
        completed_with_quote("Application"),
        click("Send application"),
        completed_with_quote("Application recorded"),
    ]
    async with graph_case(tmp_path, script=script) as (
        browser,
        gateway,
        _store,
        runtime,
        graph,
        config,
        initial,
        _events,
    ):
        rejecting_completion(gateway, once=True)
        first = await graph.ainvoke(initial, config)
        assert pending(first)["kind"] == "approval"
        assert first["completion_repairs"] == 1
        assert not (runtime.run_dir / "result.json").exists()
        assert await browser.page.evaluate("window.effects || 0") == 0
        async with AsyncSqliteSaver.from_conn_string(
            str(tmp_path / "checkpoints.sqlite")
        ) as saver:
            reopened = runtime.compile(saver)
            done = await reopened.ainvoke(approval_answer(first), config)
        assert done["result"]["status"] == "completed"
        assert done["completion_repairs"] == 1
        assert await browser.page.evaluate("window.effects") == 1


async def test_perpetual_completion_rejection_exhausts_two_repairs(tmp_path):
    async with graph_case(
        tmp_path, script=[completed_with_quote("Application")] * 6
    ) as (browser, gateway, _store, _runtime, graph, config, initial, events):
        rejecting_completion(gateway)
        done = await graph.ainvoke(initial, config)
        assert done["result"]["status"] == "partial"
        assert "resulting receipt" in done["result"]["remaining"][0]
        assert done["result"]["summary"].startswith("Completion was not verified:")
        assert not done["result"]["claims"]
        assert gateway.completion_reviews == gateway.calls == 3
        assert done["completion_repairs"] == 2
        assert len([event for event in events if event[0] == "completion_repair"]) == 2
        assert await browser.page.evaluate("window.effects || 0") == 0


async def test_invalid_completion_quote_returns_feedback_before_review(tmp_path):
    async with graph_case(
        tmp_path,
        script=[
            completed_with_quote("Invented receipt"),
            completed_with_quote("Application"),
        ],
    ) as (_browser, gateway, _store, _runtime, graph, config, initial, _events):
        done = await graph.ainvoke(initial, config)
        assert done["result"]["status"] == "completed"
        assert gateway.completion_reviews == 1
        assert done["completion_repairs"] == 1
        assert "exact contiguous substring" in str(gateway.requests[-1]["input"])


async def test_completion_repair_stops_when_review_budget_is_unavailable(tmp_path):
    from browser_agent.storage import BudgetExceeded

    async with graph_case(
        tmp_path, script=[completed_with_quote("Application")] * 4
    ) as (_browser, gateway, _store, _runtime, graph, config, initial, _events):
        rejecting_completion(
            gateway, error=BudgetExceeded("Remaining task budget is insufficient")
        )
        done = await graph.ainvoke(initial, config)
        assert done["result"]["status"] == "partial"
        assert gateway.calls == gateway.completion_reviews == 1
        assert done.get("completion_repairs", 0) == 0
        assert "budget" in done["result"]["remaining"][0]


async def test_completion_repair_respects_existing_decision_limit(tmp_path):
    async with graph_case(
        tmp_path, script=[completed_with_quote("Application")] * 4
    ) as (_browser, gateway, _store, runtime, graph, config, initial, _events):
        runtime.settings.max_decisions = 1
        rejecting_completion(gateway)
        done = await graph.ainvoke(initial, config)
        assert done["result"]["status"] == "partial"
        assert gateway.calls == gateway.completion_reviews == 1
        assert done.get("completion_repairs", 0) == 0


async def test_completion_repair_keeps_duplicate_effect_admission_guard(tmp_path):
    from test_graph_resume import FORM, approval_answer

    # A server can accept a write without changing the visible form. Refs change,
    # but identical resolved effect data must still be admitted only once.
    html = FORM.replace(
        ";document.querySelector('output').textContent='Application recorded'", ""
    )
    script = [
        click("Send application"),
        completed_with_quote("Application"),
        click("Send application"),
        completed_with_quote("Application"),
    ]
    async with graph_case(tmp_path, html=html, script=script) as (
        browser,
        gateway,
        store,
        _runtime,
        graph,
        config,
        initial,
        events,
    ):
        rejecting_completion(gateway, once=True)
        first = await graph.ainvoke(initial, config)
        second = await graph.ainvoke(approval_answer(first), config)
        assert pending(second)["kind"] == "approval"
        done = await graph.ainvoke(approval_answer(second), config)
        assert done["result"]["status"] == "completed"
        assert await browser.page.evaluate("window.effects") == 1
        assert store.action(first["action_id"])["status"] == "observed"
        assert store.action(second["action_id"]) is None
        assert any("already dispatched" in str(event) for event in events)


async def test_completion_packet_recovers_prior_contents_after_compaction(tmp_path):
    pages = {
        str(
            i
        ): f'<h1>Document {i}</h1><article>Unique observed body {i}.</article><a href="https://example.invalid/docs/{i + 1}">Next</a>'
        for i in range(9)
    }
    script = [click("Next")] * 8 + [completed_with_quote("Document 8")]
    async with graph_case(
        tmp_path, html=pages["0"], script=script, classification="ordinary"
    ) as (_browser, gateway, _store, _runtime, graph, config, initial, _events):

        async def serve(route):
            await route.fulfill(
                body=pages[route.request.url.rsplit("/", 1)[1]],
                content_type="text/html",
            )

        await _browser.context.route("https://example.invalid/docs/**", serve)
        reviewed = []

        async def review(task, proposal, evidence):
            reviewed.append((proposal, evidence))
            return {
                "supported": all(
                    any(
                        f"Unique observed body {i}." in text
                        for text in evidence.values()
                    )
                    for i in range(9)
                ),
                "reason": "Every previously delivered document body must remain available for independent review.",
            }

        gateway.verify_completion = review
        initial["task"] = (
            "Inspect the nine documents, preserving their actual contents."
        )
        done = await graph.ainvoke(initial, config)
        assert done["result"]["status"] == "completed"
        assert gateway.memory_calls >= 2
        assert len(reviewed) == 1
        manifest = reviewed[0][0]["evidence_manifest"]
        assert len(manifest["sources"]) == 9
        assert all(
            source["provenance"] == "registered_browser_observation"
            for source in manifest["sources"].values()
        )
        assert all(source["saved_at_unix"] for source in manifest["sources"].values())


async def test_completion_packet_rejects_missing_content_despite_memory_claims(
    tmp_path,
):
    async with graph_case(
        tmp_path, script=[completed_with_quote("Application")] * 3
    ) as (_browser, gateway, _store, _runtime, graph, config, initial, _events):
        initial["notes"] = (
            "The requested document body was fully read: invented body text."
        )
        reviewed = []

        async def review(task, proposal, evidence):
            reviewed.append(evidence)
            return {
                "supported": any(
                    "invented body text" in text for text in evidence.values()
                ),
                "reason": "The requested body has no actual observed evidence.",
            }

        gateway.verify_completion = review
        done = await graph.ainvoke(initial, config)
        assert done["result"]["status"] == "partial"
        assert len(reviewed) == 3
        assert not done["result"]["claims"]
        assert all("invented body text" not in str(packet) for packet in reviewed)


async def test_completion_packet_caps_bytes_and_marks_omitted_or_truncated_sources(
    tmp_path,
):
    import json

    async with graph_case(tmp_path) as (
        _browser,
        _gateway,
        _store,
        runtime,
        _graph,
        _config,
        initial,
        _events,
    ):
        observations = [
            {
                "id": "obs-cited",
                "text": "Cited actual contents.",
                "url": "https://example.invalid/one",
                "title": "One",
                "truncated": False,
                "revision": 1,
            },
            {
                "id": "obs-scope",
                "text": "Original collection.",
                "url": "https://example.invalid/list",
                "title": "List",
                "truncated": False,
                "revision": 2,
            },
            {
                "id": "obs-large",
                "text": "界" * 4000,
                "url": "https://example.invalid/large",
                "title": "Large",
                "truncated": False,
                "revision": 3,
            },
            {
                "id": "obs-truncated",
                "text": "Visible partial body.",
                "url": "https://example.invalid/partial",
                "title": "Partial",
                "truncated": True,
                "next_offset": 50,
                "offset": 0,
                "revision": 4,
            },
        ]
        for observation in observations:
            runtime.save_observation(observation)
        state = initial | {
            "evidence_ids": [obs["id"] for obs in observations] + ["obs-missing"],
            "scope": {"items": [{"evidence_id": "obs-scope"}]},
            "visited": [{"evidence_id": "obs-large"}, {"evidence_id": "obs-truncated"}],
        }
        proposal = {"claims": [{"evidence_id": "obs-cited"}]}
        evidence, manifest = runtime.completion_packet(state, proposal, max_bytes=2000)
        assert (
            len(
                json.dumps(
                    {"evidence": evidence, "manifest": manifest}, ensure_ascii=False
                ).encode()
            )
            <= 2000
        )
        assert list(evidence)[:2] == ["obs-cited", "obs-scope"]
        assert "obs-large" not in evidence
        assert {"evidence_id": "obs-large", "reason": "packet_byte_limit"} in manifest[
            "omitted"
        ]
        assert {
            "evidence_id": "obs-missing",
            "reason": "saved_observation_unavailable",
        } in manifest["omitted"]
        assert manifest["sources"]["obs-truncated"]["snapshot_truncated"] is True
        assert manifest["sources"]["obs-truncated"]["next_offset"] == 50
        assert evidence["obs-truncated"] == "Visible partial body."


async def test_completion_packet_does_not_load_unregistered_files(tmp_path):
    async with graph_case(tmp_path) as (
        _browser,
        _gateway,
        _store,
        runtime,
        _graph,
        _config,
        initial,
        _events,
    ):
        runtime.save_observation(
            {
                "id": "obs-private",
                "text": "Not delivered to the actor",
                "url": "https://example.invalid/private",
            }
        )
        state = initial | {"scope": {"items": [{"evidence_id": "obs-private"}]}}
        evidence, manifest = runtime.completion_packet(
            state, {"claims": [{"evidence_id": "obs-private"}]}
        )
        assert evidence == {}
        assert manifest["sources"] == {}
        assert manifest["omitted"] == [
            {"evidence_id": "obs-private", "reason": "unregistered_observation"}
        ]


async def test_completion_problem_and_omissions_survive_recall_memory_and_resume(
    tmp_path,
):
    from test_graph_resume import approval_answer

    def recall_current(obs):
        return "recall", {"evidence_id": obs["id"], "offset": 0}

    script = [
        completed_with_quote("Application"),
        recall_current,
        recall_current,
        click("Send application"),
        completed_with_quote("Application recorded"),
    ]
    async with graph_case(tmp_path, script=script) as (
        browser,
        gateway,
        _store,
        runtime,
        graph,
        config,
        initial,
        _events,
    ):
        initial["evidence_ids"] = ["obs-missing"]
        rejecting_completion(gateway, once=True)
        first = await graph.ainvoke(initial, config)
        assert pending(first)["kind"] == "approval"
        assert first["completion_feedback"]["problems"]
        assert first["completion_feedback"]["omitted_evidence"] == [
            {"evidence_id": "obs-missing", "reason": "saved_observation_unavailable"}
        ]
        assert gateway.memory_calls >= 1
        for req in gateway.requests[1:]:
            assert any(
                isinstance(message.get("content"), str)
                and message["content"].startswith("Unresolved completion review")
                for message in req["input"]
            )
        async with AsyncSqliteSaver.from_conn_string(
            str(tmp_path / "checkpoints.sqlite")
        ) as saver:
            done = await runtime.compile(saver).ainvoke(approval_answer(first), config)
        assert done["result"]["status"] == "completed"
        assert done["completion_feedback"] is None
        assert await browser.page.evaluate("window.effects") == 1


@pytest.mark.parametrize(
    "corrupt", ["{}", "[]", "null", '{"id":"wrong","text":"Application"}', "not json"]
)
async def test_corrupt_completion_snapshot_is_rejected_without_uncaught_error(
    tmp_path, corrupt
):
    async with graph_case(tmp_path) as (
        _browser,
        gateway,
        _store,
        runtime,
        graph,
        config,
        initial,
        _events,
    ):

        def finish_with_corrupt_archive(obs):
            (runtime.run_dir / "evidence" / f"{obs['id']}.json").write_text(corrupt)
            return completed_with_quote("Application")(obs)

        gateway.script = [finish_with_corrupt_archive] * 3
        done = await graph.ainvoke(initial, config)
        assert done["result"]["status"] == "partial"
        assert gateway.completion_reviews == 0
        assert any(
            "invalid" in problem or "unavailable" in problem
            for problem in done["result"]["remaining"]
        )
