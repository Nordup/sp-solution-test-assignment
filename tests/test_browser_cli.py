"""Regression coverage for the official Playwright CLI transport."""

from __future__ import annotations

import asyncio
import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import pytest_asyncio

from browser_agent.browser_cli import BrowserError, PlaywrightCLI
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
    session = PlaywrightCLI(settings, session_name=f"test-{tmp_path.name}")
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
    saved_path = browser._extract_paths(saved["output"])[-1]
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
    filled_snapshot = await browser.execute("playwright", {"command": "snapshot", "args": []})
    assert _item(filled_snapshot["output"], "Country").get("text") == "Armenia"
    click = await browser.execute("playwright", {"command": "click", "args": [send]})
    assert click["status"] == "executed"

    after = await browser.execute("playwright", {"command": "snapshot", "args": []})
    def contains_clicked(items):
        return any(
            isinstance(item, dict)
            and (item.get("text") == "Clicked" or contains_clicked(item.get("children", [])))
            for item in items
        )

    assert contains_clicked(after["output"]["snapshot"])

    assert not list(browser.artifact_dir.glob("*.png"))
    screenshot = await browser.execute("playwright", {"command": "screenshot", "args": []})
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
async def test_cli_rejects_hidden_commands_and_preserves_raw_cli_errors(browser):
    rejected = await browser.execute(
        "playwright", {"command": "run-code", "args": ["page => page.title()"]}
    )
    assert rejected["status"] == "error"
    assert rejected["error"]["code"] == "unsupported_command"

    failed = await browser.execute("playwright", {"command": "click", "args": ["bogus"]})
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


def test_cli_screenshot_output_does_not_erase_page_evidence(tmp_path):
    browser = PlaywrightCLI(_settings(tmp_path), session_name="evidence-retention")
    browser._update_evidence(
        "snapshot",
        {"result": {"snapshot": [{"ref": "e5", "role": "button", "name": "Delete"}]}},
    )
    assert "Delete" in browser.evidence and "e5" in browser.evidence

    browser._update_evidence(
        "screenshot",
        {"result": "- [Screenshot of viewport](screenshot.png)"},
    )
    assert "Delete" in browser.evidence and "e5" in browser.evidence
    assert "screenshot.png" in browser.evidence


class _FakeProcess:
    def __init__(self, stdout=b"{}", stderr=b"", *, wait_for_release=False):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = 0
        self.wait_for_release = wait_for_release
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.signaled = False
        self.waited = False

    async def communicate(self):
        self.started.set()
        if self.wait_for_release:
            await self.release.wait()
        return self.stdout, self.stderr

    def send_signal(self, _signal):
        self.signaled = True
        self.release.set()

    def kill(self):
        self.signaled = True
        self.release.set()

    async def wait(self):
        self.waited = True
        self.release.set()
        return self.returncode


def _settings(tmp_path):
    return Settings(artifact_dir=tmp_path, browser_headed=False)


@pytest.mark.asyncio
async def test_cli_start_only_prepares_and_child_task_can_execute_and_close(monkeypatch, tmp_path):
    calls = []
    process = _FakeProcess(stdout=json.dumps({"result": "ok"}).encode())

    async def create(*argv, **kwargs):
        calls.append((argv, kwargs))
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    browser = PlaywrightCLI(_settings(tmp_path), session_name="fake-session")
    await browser.start()
    assert calls == []
    result = await asyncio.create_task(
        browser.execute("playwright", {"command": "open", "args": ["about:blank"]})
    )
    assert result["status"] == "executed"
    await asyncio.create_task(browser.close())
    assert len(calls) == 2 and calls[-1][0][4] == "close"


def test_cli_help_argv_and_host_flag_boundaries(tmp_path):
    browser = PlaywrightCLI(_settings(tmp_path), session_name="argv")
    assert browser._argv("help", [])[-2:] == ["--help", "--json"]
    browser._validate_model_args("mousewheel", ["0", "-500"])
    browser._validate_model_args("attach", ["--cdp", "http://127.0.0.1:9222"])
    browser._validate_model_args("attach", ["--extension=chrome"])
    with pytest.raises(BrowserError):
        browser._validate_model_args("open", ["--profile=/tmp/other"])
    with pytest.raises(BrowserError):
        browser._validate_model_args("open", ["--config=/tmp/config.json"])


@pytest.mark.asyncio
@pytest.mark.parametrize("stdout", [b"", b"not json"])
async def test_cli_malformed_json_is_an_uncertain_transport_error(
    monkeypatch, tmp_path, stdout
):
    process = _FakeProcess(stdout=stdout)

    async def create(*_argv, **_kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    browser = PlaywrightCLI(_settings(tmp_path), session_name="bad-json")
    await browser.start()
    result = await browser.execute("playwright", {"command": "tab-list", "args": []})
    assert result["status"] == "error"
    assert result["error"]["code"] == "cli_parse_error"
    assert result["error"]["uncertain"] is True


@pytest.mark.asyncio
async def test_cli_missing_screenshot_artifact_is_error(monkeypatch, tmp_path):
    browser = PlaywrightCLI(_settings(tmp_path), session_name="no-shot")
    await browser.start()

    async def no_file(_command, _args):
        return {"result": "- [Screenshot of viewport](missing.png)"}

    monkeypatch.setattr(browser, "_run_cli", no_file)
    result = await browser.execute("playwright", {"command": "screenshot", "args": []})
    assert result["status"] == "error"
    assert result["error"]["code"] == "screenshot_missing"


@pytest.mark.asyncio
async def test_cli_cancellation_reaps_subprocess_and_keeps_private_logs(monkeypatch, tmp_path):
    process = _FakeProcess(stdout=b"partial", stderr=b"diagnostic", wait_for_release=True)

    async def create(*_argv, **_kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    browser = PlaywrightCLI(_settings(tmp_path), session_name="cancel")
    await browser.start()
    task = asyncio.create_task(
        browser.execute("playwright", {"command": "tab-list", "args": []})
    )
    await process.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert process.signaled and process.waited
    assert list(browser.browser_session_dir.glob("tab-list-*-stdout.log"))
    assert list(browser.browser_session_dir.glob("tab-list-*-stderr.log"))
