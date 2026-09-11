"""Real Playwright CLI coverage against a local HTTP page."""

from __future__ import annotations

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

import pytest
import pytest_asyncio

from browser_agent.browser import PlaywrightCLI
from browser_agent.browser.artifacts import extract_paths
from browser_agent.config import Settings


class _FixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"""
<!doctype html><html><head><title>CLI fixture</title></head><body>
<main><h1>Country form</h1>
<label for="country">Country</label><input id="country" aria-label="Country">
<button id="send" onclick="document.querySelector('#status').textContent='Clicked';">Send</button>
<p id="status">Not clicked</p></main>
</body></html>
"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        return


@pytest.fixture(scope="module")
def fixture_url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FixtureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/"
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


@pytest_asyncio.fixture
async def browser(tmp_path, fixture_url):
    settings = Settings(artifact_dir=tmp_path, browser_headed=False)
    session = PlaywrightCLI(settings, session_name=f"test-{uuid4().hex}")
    await session.start()
    navigation = await session.execute(
        "playwright", {"command": "open", "args": [fixture_url]}
    )
    assert navigation["status"] == "executed"
    try:
        yield session
    finally:
        await session.close()


def _ref(snapshot, label):
    def walk(items):
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("name") == label:
                return item["ref"]
            found = walk(item.get("children", []))
            if found:
                return found
        return None

    result = walk(snapshot["snapshot"])
    assert result, snapshot
    return result


def _item(snapshot, label):
    def walk(items):
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("name") == label:
                return item
            found = walk(item.get("children", []))
            if found:
                return found
        return None

    result = walk(snapshot["snapshot"])
    assert result, snapshot
    return result


@pytest.mark.asyncio
async def test_cli_navigates_fills_clicks_and_screenshots(browser):
    snapshot = await browser.execute("playwright", {"command": "snapshot", "args": []})
    assert snapshot["status"] == "executed"
    page_snapshot = snapshot["output"]
    assert page_snapshot["snapshot"]
    saved = await browser.execute(
        "playwright",
        {"command": "snapshot", "args": ["--filename=initial.yml"]},
    )
    saved_path = extract_paths(saved["output"])[-1]
    saved_artifact = await browser.execute(
        "read_browser_artifact", {"path": saved_path, "offset": 0}
    )
    assert saved_artifact["status"] == "read" and "Country" in saved_artifact["text"]
    country = _ref(page_snapshot, "Country")
    send = _ref(page_snapshot, "Send")

    fill = await browser.execute(
        "playwright", {"command": "fill", "args": [country, "Armenia"]}
    )
    assert fill["status"] == "executed"
    filled_snapshot = await browser.execute(
        "playwright", {"command": "snapshot", "args": []}
    )
    assert _item(filled_snapshot["output"], "Country").get("text") == "Armenia"
    click = await browser.execute("playwright", {"command": "click", "args": [send]})
    assert click["status"] == "executed"
    # The CLI click result contains a fresh snapshot artifact.  The transport
    # must make its post-click warning/state available to the reviewer without
    # requiring an automatic follow-up browser call.
    assert "Clicked" in browser.evidence

    after = await browser.execute("playwright", {"command": "snapshot", "args": []})

    def contains_clicked(items):
        return any(
            isinstance(item, dict)
            and (
                item.get("text") == "Clicked"
                or contains_clicked(item.get("children", []))
            )
            for item in items
        )

    assert contains_clicked(after["output"]["snapshot"])

    assert not list(browser.artifact_dir.glob("*.png"))
    screenshot = await browser.execute(
        "playwright", {"command": "screenshot", "args": []}
    )
    assert screenshot["status"] == "executed"
    content = screenshot["content"]
    assert content and content[0]["type"] == "image"
    assert content[0]["mimeType"] == "image/png"
    assert base64.b64decode(content[0]["data"])
    image_path = next(browser.artifact_dir.glob("*.png"))
    image_read = await browser.execute(
        "read_browser_artifact", {"path": str(image_path), "offset": 0}
    )
    assert image_read["status"] == "read"


@pytest.mark.asyncio
async def test_cli_eval_reads_observed_element(browser):
    snapshot = await browser.execute("playwright", {"command": "snapshot", "args": []})
    heading = _ref(snapshot["output"], "Country form")
    evaluated = await browser.execute(
        "playwright",
        {"command": "eval", "args": ["(element) => element.textContent", heading]},
    )
    assert evaluated["status"] == "executed"
    assert "Country form" in json.dumps(evaluated, ensure_ascii=False)


@pytest.mark.asyncio
async def test_cli_rejects_hidden_commands_and_preserves_raw_cli_errors(browser):
    rejected = await browser.execute(
        "playwright", {"command": "run-code", "args": ["page => page.title()"]}
    )
    assert rejected["status"] == "error"
    assert rejected["error"]["code"] == "unsupported_command"

    failed = await browser.execute(
        "playwright", {"command": "click", "args": ["bogus"]}
    )
    assert failed["status"] == "error"
    assert failed["error"]["code"] == "cli_error"
    assert "does not match any elements" in failed["error"]["message"]


@pytest.mark.asyncio
async def test_cli_evidence_is_bounded_and_prepare_task_does_not_navigate(browser):
    snapshot = await browser.execute("playwright", {"command": "snapshot", "args": []})
    assert snapshot["status"] == "executed"
    assert browser.evidence and len(browser.evidence) <= 1_000_000
    await browser.prepare_task()
    assert browser.evidence == ""
