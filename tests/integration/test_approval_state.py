"""Real Chrome verifies approval invalidation and native-dialog consent."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

import pytest
import pytest_asyncio

from browser_agent.agent import run_task
from browser_agent.browser import BrowserError, PlaywrightCLI
from browser_agent.config import Settings
from tests.integration.test_agent import ScriptedModel


@pytest_asyncio.fixture
async def approval_browser(tmp_path):
    effects = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            body = b"""<!doctype html><title>Approval test</title>
<label>Recipient<select id="recipient"><option>Alice</option><option>Bob</option></select></label>
<label>Amount<input id="amount" value="10"></label>
<button onclick="send()">Send record</button>
<button onclick="confirm('Delete the disposable record?') && send()">Confirm deletion</button>
<script>function send(){fetch('/effect',{method:'POST',body:JSON.stringify({
recipient:document.querySelector('#recipient').value,
amount:document.querySelector('#amount').value})})}</script>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            effects.append(
                json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            )
            self.send_response(204)
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    (tmp_path / ".playwright").mkdir()
    settings = Settings(artifact_dir=tmp_path, browser_headed=False)
    browser = PlaywrightCLI(settings, session_name=f"approval-{uuid4().hex}")
    await browser.start()
    url = f"http://127.0.0.1:{server.server_port}/"
    try:
        opened = await browser.execute("playwright", {"command": "open", "args": [url]})
        assert opened["status"] == "executed", opened
        yield browser, effects, url
    finally:
        await browser.close()
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def reference(value, label):
    if isinstance(value, dict):
        if value.get("name") == label and "ref" in value:
            return value["ref"]
        children = value.values()
    elif isinstance(value, list):
        children = value
    else:
        return None
    return next((ref for child in children if (ref := reference(child, label))), None)


def finish():
    return "finish", {"status": "partial", "summary": "Probe ended.", "remaining": []}


@pytest.mark.parametrize("change", [None, "recipient", "amount", "tab"])
async def test_approval_only_dispatches_unchanged_page(approval_browser, change):
    browser, effects, url = approval_browser
    snapshot = await browser.execute("playwright", {"command": "snapshot", "args": []})
    ref = reference(snapshot, "Send record")
    assert ref
    model = ScriptedModel(
        [("playwright", {"command": "click", "args": [ref]}), finish()],
        security=[True],
    )
    questions = []

    async def respond(question):
        questions.append(question)
        if change == "tab":
            await browser.execute("playwright", {"command": "tab-new", "args": [url]})
        elif change:
            value = "Bob" if change == "recipient" else "999"
            await browser.execute(
                "playwright",
                {
                    "command": "eval",
                    "args": [
                        f"() => document.getElementById('{change}').value = '{value}'"
                    ],
                },
            )
        return {"request_id": question["request_id"], "approved": True}

    await run_task(
        browser.settings,
        "Send to Alice for 10.",
        browser,
        responder=respond,
        model_factory=lambda *_a, **_k: model,
    )
    assert len(questions) == 1
    if change:
        assert effects == []
        assert "approval_state_changed" in json.dumps(model.requests[-1])
    else:
        assert effects == [{"recipient": "Alice", "amount": "10"}]


async def test_unavailable_approval_state_never_dispatches(
    approval_browser, monkeypatch
):
    browser, effects, _url = approval_browser
    snapshot = await browser.execute("playwright", {"command": "snapshot", "args": []})
    ref = reference(snapshot, "Send record")
    model = ScriptedModel(
        [("playwright", {"command": "click", "args": [ref]}), finish()], security=[True]
    )

    async def unavailable():
        raise BrowserError("approval_state_unavailable", "Cannot inspect the page.")

    monkeypatch.setattr(browser, "approval_state", unavailable)
    await run_task(
        browser.settings,
        "Send a record.",
        browser,
        model_factory=lambda *_a, **_k: model,
    )
    assert effects == []
    assert "approval_state_unavailable" in json.dumps(model.requests[-1])


@pytest.mark.parametrize("accept", [False, True])
async def test_native_dialog_requires_manual_browser_action(approval_browser, accept):
    browser, effects, _url = approval_browser
    snapshot = await browser.execute("playwright", {"command": "snapshot", "args": []})
    ref = reference(snapshot, "Confirm deletion")
    await browser.execute("playwright", {"command": "click", "args": [ref]})
    model = ScriptedModel(
        [("playwright", {"command": "dialog-accept", "args": []}), finish()],
        security=[False],
    )
    questions = []

    async def handle_manually(question):
        questions.append(question)
        assert question["kind"] == "challenge"
        await browser.execute(
            "playwright",
            {"command": "dialog-accept" if accept else "dialog-dismiss", "args": []},
        )
        return "ready"

    await run_task(
        browser.settings,
        "Confirm the deletion.",
        browser,
        responder=handle_manually,
        model_factory=lambda *_a, **_k: model,
    )
    assert len(questions) == 1
    assert len(effects) == int(accept)
    assert "security" not in model.purposes
    assert "manual_step" in json.dumps(model.requests[-1])


@pytest.mark.parametrize("native_error", [True, False])
async def test_uncertain_approved_effect_stops_before_any_retry(
    approval_browser, monkeypatch, native_error
):
    browser, effects, _url = approval_browser
    snapshot = await browser.execute("playwright", {"command": "snapshot", "args": []})
    ref = reference(snapshot, "Send record")
    command = ("playwright", {"command": "click", "args": [ref]})
    model = ScriptedModel([command, command, finish()], security=[True, True])
    original = browser.execute

    async def lose_response(tool, args):
        result = await original(tool, args)
        if args.get("command") == "click":
            error = BrowserError(
                "connection_lost", "Response was lost.", uncertain=True
            )
            if native_error:
                return {"status": "error", "error": error.as_dict()}
            raise error
        return result

    async def approve(question):
        return {"request_id": question["request_id"], "approved": True}

    monkeypatch.setattr(browser, "execute", lose_response)
    result = await run_task(
        browser.settings,
        "Send exactly once.",
        browser,
        responder=approve,
        model_factory=lambda *_a, **_k: model,
    )
    assert effects == [{"recipient": "Alice", "amount": "10"}]
    assert len(model.requests) == 1
    assert result["status"] == "partial"
    assert "may already have taken effect" in result["summary"]
    assert (await original("playwright", {"command": "snapshot", "args": []}))[
        "status"
    ] == "executed"
