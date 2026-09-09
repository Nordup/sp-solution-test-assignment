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
