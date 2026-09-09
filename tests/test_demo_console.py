"""Actual loopback HTTP controls, synthetic runner; no model or real account calls."""

import asyncio
import contextlib
import json
import threading
import time
from http.client import HTTPConnection

import pytest
from pydantic import ValidationError

from browser_agent.config import Settings
from scripts.demo_console import DemoApp, create_server


@contextlib.contextmanager
def console_server(tmp_path, runner, synthetic=False):
    app = DemoApp(
        Settings(artifact_dir=tmp_path),
        url="http://127.0.0.1:9999/start",
        profile="demo",
        release_session="existing-release",
        synthetic=synthetic,
        runner=runner,
    )
    server = create_server(app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield app, server
    finally:
        server.shutdown()
        server.server_close()
        app.close()
        thread.join(timeout=3)


def http(server, app, method="GET", path="/state", body=None, overrides=None):
    conn = HTTPConnection("127.0.0.1", server.server_port, timeout=3)
    headers = {
        "Authorization": "Bearer " + app.token,
        "Origin": f"http://127.0.0.1:{server.server_port}",
        "X-CSRF-Token": app.csrf,
        "Content-Type": "application/json",
    }
    headers.update(overrides or {})
    raw = json.dumps(body) if body is not None else None
    conn.request(method, path, raw, headers)
    response = conn.getresponse()
    text = response.read().decode()
    result = (
        json.loads(text)
        if response.getheader("Content-Type").startswith("application/json")
        else text
    )
    status = response.status
    conn.close()
    return status, result


def wait_state(server, app, predicate):
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        code, state = http(server, app)
        assert code == 200
        if predicate(state):
            return state
        time.sleep(0.01)
    pytest.fail("Console did not reach the expected state")


@pytest.mark.parametrize(
    "headers,code",
    [
        ({"Authorization": ""}, 401),
        ({"Authorization": "Bearer wrong"}, 401),
        ({"Host": "evil.example"}, 403),
        ({"Origin": "https://evil.example"}, 403),
        ({"Origin": ""}, 403),
        ({"X-CSRF-Token": ""}, 403),
        ({"X-CSRF-Token": "incorrect"}, 403),
        ({"Content-Type": "text/plain"}, 415),
    ],
)
def test_console_mutation_requires_host_session_origin_and_csrf(
    tmp_path, headers, code
):
    calls = []

    async def runner(*args, **kwargs):
        calls.append(kwargs)
        return {"status": "completed"}

    with console_server(tmp_path, runner) as (app, server):
        assert (
            http(server, app, "POST", "/start", {"task": "Inspect page"}, headers)[0]
            == code
        )
        assert calls == []
        assert not app.state()["active"]


def test_console_reads_require_session_and_never_embed_token_or_settings(tmp_path):
    async def runner(*args, **kwargs):
        return {"status": "completed"}

    with console_server(tmp_path, runner) as (app, server):
        code, page = http(server, app, path="/", overrides={"Authorization": ""})
        assert code == 200
        assert app.token not in page and app.csrf not in page
        assert "textContent=state.output" in page
        assert "innerHTML" not in page
        assert http(server, app, overrides={"Authorization": ""})[0] == 401
        assert http(server, app, overrides={"Host": "evil.example"})[0] == 403
        code, state = http(server, app)
        assert code == 200
        assert "api_key" not in state and "settings" not in state
        assert server.server_address[0] == "127.0.0.1"
        assert http(server, app, path="/arbitrary-file")[0] == 404


def test_console_singleflight_exact_approval_and_clarification(tmp_path):
    received = []

    async def runner(settings, **kwargs):
        received.append(kwargs)
        kwargs["console"].print(
            "<script>real output, never HTML</script>", markup=False
        )
        approval = await kwargs["responder"](
            {
                "kind": "approval",
                "request_id": "approval-42",
                "effect": {"destination": "Acme", "content": "Exact letter"},
            }
        )
        received.append(approval)
        answer = await kwargs["responder"](
            {"kind": "clarification", "question": "Which date?"}
        )
        received.append(answer)
        return {"status": "partial", "run_id": "actual-run-id"}

    with console_server(tmp_path, runner, True) as (app, server):
        literal = "Inspect $(touch /tmp/never-run-by-console); do not submit"
        assert http(server, app, "POST", "/start", {"task": literal})[0] == 200
        state = wait_state(server, app, lambda s: s["pending"])
        assert "Synthetic evaluation" in state["label"]
        assert "<script>real output" in state["output"]
        assert http(server, app, "POST", "/start", {"task": "second"})[0] == 409
        question = state["pending"]
        assert received[0]["task"] == literal
        assert received[0]["profile"] == "demo"
        assert received[0]["release_session"] == "existing-release"
        assert received[0]["headless"] is False
        assert received[0]["synthetic"] is True
        assert len(received) == 1  # No approval has been synthesized.
        bad = {
            "question_id": question["id"],
            "request_id": "other-approval",
            "approved": True,
        }
        assert http(server, app, "POST", "/answer", bad)[0] == 409
        bad["request_id"] = "approval-42"
        bad["approved"] = "yes"
        assert http(server, app, "POST", "/answer", bad)[0] == 409
        answer = {
            "question_id": question["id"],
            "request_id": "approval-42",
            "approved": False,
        }
        assert http(server, app, "POST", "/answer", answer)[0] == 200
        assert http(server, app, "POST", "/answer", answer)[0] == 409
        state = wait_state(
            server,
            app,
            lambda s: (
                s["pending"] and s["pending"]["question"]["kind"] == "clarification"
            ),
        )
        assert received[1] == {"request_id": "approval-42", "approved": False}
        reply = {"question_id": state["pending"]["id"], "answer": "Last Thursday"}
        assert http(server, app, "POST", "/answer", reply)[0] == 200
        state = wait_state(server, app, lambda s: not s["active"])
        assert received[2] == {"answer": "Last Thursday"}
        assert state["result"] == {"status": "partial", "run_id": "actual-run-id"}


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"task": ""},
        {"task": 12},
        {"task": "x", "approved": True},
        {"task": "x" * 12001},
    ],
)
def test_console_rejects_invalid_start_without_runner(tmp_path, body):
    async def runner(*args, **kwargs):
        pytest.fail("Invalid task reached runner")

    with console_server(tmp_path, runner) as (app, server):
        assert http(server, app, "POST", "/start", body)[0] == 409


def test_console_pause_returns_none_without_approval(tmp_path):
    answers = []

    async def runner(*args, **kwargs):
        answers.append(
            await kwargs["responder"]({"kind": "approval", "request_id": "exact"})
        )
        return {"status": "needs_user", "run_id": "saved"}

    with console_server(tmp_path, runner) as (app, server):
        http(server, app, "POST", "/start", {"task": "Wait at approval"})
        state = wait_state(server, app, lambda s: s["pending"])
        assert (
            http(
                server,
                app,
                "POST",
                "/answer",
                {"question_id": state["pending"]["id"], "pause": True},
            )[0]
            == 200
        )
        state = wait_state(server, app, lambda s: not s["active"])
        assert answers == [None]
        assert state["result"]["status"] == "needs_user"


def test_console_exception_values_are_not_exposed(tmp_path):
    async def runner(*args, **kwargs):
        raise RuntimeError("private-api-key-value")

    with console_server(tmp_path, runner) as (app, server):
        http(server, app, "POST", "/start", {"task": "Inspect"})
        state = wait_state(server, app, lambda s: not s["active"])
        assert "private-api-key-value" not in json.dumps(state)
        assert state["result"]["error_type"] == "RuntimeError"


def test_console_close_cancels_active_runner(tmp_path):
    cancelled = threading.Event()
    started = threading.Event()

    async def runner(*args, **kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    with console_server(tmp_path, runner) as (app, server):
        http(server, app, "POST", "/start", {"task": "Inspect"})
        assert started.wait(2)
    assert cancelled.is_set()


def test_console_cannot_raise_configured_task_cap():
    with pytest.raises(ValidationError):
        Settings(budget_usd=5.01)


async def test_console_browser_ui_streams_actual_output_and_binds_click_to_question(
    tmp_path,
):
    from playwright.async_api import async_playwright, expect

    answers = []

    async def runner(*args, **kwargs):
        kwargs["console"].print("Actual event: inspected <unsafe> page", markup=False)
        answers.append(
            await kwargs["responder"](
                {
                    "kind": "approval",
                    "request_id": "browser-approval",
                    "details": {
                        "recipient": "Acme",
                        "content": "Exact proposed letter",
                    },
                }
            )
        )
        return {"status": "partial", "run_id": "real-ui-test"}

    with console_server(tmp_path, runner, True) as (app, server):
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.goto(f"http://127.0.0.1:{server.server_port}/#{app.token}")
                await expect(page.locator("#mode")).to_contain_text(
                    "Synthetic evaluation"
                )
                await page.get_by_label("Task — describe the result you want").fill(
                    "Inspect this page"
                )
                await page.get_by_role("button", name="Start task", exact=True).click()
                await expect(page.locator("#output")).to_contain_text(
                    "Actual event: inspected <unsafe> page"
                )
                await expect(page.locator("#details")).to_contain_text(
                    "Exact proposed letter"
                )
                assert answers == []
                await page.get_by_role("button", name="Deny", exact=True).click()
                await expect(page.locator("#result")).to_contain_text("real-ui-test")
                assert answers == [
                    {"request_id": "browser-approval", "approved": False}
                ]
                assert await page.locator("unsafe").count() == 0
                assert app.token not in page.url
            finally:
                await browser.close()


def test_console_shutdown_preserves_driver_tasks_for_runner_cleanup(tmp_path):
    started = threading.Event()
    driver_cleaned = threading.Event()

    async def runner(*args, **kwargs):
        finish_driver = asyncio.Event()

        async def driver():
            await finish_driver.wait()
            driver_cleaned.set()

        child = asyncio.create_task(driver())
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            finish_driver.set()
            await child

    with console_server(tmp_path, runner) as (app, server):
        http(server, app, "POST", "/start", {"task": "Inspect"})
        assert started.wait(2)
    assert driver_cleaned.is_set()
