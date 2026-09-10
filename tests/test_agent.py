"""Integration coverage for the Playwright CLI LangGraph loop."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from browser_agent.agent import run_task
from browser_agent.browser_cli import BrowserError
from browser_agent.config import Settings


class FakeBrowser:
    def __init__(self, tmp_path, *, error=False):
        self.profile = SimpleNamespace(name="fake")
        self.artifact_dir = Path(tmp_path) / "evidence"
        self.evidence = "snapshot: button e5 [Send]"
        self.instructions = "Use the fake Playwright CLI in tests."
        self.emit = None
        self.executed = []
        self.error = error
        self.prepared = 0

    async def prepare_task(self):
        self.prepared += 1
        self.evidence = ""

    async def execute(self, tool, args):
        self.executed.append((tool, dict(args)))
        if self.error:
            self.error = False
            raise BrowserError("cli_failed", "Synthetic CLI failure", uncertain=False)
        if tool == "playwright" and args.get("command") == "screenshot":
            return {
                "status": "executed",
                "tool": tool,
                "command": "screenshot",
                "output": {"path": "shot.png"},
                "content": [{"type": "image", "data": "aGk=", "mimeType": "image/png"}],
            }
        result = {
            "status": "executed",
            "tool": tool,
            "command": args.get("command"),
            "output": {"ok": True, "command": args.get("command")},
        }
        self.evidence = f"{args.get('command')}: completed"
        return result


class ScriptedGateway:
    def __init__(self, actor, security=(), *, compaction=None, block_actor=False):
        self.actor = list(actor)
        self.security = list(security)
        self.compaction = list(compaction or [])
        self.requests = []
        self.purposes = []
        self.attempts = 0
        self.cost_usd = 0.0
        self.reported_cost_usd = 0.0
        self.usage = {}
        self.closed = False
        self.block_actor = block_actor
        self.started = asyncio.Event()

    async def call(self, request, purpose="actor"):
        self.purposes.append(purpose)
        self.attempts += 1
        if purpose == "security":
            reply = self.security.pop(0) if self.security else False
            if isinstance(reply, BaseException):
                raise reply
            if isinstance(reply, dict):
                return reply
            return {
                "status": "completed",
                "output": [{
                    "type": "function_call",
                    "name": "security_review",
                    "call_id": str(uuid4()),
                    "arguments": json.dumps({"needs_approval": bool(reply)}),
                }],
            }
        self.started.set()
        if self.block_actor:
            await asyncio.Future()
        self.requests.append(request)
        name, args = self.actor.pop(0)
        output = []
        if self.compaction:
            output.append(self.compaction.pop(0))
        output.append({
            "type": "function_call",
            "name": name,
            "call_id": str(uuid4()),
            "arguments": json.dumps(args),
        })
        return {"status": "completed", "output": output}

    async def close(self):
        self.closed = True


def action(name, **args):
    return name, args


def finish(summary="Done", status="completed"):
    return action("finish", status=status, summary=summary, remaining=[] if status == "completed" else ["remaining"])


def diagnostic_events(tmp_path, result):
    path = Path(tmp_path) / "runs" / result["run_id"] / "events.jsonl"
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if json.loads(line).get("event") == "diagnostic"
    ]


async def run_case(tmp_path, actor, security=(), *, responder=None, browser=None, gateway=None, compaction=None):
    settings = Settings(artifact_dir=tmp_path, active_seconds=60)
    browser = browser or FakeBrowser(tmp_path)
    gateway = gateway or ScriptedGateway(actor, security, compaction=compaction)
    result = await run_task(
        settings,
        "Complete the requested browser task",
        browser,
        responder=responder,
        gateway_factory=lambda *_args, **_kwargs: gateway,
    )
    return result, browser, gateway


@pytest.mark.asyncio
async def test_false_review_executes_once_and_pairs_actual_result(tmp_path):
    result, browser, gateway = await run_case(
        tmp_path,
        [action("playwright", command="fill", args=["e5", "Armenia"]), finish()],
        security=[False],
    )
    assert result["status"] == "completed"
    assert browser.emit is None
    assert browser.executed == [("playwright", {"command": "fill", "args": ["e5", "Armenia"]})]
    assert gateway.purposes == ["actor", "security", "actor"]
    assert "function_call_output" in json.dumps(gateway.requests[1])
    assert "completed" in json.dumps(gateway.requests[1])


@pytest.mark.asyncio
async def test_true_review_waits_and_dispatches_exact_call_once(tmp_path):
    questions = []

    async def responder(question):
        questions.append(question)
        return {"request_id": question["request_id"], "approved": True}

    result, browser, _gateway = await run_case(
        tmp_path,
        [action("playwright", command="click", args=["e5"]), finish()],
        security=[True],
        responder=responder,
    )
    assert result["status"] == "completed"
    assert len(questions) == 1
    assert browser.executed == [("playwright", {"command": "click", "args": ["e5"]})]


@pytest.mark.asyncio
async def test_private_reviewer_packet_diagnostic_keeps_actual_evidence(tmp_path):
    class EvidenceBrowser(FakeBrowser):
        async def prepare_task(self):
            self.prepared += 1
            self.evidence = 'snapshot: {"ref":"e5","name":"Delete selected messages"}'

    async def responder(question):
        return {"request_id": question["request_id"], "approved": True}

    result, _browser, _gateway = await run_case(
        tmp_path,
        [action("playwright", command="click", args=["e5"]), finish()],
        security=[True],
        responder=responder,
        browser=EvidenceBrowser(tmp_path),
    )
    packets = [
        event
        for event in diagnostic_events(tmp_path, result)
        if event.get("phase") == "review_request_build" and event.get("state") == "finished"
    ]
    assert packets
    packet = packets[-1]["review_request"]
    assert "Delete selected messages" in json.dumps(packet)


@pytest.mark.asyncio
async def test_declined_action_is_normal_result_and_actor_continues(tmp_path):
    async def responder(question):
        return {"request_id": question["request_id"], "approved": False}

    result, browser, gateway = await run_case(
        tmp_path,
        [action("playwright", command="click", args=["e5"]), finish()],
        security=[True],
        responder=responder,
    )
    assert result["status"] == "completed"
    assert browser.executed == []
    assert "skipped_by_user" in json.dumps(gateway.requests[1])


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [ValueError("bad review"), RuntimeError("provider down")])
async def test_reviewer_failure_falls_back_to_manual_approval(tmp_path, failure):
    questions = []

    async def responder(question):
        questions.append(question)
        return {"request_id": question["request_id"], "approved": True}

    result, browser, _gateway = await run_case(
        tmp_path,
        [action("playwright", command="click", args=["e5"]), finish()],
        security=[failure],
        responder=responder,
    )
    assert result["status"] == "completed"
    assert len(browser.executed) == 1
    assert "Automatic safety check unavailable" in questions[0]["question"]


@pytest.mark.asyncio
async def test_read_only_screenshot_has_image_pair_and_no_automatic_scan(tmp_path):
    result, browser, gateway = await run_case(
        tmp_path,
        [action("playwright", command="screenshot", args=[]), finish()],
    )
    assert result["status"] == "completed"
    assert len(browser.executed) == 1
    assert gateway.purposes == ["actor", "actor"]
    assert "input_image" in json.dumps(gateway.requests[1])


@pytest.mark.asyncio
async def test_browser_error_is_paired_once_without_replay(tmp_path):
    result, browser, gateway = await run_case(
        tmp_path,
        [action("playwright", command="click", args=["e5"]), finish()],
        browser=FakeBrowser(tmp_path, error=True),
    )
    assert result["status"] == "completed"
    assert len(browser.executed) == 1
    assert "Synthetic CLI failure" in json.dumps(gateway.requests[1])


@pytest.mark.asyncio
async def test_native_compaction_keeps_pinned_task(tmp_path):
    compaction = {"type": "compaction", "id": "cmp", "encrypted_content": "opaque"}
    result, _browser, gateway = await run_case(
        tmp_path,
        [action("playwright", command="snapshot", args=[]), finish()],
        compaction=[compaction],
    )
    assert result["status"] == "completed"
    assert gateway.requests[1]["input"][0] == compaction
    assert "Complete the requested browser task" in json.dumps(gateway.requests[1])


@pytest.mark.asyncio
async def test_compaction_ask_user_pairs_answer_without_old_history(tmp_path):
    result, _browser, gateway = await run_case(
        tmp_path,
        [
            action("ask_user", question="Which account?", kind="clarification"),
            finish(),
        ],
        responder=lambda question: asyncio.sleep(0, result="work account"),
        compaction=[{"type": "compaction", "id": "cmp", "encrypted_content": "opaque"}],
    )
    assert result["status"] == "completed"
    second = json.dumps(gateway.requests[1])
    assert '"type": "compaction"' in second
    assert second.count('"type": "function_call"') == 1
    assert "work account" in second
    assert "Which account?" in second


@pytest.mark.asyncio
async def test_cancellation_closes_gateway_but_not_browser(tmp_path):
    browser = FakeBrowser(tmp_path)
    gateway = ScriptedGateway([], block_actor=True)
    task = asyncio.create_task(run_case(tmp_path, [], browser=browser, gateway=gateway))
    await asyncio.wait_for(gateway.started.wait(), 2)
    task.cancel()
    result, _browser, _gateway = await task
    assert result["status"] == "partial"
    assert gateway.closed is True
