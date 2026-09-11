"""Integration coverage for the Playwright CLI LangGraph loop."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from browser_agent.agent import run_task
from browser_agent.browser import BrowserError
from browser_agent.config import Settings


class FakeBrowser:
    def __init__(self, tmp_path, *, error=False):
        self.profile = SimpleNamespace(name="fake")
        self.current_url = None
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


class ScriptedModel:
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
                "output": [
                    {
                        "type": "function_call",
                        "name": "security_review",
                        "call_id": str(uuid4()),
                        "arguments": json.dumps({"needs_approval": bool(reply)}),
                    }
                ],
            }
        self.started.set()
        if self.block_actor:
            await asyncio.Future()
        self.requests.append(request)
        name, args = self.actor.pop(0)
        output = []
        if self.compaction:
            output.append(self.compaction.pop(0))
        output.append(
            {
                "type": "function_call",
                "name": name,
                "call_id": str(uuid4()),
                "arguments": json.dumps(args),
            }
        )
        return {"status": "completed", "output": output}

    async def close(self):
        self.closed = True


class OpaqueHistoryModel(ScriptedModel):
    """Emit native reasoning/message items around each scripted function call."""

    def __init__(self, actor, security=(), *, compaction_turn=None):
        super().__init__(actor, security)
        self.compaction_turn = compaction_turn
        self.turn = 0

    async def call(self, request, purpose="actor"):
        if purpose == "security":
            return await super().call(request, purpose)
        self.purposes.append(purpose)
        self.attempts += 1
        self.started.set()
        self.requests.append(request)
        name, args = self.actor.pop(0)
        output = []
        if self.turn == self.compaction_turn:
            output.append(
                {
                    "type": "reasoning",
                    "id": "rs_stale",
                    "encrypted_content": "discard-before-compaction",
                    "summary": [],
                }
            )
            output.append(
                {
                    "type": "compaction",
                    "id": "cmp-native",
                    "encrypted_content": "opaque-compaction",
                }
            )
        output.extend(
            [
                {
                    "type": "reasoning",
                    "id": f"rs_{self.turn}",
                    "encrypted_content": f"opaque-reasoning-{self.turn}",
                    "summary": [],
                },
                {
                    "type": "message",
                    "id": f"msg_{self.turn}",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": f"Turn {self.turn}"}],
                    "status": "completed",
                },
                {
                    "type": "function_call",
                    "name": name,
                    "call_id": f"call_{self.turn}",
                    "arguments": json.dumps(args),
                },
            ]
        )
        self.turn += 1
        return {"status": "completed", "output": output}


def tool_call(name, **args):
    return name, args


def finish_call(summary="Done", status="completed"):
    return tool_call(
        "finish",
        status=status,
        summary=summary,
        remaining=[] if status == "completed" else ["remaining"],
    )


def diagnostic_events(tmp_path, result):
    path = Path(tmp_path) / "runs" / result["run_id"] / "events.jsonl"
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if json.loads(line).get("event") == "diagnostic"
    ]


async def run_agent_case(
    tmp_path,
    actor,
    security=(),
    *,
    responder=None,
    browser=None,
    model=None,
    compaction=None,
):
    settings = Settings(artifact_dir=tmp_path, active_seconds=60)
    browser = browser or FakeBrowser(tmp_path)
    model = model or ScriptedModel(actor, security, compaction=compaction)
    result = await run_task(
        settings,
        "Complete the requested browser task",
        browser,
        responder=responder,
        model_factory=lambda *_args, **_kwargs: model,
    )
    return result, browser, model


@pytest.mark.asyncio
async def test_false_review_executes_once_and_pairs_actual_result(tmp_path):
    result, browser, model = await run_agent_case(
        tmp_path,
        [
            tool_call("playwright", command="fill", args=["e5", "Armenia"]),
            finish_call(),
        ],
        security=[False],
    )
    assert result["status"] == "completed"
    assert browser.emit is None
    assert browser.executed == [
        ("playwright", {"command": "fill", "args": ["e5", "Armenia"]})
    ]
    assert model.purposes == ["actor", "security", "actor"]
    assert "function_call_output" in json.dumps(model.requests[1])
    assert "completed" in json.dumps(model.requests[1])
    # The native result is paired directly with the original call.  A routine
    # synthetic user turn would split carried reasoning from its result.
    assert (
        sum(
            item.get("role") == "user"
            for item in model.requests[1]["input"]
            if isinstance(item, dict)
        )
        == 1
    )
    assert model.requests[1]["input"][-1]["type"] == "function_call_output"


@pytest.mark.asyncio
async def test_true_review_waits_and_dispatches_exact_call_once(tmp_path):
    questions = []

    async def responder(question):
        questions.append(question)
        return {"request_id": question["request_id"], "approved": True}

    result, browser, _gateway = await run_agent_case(
        tmp_path,
        [tool_call("playwright", command="click", args=["e5"]), finish_call()],
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

    result, _browser, _gateway = await run_agent_case(
        tmp_path,
        [tool_call("playwright", command="click", args=["e5"]), finish_call()],
        security=[True],
        responder=responder,
        browser=EvidenceBrowser(tmp_path),
    )
    packets = [
        event
        for event in diagnostic_events(tmp_path, result)
        if event.get("phase") == "review_request_build"
        and event.get("state") == "finished"
    ]
    assert packets
    packet = packets[-1]["review_request"]
    assert "Delete selected messages" in json.dumps(packet)


@pytest.mark.asyncio
async def test_declined_action_is_normal_result_and_actor_continues(tmp_path):
    async def responder(question):
        return {"request_id": question["request_id"], "approved": False}

    result, browser, model = await run_agent_case(
        tmp_path,
        [tool_call("playwright", command="click", args=["e5"]), finish_call()],
        security=[True],
        responder=responder,
    )
    assert result["status"] == "completed"
    assert browser.executed == []
    assert "skipped_by_user" in json.dumps(model.requests[1])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", [ValueError("bad review"), RuntimeError("provider down")]
)
async def test_reviewer_failure_falls_back_to_manual_approval(tmp_path, failure):
    questions = []

    async def responder(question):
        questions.append(question)
        return {"request_id": question["request_id"], "approved": True}

    result, browser, _gateway = await run_agent_case(
        tmp_path,
        [tool_call("playwright", command="click", args=["e5"]), finish_call()],
        security=[failure],
        responder=responder,
    )
    assert result["status"] == "completed"
    assert len(browser.executed) == 1
    assert "Automatic safety check unavailable" in questions[0]["question"]


@pytest.mark.asyncio
async def test_read_only_screenshot_has_image_pair_and_no_automatic_scan(tmp_path):
    result, browser, model = await run_agent_case(
        tmp_path,
        [tool_call("playwright", command="screenshot", args=[]), finish_call()],
    )
    assert result["status"] == "completed"
    assert len(browser.executed) == 1
    assert model.purposes == ["actor", "actor"]
    assert "input_image" in json.dumps(model.requests[1])


@pytest.mark.asyncio
async def test_browser_error_is_paired_once_without_replay(tmp_path):
    result, browser, model = await run_agent_case(
        tmp_path,
        [tool_call("playwright", command="click", args=["e5"]), finish_call()],
        browser=FakeBrowser(tmp_path, error=True),
    )
    assert result["status"] == "completed"
    assert len(browser.executed) == 1
    assert "Synthetic CLI failure" in json.dumps(model.requests[1])


@pytest.mark.asyncio
async def test_native_compaction_keeps_pinned_task(tmp_path):
    compaction = {"type": "compaction", "id": "cmp", "encrypted_content": "opaque"}
    result, _browser, model = await run_agent_case(
        tmp_path,
        [tool_call("playwright", command="snapshot", args=[]), finish_call()],
        compaction=[compaction],
    )
    assert result["status"] == "completed"
    assert model.requests[1]["input"][0] == compaction
    assert "Complete the requested browser task" in json.dumps(model.requests[1])


@pytest.mark.asyncio
async def test_reasoning_and_assistant_items_survive_tool_turns_and_compaction(
    tmp_path,
):
    model = OpaqueHistoryModel(
        [
            tool_call("playwright", command="fill", args=["e5", "Armenia"]),
            tool_call("playwright", command="snapshot", args=[]),
            finish_call(),
        ],
        compaction_turn=1,
    )
    result, browser, _gateway = await run_agent_case(
        tmp_path,
        [],
        security=[False],
        model=model,
    )
    assert result["status"] == "completed"
    assert len(browser.executed) == 2

    first_followup = model.requests[1]["input"]
    first_serialized = json.dumps(first_followup, ensure_ascii=False)
    assert "opaque-reasoning-0" in first_serialized
    assert "Turn 0" in first_serialized
    assert first_serialized.count('"type": "function_call"') == 1
    assert first_serialized.count('"type": "function_call_output"') == 1
    assert (
        sum(
            item.get("role") == "user"
            for item in first_followup
            if isinstance(item, dict)
        )
        == 1
    )
    assert first_followup[-1]["type"] == "function_call_output"

    compacted_followup = model.requests[2]["input"]
    compacted_serialized = json.dumps(compacted_followup, ensure_ascii=False)
    assert compacted_followup[0] == {
        "type": "compaction",
        "id": "cmp-native",
        "encrypted_content": "opaque-compaction",
    }
    assert "opaque-reasoning-0" not in compacted_serialized
    assert "discard-before-compaction" not in compacted_serialized
    assert "opaque-reasoning-1" in compacted_serialized
    assert "Turn 1" in compacted_serialized
    assert compacted_serialized.count('"type": "function_call"') == 1
    assert compacted_serialized.count('"type": "function_call_output"') == 1

    diagnostics = diagnostic_events(tmp_path, result)
    actor_builds = [
        event
        for event in diagnostics
        if event.get("phase") == "actor_request_build"
        and event.get("state") == "finished"
    ]
    assert any(
        event.get("encrypted_reasoning_items") == 1 for event in actor_builds[1:]
    )
    assert all("opaque-reasoning" not in json.dumps(event) for event in actor_builds)


@pytest.mark.asyncio
@pytest.mark.parametrize("approved", [True, False])
async def test_reasoning_items_pair_after_approval_decision(tmp_path, approved):
    model = OpaqueHistoryModel(
        [
            tool_call("playwright", command="click", args=["e5"]),
            finish_call(),
        ],
        security=[True],
    )

    async def responder(question):
        return {"request_id": question["request_id"], "approved": approved}

    result, browser, _gateway = await run_agent_case(
        tmp_path,
        [],
        security=[True],
        responder=responder,
        model=model,
    )
    assert result["status"] == "completed"
    assert len(browser.executed) == int(approved)
    history = json.dumps(model.requests[1], ensure_ascii=False)
    assert "opaque-reasoning-0" in history
    if approved:
        assert "executed" in history
    else:
        assert "skipped_by_user" in history
    assert history.count('"type": "function_call"') == 1
    assert history.count('"type": "function_call_output"') == 1


@pytest.mark.asyncio
async def test_reasoning_items_pair_after_ask_user_answer(tmp_path):
    model = OpaqueHistoryModel(
        [
            tool_call("ask_user", question="Which account?", kind="clarification"),
            finish_call(),
        ]
    )
    result, _browser, _gateway = await run_agent_case(
        tmp_path,
        [],
        responder=lambda question: asyncio.sleep(0, result="work account"),
        model=model,
    )
    assert result["status"] == "completed"
    history = json.dumps(model.requests[1], ensure_ascii=False)
    assert "opaque-reasoning-0" in history
    assert "work account" in history
    assert history.count('"type": "function_call"') == 1
    assert history.count('"type": "function_call_output"') == 1


@pytest.mark.asyncio
async def test_compaction_ask_user_pairs_answer_without_old_history(tmp_path):
    result, _browser, model = await run_agent_case(
        tmp_path,
        [
            tool_call("ask_user", question="Which account?", kind="clarification"),
            finish_call(),
        ],
        responder=lambda question: asyncio.sleep(0, result="work account"),
        compaction=[{"type": "compaction", "id": "cmp", "encrypted_content": "opaque"}],
    )
    assert result["status"] == "completed"
    second = json.dumps(model.requests[1])
    assert '"type": "compaction"' in second
    assert second.count('"type": "function_call"') == 1
    assert "work account" in second
    assert "Which account?" in second


@pytest.mark.asyncio
async def test_cancellation_closes_gateway_but_not_browser(tmp_path):
    browser = FakeBrowser(tmp_path)
    model = ScriptedModel([], block_actor=True)
    task = asyncio.create_task(
        run_agent_case(tmp_path, [], browser=browser, model=model)
    )
    await asyncio.wait_for(model.started.wait(), 2)
    task.cancel()
    result, _browser, _gateway = await task
    assert result["status"] == "partial"
    assert model.closed is True
