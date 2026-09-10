"""Exercise the actual small LangGraph and Chromium; only paid model calls are faked."""

import asyncio
import json
import re
from uuid import uuid4

import pytest
from langgraph.graph import StateGraph
from langsmith.run_helpers import get_tracing_context

from browser_agent.agent import run_task
from browser_agent.browser import BrowserError, BrowserSession
from browser_agent.config import Settings

FORM = '<form onsubmit="event.preventDefault();window.effects=(window.effects||0)+1"><textarea aria-label="Message">Original text</textarea><button type="submit">Send</button></form>'


def ref(observation, label):
    line = next(
        line
        for line in observation["text"].splitlines()
        if f'"{label}"' in line and "[ref=" in line
    )
    return re.search(r"\[ref=([^\]]+)\]", line).group(1)


def click(label):
    return lambda observation: ("click", {"ref": ref(observation, label)})


def finish(status="completed"):
    return lambda _observation: (
        "finish",
        {
            "status": status,
            "summary": "Observed test result",
            "remaining": [] if status == "completed" else ["Work remains"],
        },
    )


class ScriptedGateway:
    def __init__(self, script):
        self.script, self.calls, self.cost_usd = list(script), 0, 0
        self.requests, self.closed, self.trace_enabled = [], False, None

    async def call(self, request):
        self.calls += 1
        self.requests.append(request)
        self.trace_enabled = get_tracing_context().get("enabled")
        text = request["input"][-1]["content"][0]["text"]
        observation = json.loads(
            text.split("Current browser observation:\n", 1)[1].split(
                "\nRuntime feedback:", 1
            )[0]
        )
        tool, args = self.script.pop(0)(observation)
        return {
            "status": "completed",
            "output": [
                {
                    "type": "function_call",
                    "name": tool,
                    "call_id": str(uuid4()),
                    "arguments": json.dumps(
                        {
                            "notebook": "Test task facts; next action as proposed.",
                            **args,
                        }
                    ),
                }
            ],
        }

    async def close(self):
        self.closed = True


def browser_factory(
    html, state, *, stale=False, uncertain=False, observation_failure=False
):
    class TestBrowser(BrowserSession):
        failed_once = False
        dispatched = False

        async def start(self, url=None):
            await super().start(None)
            await self.context.route(
                "**/*", lambda route: route.fulfill(body=html, content_type="text/html")
            )
            await self.page.goto("https://unit.test/start")
            state["browser"] = self

        async def action_context(self, *args, **kwargs):
            if stale and not self.failed_once:
                self.failed_once = True
                raise BrowserError(
                    "stale_ref", "Synthetic target replacement before dispatch"
                )
            return await super().action_context(*args, **kwargs)

        async def execute(self, *args, **kwargs):
            result = await super().execute(*args, **kwargs)
            self.dispatched = True
            if uncertain:
                raise BrowserError(
                    "navigation_timeout", "Response lost after effect", uncertain=True
                )
            return result

        async def observe(self, *args, **kwargs):
            if observation_failure and self.dispatched:
                raise BrowserError("observation_timeout", "Cannot inspect result")
            return await super().observe(*args, **kwargs)

        async def close(self):
            if self.page and not self.page.is_closed():
                state["effects"] = await self.page.evaluate("window.effects || 0")
            await super().close()

    return TestBrowser


async def run_case(tmp_path, script, *, html=FORM, responder=None, **browser_options):
    state = {}
    gateway = ScriptedGateway(script)
    result = await run_isolated(
        Settings(artifact_dir=tmp_path),
        "Perform the requested test action",
        responder=responder(state) if responder else None,
        browser_factory=browser_factory(html, state, **browser_options),
        gateway_factory=lambda _settings, **_kwargs: gateway,
    )
    return result, state, gateway


async def run_isolated(
    settings,
    task,
    *,
    url=None,
    profile="default",
    headless=True,
    responder=None,
    browser_factory=BrowserSession,
    gateway_factory=None,
):
    """Test-only harness for a fresh browser around the shared task runner."""
    settings.prepare()
    browser = browser_factory(
        settings.artifact_dir / "profiles" / profile,
        headless=headless,
        artifact_dir=settings.artifact_dir / "test-evidence",
    )
    try:
        await browser.start(url)
        kwargs = {"responder": responder}
        if gateway_factory is not None:
            kwargs["gateway_factory"] = gateway_factory
        return await run_task(settings, task, browser, **kwargs)
    finally:
        await browser.close()


async def test_actual_langgraph_run_records_result_and_disables_external_tracing(
    tmp_path, monkeypatch
):
    compiled = []
    original = StateGraph.compile

    def compile_graph(graph, *args, **kwargs):
        compiled.append(True)
        return original(graph, *args, **kwargs)

    monkeypatch.setattr(StateGraph, "compile", compile_graph)
    result, _state, gateway = await run_case(
        tmp_path, [finish()], html="<h1>Research result</h1>"
    )
    assert compiled == [True] and result["status"] == "completed"
    assert gateway.closed and gateway.trace_enabled is False
    saved = json.loads(
        (tmp_path / "runs" / result["run_id"] / "result.json").read_text()
    )
    assert saved == result and result["steps"] == 1 and result["cost_usd"] == 0


async def test_shared_task_runs_reset_refs_evidence_and_budget(tmp_path):
    settings = Settings(artifact_dir=tmp_path)
    browser = BrowserSession(tmp_path / "profile", headless=True)
    gateway_instances = [ScriptedGateway([finish()]), ScriptedGateway([finish()])]
    gateways = list(gateway_instances)

    def gateway_factory(_settings, **_kwargs):
        return gateways.pop(0)

    await browser.start()
    try:
        first = await run_task(
            settings,
            "Read the current page",
            browser,
            gateway_factory=gateway_factory,
        )
        first_observation = browser._observation["id"]
        first_evidence = browser.artifact_dir
        second = await run_task(
            settings,
            "Read the current page again",
            browser,
            gateway_factory=gateway_factory,
        )
        assert first["status"] == second["status"] == "completed"
        assert first["cost_usd"] == second["cost_usd"] == 0
        assert first_evidence != browser.artifact_dir
        with pytest.raises(BrowserError, match="current"):
            await browser.execute("tabs", {}, first_observation)
        assert all(gateway.closed for gateway in gateway_instances)
        assert (tmp_path / "runs" / first["run_id"] / "result.json").exists()
        assert (tmp_path / "runs" / second["run_id"] / "result.json").exists()
    finally:
        await browser.close()


@pytest.mark.parametrize("answer", ["approve", "deny", "wrong_id"])
async def test_exact_approval_controls_one_actual_effect(tmp_path, answer):
    questions = []

    def responder(_state):
        async def respond(question):
            questions.append(question)
            assert question["details"]["fields"][0]["value"] == "Original text"
            return {
                "request_id": question["request_id"]
                if answer != "wrong_id"
                else "different",
                "approved": answer != "deny",
            }

        return respond

    result, state, gateway = await run_case(
        tmp_path, [click("Send"), finish()], responder=responder
    )
    assert len(questions) == 1
    assert state["effects"] == (1 if answer == "approve" else 0)
    assert gateway.calls == (2 if answer == "approve" else 1)
    assert result["status"] == ("completed" if answer == "approve" else "partial")


async def test_changed_form_after_approval_prevents_dispatch_and_replans(tmp_path):
    def responder(state):
        async def respond(question):
            await (
                state["browser"]
                .page.get_by_role("textbox", name="Message")
                .fill("Changed after review")
            )
            return {"request_id": question["request_id"], "approved": True}

        return respond

    result, state, gateway = await run_case(
        tmp_path, [click("Send"), finish("partial")], responder=responder
    )
    assert result["status"] == "partial" and state["effects"] == 0
    assert gateway.calls == 2
    errors = [
        json.loads(item["output"])["error"]
        for item in gateway.requests[-1]["input"]
        if item.get("type") == "function_call_output"
    ]
    assert errors[0]["code"] in {"stale_ref", "approval_changed"}
    assert "Changed after review" in json.dumps(gateway.requests[-1])


async def test_stale_reference_requires_new_actor_decision_before_effect(tmp_path):
    def responder(_state):
        async def respond(question):
            return {"request_id": question["request_id"], "approved": True}

        return respond

    result, state, gateway = await run_case(
        tmp_path,
        [click("Send"), click("Send"), finish()],
        responder=responder,
        stale=True,
    )
    assert (
        result["status"] == "completed" and state["effects"] == 1 and gateway.calls == 3
    )
    assert "stale_ref" in json.dumps(gateway.requests[1])


@pytest.mark.parametrize("failure", ["uncertain", "observation_failure"])
async def test_uncertain_effect_is_never_replayed(tmp_path, failure):
    def responder(_state):
        async def respond(question):
            return {"request_id": question["request_id"], "approved": True}

        return respond

    result, state, gateway = await run_case(
        tmp_path, [click("Send"), click("Send")], responder=responder, **{failure: True}
    )
    assert result["status"] == "needs_user" and state["effects"] == 1
    assert gateway.calls == 1 and gateway.closed


@pytest.mark.parametrize(
    "html,kind",
    [
        ("<p>Verify you are human</p>", "challenge"),
        ('<input type="password" aria-label="Password">', "login"),
    ],
)
async def test_login_and_challenge_pause_without_model_polling(tmp_path, html, kind):
    result, _state, gateway = await run_case(tmp_path, [], html=html)
    assert result["status"] == "needs_user" and result["question"]["kind"] == kind
    assert gateway.calls == 0


async def test_cancelled_run_keeps_actual_effect_and_cleans_up(tmp_path, monkeypatch):
    original_call = ScriptedGateway.call

    async def interrupted(self, request):
        if self.calls == 1:
            self.cost_usd = 0.01
            raise asyncio.CancelledError()
        return await original_call(self, request)

    monkeypatch.setattr(ScriptedGateway, "call", interrupted)

    def responder(_state):
        async def respond(question):
            return {"request_id": question["request_id"], "approved": True}

        return respond

    result, state, gateway = await run_case(
        tmp_path, [click("Send")], responder=responder
    )
    assert result["status"] == "partial" and state["effects"] == 1
    assert result["steps"] >= 1 and result["cost_usd"] == 0.01
    saved = json.loads(
        (tmp_path / "runs" / result["run_id"] / "result.json").read_text()
    )
    assert saved == result
    assert "did not start" not in result["summary"]
    assert gateway.closed and gateway.calls == 1


@pytest.mark.parametrize("becomes_enabled", [True, False])
async def test_disabled_target_reobserves_before_approval_or_stops_at_retry_bound(
    tmp_path, becomes_enabled
):
    state, questions = {}, []
    base = browser_factory(
        FORM.replace('type="submit"', 'type="submit" disabled'), state
    )

    class LoadingBrowser(base):
        async def action_context(self, *args, **kwargs):
            try:
                return await super().action_context(*args, **kwargs)
            except BrowserError as exc:
                if exc.code == "disabled":
                    assert await self.page.evaluate("window.effects || 0") == 0
                    assert questions == []
                    if becomes_enabled:
                        await self.page.get_by_role("button", name="Send").evaluate(
                            "el => el.disabled = false"
                        )
                raise

    async def respond(question):
        questions.append(question)
        return {"request_id": question["request_id"], "approved": True}

    gateway = ScriptedGateway(
        [click("Send"), click("Send"), finish()]
        if becomes_enabled
        else [click("Send")] * 3
    )
    result = await run_isolated(
        Settings(artifact_dir=tmp_path, max_retries=2),
        "Send the message",
        browser_factory=LoadingBrowser,
        gateway_factory=lambda *_args, **_kwargs: gateway,
        responder=respond,
    )
    assert result["status"] == ("completed" if becomes_enabled else "needs_user")
    assert gateway.calls == 3
    assert state["effects"] == (1 if becomes_enabled else 0)
    assert len(questions) == (1 if becomes_enabled else 0)
    assert '"code": "disabled"' in json.dumps(gateway.requests[1]).replace('\\"', '"')
    first = gateway.requests[0]["input"][-1]["content"][0]["text"]
    second = gateway.requests[1]["input"][-1]["content"][0]["text"]
    assert first != second


async def test_required_notebook_survives_expired_history_on_next_real_request(
    tmp_path,
):
    from browser_agent.context import HISTORY_MESSAGES

    html = '<p id="fact">Original item identity: Q19</p><button onclick="document.querySelector(\'#fact\').remove()">Hide fact</button>'
    notebook = "Original item Q19 was observed. Do not reselect shifted items."

    def initial(observation):
        assert "Original item identity: Q19" in observation["text"]
        return "click", {"ref": ref(observation, "Hide fact"), "notebook": notebook}

    def read_after(index):
        def proposal(observation):
            assert "Original item identity: Q19" not in observation["text"]
            return "read", {
                "offset": 0,
                "scope": None,
                "notebook": notebook
                + f" Completed observation {index}; report remains.",
            }

        return proposal

    def responder(_state):
        async def respond(question):
            return {"request_id": question["request_id"], "approved": True}

        return respond

    count = HISTORY_MESSAGES // 2 + 2
    result, _state, gateway = await run_case(
        tmp_path,
        [initial] + [read_after(i) for i in range(count)] + [finish()],
        html=html,
        responder=responder,
    )
    assert result["status"] == "completed" and gateway.calls == count + 2
    request = gateway.requests[-1]
    assert notebook in request["input"][0]["content"]
    assert result["details"] == "Test task facts; next action as proposed."
    assert f"Completed observation {count - 1}" in request["input"][0]["content"]
    history = request["input"][1:-1]
    assert len(history) == HISTORY_MESSAGES
    assert all(item.get("name") != "click" for item in history)
    assert "Original item identity: Q19" not in json.dumps(request["input"][-1])
    for item in history:
        if item.get("type") == "function_call":
            assert json.loads(item["arguments"])["notebook"].startswith(notebook)
