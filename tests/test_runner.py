"""Cancellation of the actual runner, with real Chromium and SQLite persistence."""

import asyncio
import json

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from playwright.async_api import Error as PlaywrightError

from browser_agent.browser import BrowserSession
from browser_agent.config import Settings
from browser_agent.runner import run_agent
from browser_agent.storage import Store
from tests.acceptance.test_graph_resume import ScriptedGateway, click

FORM = '<h1>Application</h1><form action="/submit" method="post"><input name="recipient" aria-label="Recipient" value="Acme"><textarea name="letter" aria-label="Letter">I have Python experience.</textarea><button>Send application</button></form>'


def factories(effects, entered=None, release=None):
    instances, gateways = [], []

    class FixtureBrowser(BrowserSession):
        async def start(self, url=None):
            await super().start()
            instances.append(self)

            async def route_fixture(route):
                if route.request.method == "POST":
                    effects.append(route.request.post_data)
                    if entered:
                        entered.set()
                    if release:
                        await release.wait()
                    try:
                        await route.fulfill(
                            content_type="text/html",
                            body="<h1>Application recorded</h1>",
                        )
                    except PlaywrightError:
                        if not self._closed and not self.page.is_closed():
                            raise
                else:
                    await route.fulfill(content_type="text/html", body=FORM)

            await self.context.route("https://fixture.test/**", route_fixture)
            await self.page.goto("https://fixture.test/form")

    def gateway_factory(*args, **kwargs):
        gateway = ScriptedGateway([click("Send application")])
        gateways.append(gateway)
        return gateway

    return FixtureBrowser, gateway_factory, instances, gateways


def events(settings, run_id):
    return [
        json.loads(line)
        for line in (settings.artifact_dir / "runs" / run_id / "events.jsonl")
        .read_text()
        .splitlines()
    ]


async def test_b12_cancel_at_approval_boundary_saves_checkpoint_and_resumes(tmp_path):
    settings = Settings(artifact_dir=tmp_path / "artifacts")
    effects = []
    browser_factory, gateway_factory, instances, gateways = factories(effects)
    waiting = asyncio.Event()
    questions = []

    async def wait_for_human(question):
        questions.append(question)
        waiting.set()
        await asyncio.Future()

    operation = asyncio.create_task(
        run_agent(
            settings,
            task="Submit my application",
            new_run_id="cancel-safe",
            profile="fixture",
            headless=True,
            responder=wait_for_human,
            browser_factory=browser_factory,
            gateway_factory=gateway_factory,
        )
    )
    await asyncio.wait_for(waiting.wait(), timeout=8)
    assert questions[0]["kind"] == "approval"
    assert effects == []
    operation.cancel()
    result = await asyncio.wait_for(operation, timeout=8)
    assert result == {"status": "cancelled", "run_id": "cancel-safe"}
    assert instances[0]._closed
    saved_events = events(settings, "cancel-safe")
    assert saved_events[-1]["event"] == "cancelled"
    assert saved_events[-1]["uncertain_actions"] == []
    async with AsyncSqliteSaver.from_conn_string(
        str(settings.artifact_dir / "state/checkpoints.sqlite")
    ) as saver:
        saved = await saver.aget_tuple({"configurable": {"thread_id": "cancel-safe"}})
        assert saved.checkpoint["channel_values"]["run_id"] == "cancel-safe"
        assert saved.checkpoint["channel_values"]["route"] == "approval"
    store = Store(settings.artifact_dir / "state/operations.sqlite")
    old_request = questions[0]["request_id"]
    store.reserve("cancel-safe", "prior-synthetic-reservation", 50)
    resumed_questions = []

    async def approve_fixture(question):
        resumed_questions.append(question)
        return {"request_id": question["request_id"], "approved": True}

    resumed = await run_agent(
        settings,
        run_id="cancel-safe",
        headless=True,
        responder=approve_fixture,
        browser_factory=browser_factory,
        gateway_factory=gateway_factory,
    )
    assert resumed["run_id"] == "cancel-safe"
    assert resumed["status"] == "partial"
    assert len(effects) == 1
    assert resumed_questions[0]["request_id"] != old_request
    assert store.approval(old_request)["status"] == "pending"
    assert store.budget("cancel-safe")["reserved"] == 50
    # Initial review schedules memory, then re-reviews the retained action. Resume
    # reuses that durable memory but still requires a fresh browser-generation review.
    assert gateways[0].reviews == 2
    assert gateways[1].reviews == 1
    assert gateways[0].memory_calls == 1
    assert gateways[1].memory_calls == 0


async def test_f19_cancel_inflight_preserves_uncertain_effect_and_never_replays(
    tmp_path,
):
    settings = Settings(artifact_dir=tmp_path / "artifacts")
    effects = []
    committed = asyncio.Event()
    release_response = asyncio.Event()
    browser_factory, gateway_factory, _instances, _gateways = factories(
        effects, committed, release_response
    )

    async def approve_fixture(question):
        if question["kind"] == "approval":
            return {"request_id": question["request_id"], "approved": True}
        return None

    operation = asyncio.create_task(
        run_agent(
            settings,
            task="Submit my application",
            new_run_id="cancel-inflight",
            profile="fixture",
            headless=True,
            responder=approve_fixture,
            browser_factory=browser_factory,
            gateway_factory=gateway_factory,
        )
    )
    try:
        await asyncio.wait_for(committed.wait(), timeout=8)
        store = Store(settings.artifact_dir / "state/operations.sqlite")
        assert len(effects) == 1
        assert store.unresolved_actions("cancel-inflight")[0]["status"] == "dispatched"
        operation.cancel()
        release_response.set()
        result = await asyncio.wait_for(operation, timeout=8)
        assert result == {"status": "cancelled", "run_id": "cancel-inflight"}
        unresolved = store.unresolved_actions("cancel-inflight")
        assert len(unresolved) == 1 and unresolved[0]["status"] == "uncertain"
        cancelled = [
            event
            for event in events(settings, "cancel-inflight")
            if event["event"] == "cancelled"
        ]
        assert cancelled[0]["uncertain_actions"][0]["id"] == unresolved[0]["id"]
        resumed = await run_agent(
            settings,
            run_id="cancel-inflight",
            headless=True,
            responder=approve_fixture,
            browser_factory=browser_factory,
            gateway_factory=gateway_factory,
        )
        assert resumed["status"] == "needs_user"
        assert resumed["question"]["kind"] == "uncertain"
        assert len(effects) == 1
        assert (
            store.unresolved_actions("cancel-inflight")[0]["id"] == unresolved[0]["id"]
        )
    finally:
        release_response.set()
        if not operation.done():
            operation.cancel()
            await operation
