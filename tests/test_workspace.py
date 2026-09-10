"""Browser workspace discovery and actor-selected lifecycle conformance."""

import json
import shutil
import socket
from uuid import uuid4

import pytest
from playwright.async_api import async_playwright

from browser_agent.agent import run_task
from browser_agent.browser import BrowserError, BrowserSession
from browser_agent.config import Settings
from browser_agent.workspace import BrowserWorkspace


def _free_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.fixture
async def external_cdp(tmp_path):
    if shutil.which("lsof") is None:
        pytest.skip("local browser discovery requires lsof")
    port = _free_port()
    playwright = await async_playwright().start()
    browser = await playwright.chromium.launch(
        headless=True,
        args=[f"--remote-debugging-port={port}", "--no-sandbox"],
    )
    page = await browser.new_page()
    await page.set_content(
        "<title>Workspace fixture</title><h1>Existing browser tab</h1>"
    )
    yield playwright, browser, page, port, tmp_path
    await browser.close()
    await playwright.stop()


@pytest.mark.asyncio
async def test_initial_workspace_is_empty_and_attach_preserves_external_browser(
    external_cdp,
):
    _playwright, external, page, _port, tmp_path = external_cdp
    workspace = BrowserWorkspace(tmp_path / "profiles", headless=True)
    try:
        initial = await workspace.observe()
        assert initial["no_browser"] is True
        assert initial["active_browser"] is None
        with pytest.raises(BrowserError) as missing:
            await workspace.attach_browser("d-missing")
        assert missing.value.code == "unknown_discovery"

        listed = await workspace.list_browsers()
        candidate = next(
            item
            for item in listed["discovered"]
            if any(tab["title"] == "Workspace fixture" for tab in item["tabs"])
        )
        attached = await workspace.attach_browser(candidate["discovery_id"])
        assert attached["ownership"] == "attached"
        observed = await workspace.observe()
        assert observed["title"] == "Workspace fixture"
        assert observed["active_browser"] == attached["browser_id"]

        # Page tools delegate to the selected external browser and retain tab identity.
        action = await workspace.action_context("new_tab", {}, observed["id"])
        opened = await workspace.execute(
            "new_tab", {}, observed["id"], expected_fingerprint=action["fingerprint"]
        )
        assert len(opened["tabs"]) == 2
        observed = await workspace.observe()
        second = next(tab for tab in observed["tabs"] if tab["active"])
        await workspace.execute("switch_tab", {"page_id": second["page_id"]}, observed["id"])
        observed = await workspace.observe()
        await workspace.execute(
            "close_tab", {"page_id": second["page_id"]}, observed["id"]
        )
        observed = await workspace.observe()
        assert len(observed["tabs"]) == 1 and observed["title"] == "Workspace fixture"

        # The actor can keep several managed browsers and switch or detach an attachment.
        owned = await workspace.launch_browser()
        assert owned["ownership"] == "owned"
        assert (await workspace.switch_browser(attached["browser_id"]))["browser_id"] == attached[
            "browser_id"
        ]
        assert (await workspace.switch_browser(owned["browser_id"]))["browser_id"] == owned[
            "browser_id"
        ]
        await workspace.switch_browser(attached["browser_id"])
        detached = await workspace.detach_browser()
        assert detached["browser_id"] == attached["browser_id"]
        assert attached["browser_id"] not in workspace._managed
        assert owned["browser_id"] in workspace._managed
    finally:
        await workspace.close()

    # Closing the agent workspace disconnects CDP without closing the external process.
    assert not page.is_closed()
    assert await page.title() == "Workspace fixture"
    await external.close()


@pytest.mark.asyncio
async def test_stale_and_nonlocal_attachment_are_refused(external_cdp):
    _playwright, external, _page, _port, tmp_path = external_cdp
    workspace = BrowserWorkspace(tmp_path / "profiles", headless=True)
    listed = await workspace.list_browsers()
    candidate = next(
        item
        for item in listed["discovered"]
        if any(tab["title"] == "Workspace fixture" for tab in item["tabs"])
    )
    discovery_id = candidate["discovery_id"]
    await external.close()
    with pytest.raises(BrowserError) as stale:
        await workspace.attach_browser(discovery_id)
    assert stale.value.code == "unknown_discovery"
    await workspace.close()

    session = BrowserSession(tmp_path / "invalid")
    with pytest.raises(BrowserError) as nonlocal_error:
        await session.attach("https://example.test:9222")
    assert nonlocal_error.value.code == "invalid_endpoint"


@pytest.mark.asyncio
async def test_owned_launch_starts_blank_and_closes_owned_browser(tmp_path):
    workspace = BrowserWorkspace(tmp_path / "profiles", headless=True)
    summary = await workspace.launch_browser()
    managed = workspace._managed[summary["browser_id"]]
    assert summary["ownership"] == "owned"
    assert managed.session.page.url == "about:blank"
    session = managed.session
    await workspace.close()
    assert not session.is_open and session.context is None


class _LifecycleGateway:
    def __init__(self):
        self.calls = 0
        self.cost_usd = 0
        self.closed = False

    async def call(self, request):
        self.calls += 1
        text = request["input"][-1]["content"][0]["text"]
        observation = json.loads(
            text.split("Current browser observation:\n", 1)[1].split(
                "\nRuntime feedback:", 1
            )[0]
        )
        if self.calls == 1:
            assert observation["no_browser"] is True
        else:
            assert observation.get("no_browser") is None
        name, args = (
            ("launch_browser", {})
            if self.calls == 1
            else (
                "finish",
                {
                    "status": "completed",
                    "summary": "Selected an owned browser.",
                    "remaining": [],
                },
            )
        )
        return {
            "status": "completed",
            "output": [
                {
                    "type": "function_call",
                    "name": name,
                    "call_id": str(uuid4()),
                    "arguments": json.dumps(
                        {"notebook": "Browser lifecycle facts.", **args}
                    ),
                }
            ],
        }

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_langgraph_can_choose_launch_from_no_browser_observation(tmp_path):
    workspace = BrowserWorkspace(tmp_path / "profiles", headless=True)
    gateway = _LifecycleGateway()
    try:
        result = await run_task(
            Settings(artifact_dir=tmp_path),
            "Open a browser and report its blank state",
            workspace,
            gateway_factory=lambda _settings, **_kwargs: gateway,
        )
        assert result["status"] == "completed"
        assert gateway.calls == 2 and gateway.closed
        assert workspace.active_id is not None
    finally:
        await workspace.close()
