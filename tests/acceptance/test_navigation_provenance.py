"""Navigation provenance with actual SQLite reopen and Chromium; no paid calls."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from browser_agent.browser import BrowserSession
from browser_agent.config import Settings
from browser_agent.context import INITIAL_URL_BYTES, ContextOverflow, request
from browser_agent.graph import AgentGraph, navigation_destination_known
from browser_agent.runner import run_agent
from browser_agent.storage import Store
from browser_agent.tools import tool_specs
from tests.acceptance.test_graph_resume import ScriptedGateway, graph_case


@pytest.mark.parametrize(
    "source,target,allowed",
    [
        ("Visit HTTPS://Example.COM:443", "https://example.com/", True),
        (
            "[site](https://example.com/path?a=1#part)",
            "https://example.com/path?a=1#part",
            True,
        ),
        ("https://example.com/longer", "https://example.com/", False),
        ("https://example.com.evil.test/", "https://example.com/", False),
        ("https://example.com/?next=https://evil.test/", "https://evil.test/", False),
        ("https://example.com/path?a=1", "https://example.com/path?a=2", False),
        ("https://example.com/path#one", "https://example.com/path#two", False),
        ("https://example.com/A", "https://example.com/a", False),
        ("https://example.com/%2Fadmin", "https://example.com//admin", False),
        ("https://example.com/", "https://example.com\\@evil.test/", False),
        (
            "https://user:password@example.com/",
            "https://user:password@example.com/",
            False,
        ),
        ("https://example.com:70000/", "https://example.com:70000/", False),
        ("https://example.com/", "https://example.com/\n", False),
    ],
)
def test_navigation_requires_exact_conservative_url_identity(source, target, allowed):
    assert navigation_destination_known(target, {"task": source}) is allowed


@pytest.mark.parametrize("field", ["feedback", "notes", "history", "visited"])
async def test_untrusted_model_or_tool_text_never_grants_navigation(tmp_path, field):
    destination = "https://unobserved.test/target"
    browser = SimpleNamespace(action_context=AsyncMock())
    gateway = SimpleNamespace(review=AsyncMock())
    runtime = AgentGraph(
        browser,
        gateway,
        Store(tmp_path / "db.sqlite"),
        Settings(artifact_dir=tmp_path),
        tmp_path / "run",
        lambda *_: None,
    )
    state = {
        "run_id": "boundary",
        "task": "Read the current page",
        field: destination,
        "action": {"tool": "navigate", "args": {"url": destination}},
        "observation": {
            "id": "current",
            "url": "https://observed.test/",
            "text": "No links",
            "tabs": [],
        },
    }
    outcome = await runtime.policy(state)
    assert outcome["route"] == "recover"
    assert "Destination must come from the user" in outcome["feedback"]
    browser.action_context.assert_not_called()
    gateway.review.assert_not_called()


@pytest.mark.parametrize(
    "observation",
    [
        {"url": "https://observed.test/"},
        {"tabs": [{"url": "https://observed.test/"}]},
        {"text": '- link "Observed"\n  - /url: https://observed.test/'},
    ],
)
def test_current_browser_urls_remain_observed_sources(observation):
    assert navigation_destination_known(
        "https://observed.test/", {"observation": observation}
    )


@pytest.mark.parametrize(
    "source", ["initial_url", "clarification", "legacy_clarification"]
)
async def test_user_url_survives_two_sqlite_reopens_and_feedback_replacement(
    tmp_path, source
):
    settings = Settings(artifact_dir=tmp_path / "artifacts")
    destination = "https://destination.test/target"
    initial = (
        destination if source == "initial_url" else "https://navigation.test/start"
    )
    reached = []
    gateways = []

    class LocalBrowser(BrowserSession):
        async def start(self, url=None):
            await super().start()

            async def serve(route):
                reached.append(route.request.url)
                await route.fulfill(
                    content_type="text/html", body="<h1>Local boundary test</h1>"
                )

            await self.context.route("**/*", serve)
            # A reopened browser starts elsewhere; it cannot supply the target
            # through current page text, URL or tabs by accident.
            await self.page.goto("https://navigation.test/start")

    scripts = [
        [lambda _: ("ask_user", {"kind": "login", "question": "Provide the site."})],
        [
            lambda _: (
                "ask_user",
                {"kind": "login", "question": "Confirm you are ready."},
            )
        ],
        [lambda _: ("navigate", {"url": destination})],
    ]

    def gateway_factory(*_, **__):
        gateway = ScriptedGateway(scripts.pop(0), classification="ordinary")
        gateways.append(gateway)
        return gateway

    common = {
        "settings": settings,
        "headless": True,
        "browser_factory": LocalBrowser,
        "gateway_factory": gateway_factory,
    }
    first = await run_agent(
        **common,
        task="Open the supplied site and inspect it.",
        url=initial,
        new_run_id="url-resume",
        profile="local",
    )
    assert first["status"] == "needs_user"
    run_config = settings.artifact_dir / "runs/url-resume/run.json"
    saved = json.loads(run_config.read_text())
    assert saved["initial_url"] == initial
    if source == "legacy_clarification":
        saved.pop("initial_url")
        run_config.write_text(json.dumps(saved))

    replies = iter(
        [{"answer": destination if source != "initial_url" else "Ready"}, None]
    )

    async def first_answer(_):
        return next(replies)

    second = await run_agent(**common, run_id="url-resume", responder=first_answer)
    assert second["status"] == "needs_user"

    async def second_answer(_):
        return {"answer": "Continue now; the earlier information still applies."}

    final = await run_agent(**common, run_id="url-resume", responder=second_answer)
    assert (
        final["status"] == "partial"
    )  # Scripted boundary-test final; no task-success claim.
    assert reached.count(destination) == 1
    async with AsyncSqliteSaver.from_conn_string(
        str(settings.artifact_dir / "state/checkpoints.sqlite")
    ) as saver:
        checkpoint = await saver.aget_tuple(
            {"configurable": {"thread_id": "url-resume"}}
        )
        state = checkpoint.checkpoint["channel_values"]
    assert state["initial_url"] == initial
    for gateway in gateways:
        assert gateway.requests
        assert all(
            {"role": "user", "content": "User-supplied starting URL:\n" + initial}
            in native_request["input"]
            for native_request in gateway.requests
        )
    assert (
        state["clarifications"][-1]
        == "Continue now; the earlier information still applies."
    )
    if source != "initial_url":
        assert destination in state["clarifications"]
    events = [
        json.loads(line)
        for line in (settings.artifact_dir / "runs/url-resume/events.jsonl")
        .read_text()
        .splitlines()
    ]
    assert any(
        event.get("event") == "tool_result"
        and event.get("result", {}).get("tool") == "navigate"
        for event in events
    )


def test_initial_url_is_available_before_any_successful_browser_observation():
    url = "https://example.test/start?keep=Exact%2FValue#section"
    state = {"task": "Inspect the supplied site", "initial_url": url}
    native = request(state, tool_specs())
    assert {"role": "user", "content": "User-supplied starting URL:\n" + url} in native[
        "input"
    ]
    with pytest.raises(ContextOverflow, match="no URL was truncated"):
        request(state | {"initial_url": url + "x" * INITIAL_URL_BYTES}, tool_specs())


@pytest.mark.parametrize("forged_quote", [False, True])
async def test_initial_url_is_groundable_clarification_source(tmp_path, forged_quote):
    url = "https://user-supplied.test/start"
    script = [
        lambda _: (
            "ask_user",
            {"kind": "clarification", "question": "Which site should I use?"},
        )
    ]
    async with graph_case(tmp_path, script=script) as (
        _,
        gateway,
        _,
        _,
        graph,
        config,
        initial,
        _,
    ):
        initial["initial_url"] = url
        calls = []

        async def review(task, question, context, evidence):
            calls.append(context)
            return {
                "classification": "already_available",
                "reason": "The user supplied the starting URL.",
                "evidence": [
                    {
                        "source_id": "user_initial_url",
                        "quote": "https://invented.test/" if forged_quote else url,
                    }
                ],
            }

        gateway.review_clarification = review
        result = await graph.ainvoke(initial, config)
        assert calls[0]["user_sources"]["user_initial_url"] == url
        if forged_quote:
            assert "could not ground" in result["__interrupt__"][0].value["question"]
        else:
            assert result["clarification_repairs"] == 1
            assert "__interrupt__" not in result
