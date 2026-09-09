"""Real StateGraph + SQLite checkpoints + Chromium; fake only the paid gateway.

The deterministic actor intentionally chooses boundary cases. These tests prove
runtime behavior, not autonomous model quality (covered by paid evaluations).
"""

import json
import re
from contextlib import asynccontextmanager
from uuid import uuid4

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from browser_agent.browser import BrowserSession
from browser_agent.config import Settings
from browser_agent.graph import AgentGraph
from browser_agent.storage import Store

FORM = """<h1>Application</h1><form action="https://fixture.test/apply" method="post" onsubmit="event.preventDefault();window.effects=(window.effects||0)+1;document.querySelector('output').textContent='Application recorded'"><input aria-label="Recipient" value="Acme"><textarea aria-label="Letter">I have Python experience.</textarea><button type="submit">Send application</button></form><output></output>"""


def choose_ref(obs, label):
    line = next(
        line
        for line in obs["text"].splitlines()
        if f'"{label}"' in line and "[ref=" in line
    )
    return re.search(r"\[ref=([^\]]+)\]", line).group(1)


def click(label):
    return lambda obs: ("click", {"ref": choose_ref(obs, label)})


def finish(obs):
    return "finish", {
        "status": "partial",
        "summary": "Boundary test finished.",
        "claims": [],
        "remaining": ["This is a deterministic boundary test."],
    }


class ScriptedGateway:
    def __init__(self, script=(), classification="consequential"):
        self.script = list(script)
        self.classification = classification
        self.calls = 0
        self.requests = []
        self.reviews = 0
        self.completion_reviews = 0

    async def call(self, req):
        self.calls += 1
        self.requests.append(req)
        current = next(
            item["content"]
            for item in reversed(req["input"])
            if isinstance(item.get("content"), str)
            and item["content"].startswith("Current browser observation")
        )
        obs = json.loads(current.split("\n", 1)[1])
        name, args = (self.script.pop(0) if self.script else finish)(obs)
        return {
            "status": "completed",
            "output": [
                {
                    "type": "function_call",
                    "name": name,
                    "call_id": uuid4().hex,
                    "arguments": json.dumps(args),
                }
            ],
        }

    async def review(self, task, action, metadata):
        self.reviews += 1
        return {
            "classification": self.classification,
            "effect_summary": "Submit the displayed application to Acme with the displayed letter.",
            "reason": "Deterministic risk classification for testing the real policy.",
        }

    async def verify_completion(self, task, result, evidence):
        self.completion_reviews += 1
        return {"supported": True, "reason": "Synthetic completion review"}


@asynccontextmanager
async def graph_case(tmp_path, html=FORM, script=(), classification="consequential"):
    browser = BrowserSession(
        tmp_path / "profile", headless=True, artifact_dir=tmp_path / "evidence"
    )
    await browser.start()
    await browser.page.set_content(html)
    store = Store(tmp_path / "operations.sqlite")
    run_id = "boundary-" + uuid4().hex
    store.create_budget(run_id, 5_000_000)
    gateway = ScriptedGateway(script, classification)
    settings = Settings(artifact_dir=tmp_path, max_decisions=15)
    events = []
    runtime = AgentGraph(
        browser,
        gateway,
        store,
        settings,
        tmp_path / "run",
        lambda name, data: events.append((name, data)),
    )
    try:
        async with AsyncSqliteSaver.from_conn_string(
            str(tmp_path / "checkpoints.sqlite")
        ) as saver:
            graph = runtime.compile(saver)
            config = {"configurable": {"thread_id": run_id}, "recursion_limit": 200}
            initial = {
                "run_id": run_id,
                "task": "Submit my application to Acme.",
                "status": "running",
                "steps": 0,
                "history": [],
                "evidence_ids": [],
            }
            yield browser, gateway, store, runtime, graph, config, initial, events
    finally:
        await browser.close()


def pending(result):
    return result["__interrupt__"][0].value


def approval_answer(result, approved=True):
    return Command(
        resume={"request_id": pending(result)["request_id"], "approved": approved}
    )


async def test_approval_is_pure_and_resume_dispatches_once(tmp_path):
    async with graph_case(tmp_path, script=[click("Send application")]) as (
        browser,
        gateway,
        store,
        _runtime,
        graph,
        config,
        initial,
        _events,
    ):
        paused = await graph.ainvoke(initial, config)
        approval = pending(paused)
        assert approval["kind"] == "approval"
        assert gateway.calls == gateway.reviews == 1
        assert await browser.page.evaluate("window.effects || 0") == 0
        saved = await graph.aget_state(config)
        assert saved.next == ("approval",)
        # Reading persisted state neither repeats model calls nor mutates browser.
        await graph.aget_state(config)
        assert gateway.calls == gateway.reviews == 1
        result = await graph.ainvoke(approval_answer(paused), config)
        assert result["result"]["status"] == "partial"
        assert await browser.page.evaluate("window.effects") == 1
        assert gateway.reviews == 1
        assert store.approval(approval["request_id"])["status"] == "consumed"
        assert not store.unresolved_actions(initial["run_id"])


async def test_denied_or_mismatched_request_never_dispatches(tmp_path):
    async with graph_case(tmp_path, script=[click("Send application")]) as (
        browser,
        gateway,
        store,
        _runtime,
        graph,
        config,
        initial,
        _events,
    ):
        paused = await graph.ainvoke(initial, config)
        result = await graph.ainvoke(
            Command(resume={"request_id": "other-request", "approved": True}), config
        )
        assert result["result"]["status"] == "partial"
        assert await browser.page.evaluate("window.effects || 0") == 0
        assert store.approval(pending(paused)["request_id"])["status"] == "denied"
        assert store.has_denials(initial["run_id"])
        assert gateway.calls == 1


async def test_manual_letter_change_requires_new_approval(tmp_path):
    async with graph_case(
        tmp_path, script=[click("Send application"), click("Send application")]
    ) as (browser, _gateway, _store, _runtime, graph, config, initial, _events):
        first = await graph.ainvoke(initial, config)
        await browser.page.get_by_role("textbox", name="Letter").fill(
            "Changed precise outbound letter"
        )
        second = await graph.ainvoke(approval_answer(first), config)
        assert pending(second)["request_id"] != pending(first)["request_id"]
        assert await browser.page.evaluate("window.effects || 0") == 0
        assert "Changed precise outbound letter" in json.dumps(pending(second))
        await graph.ainvoke(approval_answer(second), config)
        assert await browser.page.evaluate("window.effects") == 1


async def test_new_browser_generation_invalidates_pending_approval(tmp_path):
    async with graph_case(
        tmp_path, script=[click("Send application"), click("Send application")]
    ) as (browser, _gateway, _store, _runtime, graph, config, initial, _events):
        paused = await graph.ainvoke(initial, config)
        old_generation = browser.generation
        await browser.close()
        await browser.start()
        await browser.page.set_content(FORM)
        assert browser.generation != old_generation
        again = await graph.ainvoke(approval_answer(paused), config)
        assert pending(again)["request_id"] != pending(paused)["request_id"]
        assert await browser.page.evaluate("window.effects || 0") == 0
        await graph.ainvoke(approval_answer(again), config)
        assert await browser.page.evaluate("window.effects") == 1


async def test_rewound_checkpoint_cannot_reuse_consumed_approval(tmp_path):
    async with graph_case(tmp_path, script=[click("Send application")]) as (
        browser,
        _gateway,
        store,
        _runtime,
        graph,
        config,
        initial,
        _events,
    ):
        paused = await graph.ainvoke(initial, config)
        old_checkpoint = (await graph.aget_state(config)).config
        await graph.ainvoke(approval_answer(paused), config)
        assert await browser.page.evaluate("window.effects") == 1
        # Resume an old checkpoint against current durable store and real browser.
        result = await graph.ainvoke(approval_answer(paused), old_checkpoint)
        assert await browser.page.evaluate("window.effects") == 1
        assert result["result"]["status"] == "partial"
        assert store.approval(pending(paused)["request_id"])["status"] == "consumed"


async def crash_worker(directory, origin):
    """Harness-owned subprocess: parent kills only this process at HTTP barrier."""
    async with graph_case(directory, script=[click("Send application")]) as (
        browser,
        _gateway,
        _store,
        _runtime,
        graph,
        config,
        initial,
        _events,
    ):
        await browser.page.goto(origin + "/form")
        (directory / "child-state.json").write_text(
            json.dumps({"initial": initial, "config": config})
        )
        paused = await graph.ainvoke(initial, config)
        await graph.ainvoke(approval_answer(paused), config)


async def crash_boundary(tmp_path, readable):
    """B07/B08: server commits POST, blocks response; SIGKILL precedes journal finish."""
    import asyncio
    import sys
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from pathlib import Path

    from browser_agent.browser import BrowserError

    recorded = threading.Event()
    release_response = threading.Event()
    submissions = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            return

        def do_GET(self):
            if self.path == "/form":
                body = '<h1>Application</h1><form action="/submit" method="post"><input name="recipient" aria-label="Recipient" value="Acme"><textarea name="letter" aria-label="Letter">I have Python experience.</textarea><button>Send application</button></form>'
            elif readable:
                body = f"<h1>Application recorded</h1><p>Destination Acme. Letter: I have Python experience.</p><p>Submissions: {len(submissions)}</p>"
            else:
                body = "<h1>Outcome temporarily unavailable</h1>"
            self.send_response(200 if readable or self.path == "/form" else 503)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body.encode())

        def do_POST(self):
            submissions.append(
                self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode()
            )
            recorded.set()
            release_response.wait(20)
            try:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"Application recorded")
            except (BrokenPipeError, ConnectionResetError):
                return  # Deliberate crash disconnected this harness request.

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    child = await asyncio.create_subprocess_exec(
        sys.executable,
        str(Path(__file__).resolve()),
        "--crash-worker",
        str(tmp_path),
        origin,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    browser = BrowserSession(tmp_path / "profile", headless=True)
    try:
        committed = await asyncio.to_thread(recorded.wait, 12)
        if not committed:
            if child.returncode is None:
                child.kill()
            _out, err = await child.communicate()
            raise AssertionError(
                "Child did not reach submission barrier: " + err.decode()[-3000:]
            )
        child.kill()  # Only the subprocess created above, never an unrelated app.
        await child.communicate()
        release_response.set()
        saved = json.loads((tmp_path / "child-state.json").read_text())
        initial, config = saved["initial"], saved["config"]
        store = Store(tmp_path / "operations.sqlite")
        unresolved = store.unresolved_actions(initial["run_id"])
        assert len(submissions) == len(unresolved) == 1
        assert unresolved[0]["status"] == "dispatched"
        # Let Playwright's pipe-owned driver release the crashed child profile.
        # Never remove Chromium's lock or launch a replacement identity.
        for attempt in range(20):
            try:
                await browser.start()
                break
            except BrowserError as exc:
                if exc.code != "profile_busy" or attempt == 19:
                    raise
                await asyncio.sleep(0.1)
        await browser.page.goto(origin + "/status")
        action_id = unresolved[0]["id"]

        def reconcile(obs):
            return "reconcile", {
                "action_id": action_id,
                "evidence_id": obs["id"],
                "quote": "Application recorded",
                "claim": "The exact Acme application was recorded once.",
            }

        gateway = ScriptedGateway([reconcile] if readable else [])
        runtime = AgentGraph(
            browser,
            gateway,
            store,
            Settings(artifact_dir=tmp_path),
            tmp_path / "run",
            lambda *_: None,
        )
        async with AsyncSqliteSaver.from_conn_string(
            str(tmp_path / "checkpoints.sqlite")
        ) as saver:
            graph = runtime.compile(saver)
            result = await graph.ainvoke(None, config)
        assert len(submissions) == 1
        assert result["result"]["status"] == "partial"
        if readable:
            assert store.action(action_id)["status"] == "verified"
            assert not store.unresolved_actions(initial["run_id"])
            assert gateway.completion_reviews == 1
        else:
            assert len(store.unresolved_actions(initial["run_id"])) == 1
            assert gateway.completion_reviews == 0
    finally:
        release_response.set()
        if child.returncode is None:
            child.kill()
            await child.communicate()
        await browser.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


async def test_process_crash_after_commit_reconciles_without_duplicate(tmp_path):
    await crash_boundary(tmp_path, readable=True)


async def test_process_crash_with_unreadable_outcome_stays_uncertain(tmp_path):
    await crash_boundary(tmp_path, readable=False)


if __name__ == "__main__":
    import asyncio
    import sys
    from pathlib import Path

    if len(sys.argv) == 4 and sys.argv[1] == "--crash-worker":
        asyncio.run(crash_worker(Path(sys.argv[2]), sys.argv[3]))
