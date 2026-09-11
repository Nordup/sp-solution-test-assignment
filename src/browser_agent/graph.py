"""A five-node decision loop with native LangGraph routing and explicit approvals."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from functools import wraps
from typing import Any, Literal, TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from .browser import BrowserError, PlaywrightCLI
from .config import Settings
from .context import (
    ContextOverflow,
    build_request,
    continuation_items,
    extract_compaction,
    input_item_diagnostics,
)
from .model import BudgetExceeded, ModelClient, ProviderFailure
from .protocol import (
    ProtocolError,
    ResponseItem,
    ToolCall,
    parse_tool_call,
    tool_result_items,
)
from .safety import (
    approval_question,
    parse_security_review,
    review_required,
    security_review_request,
)
from .telemetry import Events, diagnostic_span

type HumanResponder = Callable[[dict[str, Any]], Awaitable[Any]]
type Route = Literal["decide", "review", "approve", "execute", "record", "__end__"]
type Transition = Command[Route]


@dataclass(frozen=True, slots=True)
class PendingAction:
    """Keep one proposal, its evidence, and its approval decision together."""

    call: ToolCall
    metadata: dict[str, Any]
    approved: bool = False
    fallback: str = ""

    @property
    def proposal(self) -> dict[str, Any]:
        return {"tool": self.call["name"], "args": self.call["arguments"]}


class AgentState(TypedDict, total=False):
    history: list[ResponseItem]
    compaction: ResponseItem | None
    steps: int
    failures: int
    feedback: str
    action: PendingAction
    pending_result: dict[str, Any]
    result: dict[str, Any]


def _stop(
    summary: str,
    status: str = "partial",
    *,
    updates: AgentState | None = None,
    question: dict[str, Any] | None = None,
    **details: Any,
) -> Transition:
    result = {"status": status, "summary": summary, "remaining": [summary], **details}
    if question is not None:
        result["question"] = question
    return Command(goto=END, update={**(updates or {}), "result": result})


def _model_failure_summary(error: Exception) -> str:
    match error:
        case BudgetExceeded():
            return "I reached the task's model budget before completing the request."
        case ContextOverflow():
            return "The task history exceeded the model input limit; start a new task to continue."
        case TimeoutError():
            return "I did not receive a model decision before the task timeout."
        case _:
            return "I could not get a usable model response; the browser remains open."


class AgentGraph:
    """Choose, review, approve, execute, and record one action at a time."""

    def __init__(
        self,
        browser: PlaywrightCLI,
        model: ModelClient,
        settings: Settings,
        task: str,
        emit: Events,
        responder: HumanResponder | None = None,
    ) -> None:
        self.browser = browser
        self.model = model
        self.settings = settings
        self.task = task
        self.emit = emit
        self.responder = responder
        self.deadline = time.monotonic() + settings.active_seconds
        self.steps = 0

    def compile(self):
        graph = StateGraph(AgentState)
        for node, destinations in (
            (self.decide, ("decide", "review", "execute", END)),
            (self.review, ("approve", "execute")),
            (self.approve, ("execute", "record", END)),
            (self.execute, ("record",)),
            (self.record, ("decide",)),
        ):
            graph.add_node(node.__name__, self._traced(node), destinations=destinations)
        graph.add_edge(START, "decide")
        return graph.compile()

    def _traced(
        self, node: Callable[[AgentState], Awaitable[Transition]]
    ) -> Callable[[AgentState], Awaitable[Transition]]:
        @wraps(node)
        async def invoke(state: AgentState) -> Transition:
            with self.emit.span(node.__name__, step=state.get("steps", 0)):
                return await node(state)

        return invoke

    async def decide(self, state: AgentState) -> Transition:
        if time.monotonic() >= self.deadline:
            return _stop(
                "I reached the task's active time limit before completing the request."
            )
        self.steps = state.get("steps", 0) + 1
        history = state.get("history", [])
        updates: AgentState = {"steps": self.steps, "feedback": "", "history": history}
        try:
            async with diagnostic_span(
                self.emit, "actor_request_build", step=self.steps
            ) as diagnostic:
                request = build_request(
                    self.task,
                    history,
                    feedback=state.get("feedback", ""),
                    instructions=self.browser.instructions,
                    compaction=state.get("compaction"),
                    compact_threshold=self.settings.compact_threshold,
                )
                diagnostic.update(input_item_diagnostics(request["input"]), status="ok")
            response = await self._model_response(request, purpose="actor")
            async with diagnostic_span(
                self.emit, "actor_parse", step=self.steps
            ) as diagnostic:
                call = parse_tool_call(response)
                diagnostic.update(status="ok", call_id=call["call_id"])
        except (
            BudgetExceeded,
            ContextOverflow,
            ProviderFailure,
            TimeoutError,
        ) as error:
            return _stop(_model_failure_summary(error), updates=updates)
        except ProtocolError as error:
            failures = state.get("failures", 0) + 1
            self.emit(
                "recovery",
                {
                    "step": self.steps,
                    "code": "invalid_model_response",
                    "message": str(error),
                },
            )
            updates.update(failures=failures, feedback=str(error))
            if failures > self.settings.max_retries:
                return _stop(
                    "I could not get a valid action from the model.", updates=updates
                )
            return Command(goto="decide", update=updates)

        if (compaction := extract_compaction(response)) is not None:
            updates.update(compaction=compaction, history=[])
            call = {
                **call,
                "response_items": continuation_items(
                    call["response_items"], compaction
                ),
            }
        name, arguments = call["name"], call["arguments"]
        self.emit(
            "tool_proposed", {"step": self.steps, "tool": name, "arguments": arguments}
        )

        if name == "finish":
            if arguments["status"] == "completed" and arguments["remaining"]:
                updates["feedback"] = (
                    "A completed report cannot contain unmet requested work; use partial."
                )
                return Command(goto="decide", update=updates)
            return Command(goto=END, update={**updates, "result": arguments})
        if name == "ask_user":
            answer = await self._ask_user(arguments)
            if answer is None:
                return _stop(
                    arguments["question"],
                    "needs_user",
                    question=arguments,
                    updates=updates,
                )
            result = {"status": "answered", "answer": str(answer)[:6000]}
            self.emit(
                "tool_result", {"tool": name, "step": self.steps, "result": result}
            )
            updates["history"] = updates["history"] + tool_result_items(call, result)
            return Command(goto="decide", update=updates)

        updates["action"] = PendingAction(
            call,
            {"evidence": self.browser.evidence, "url": self.browser.current_url or ""},
        )
        destination = (
            "review" if review_required(name, {"args": arguments}) else "execute"
        )
        return Command(goto=destination, update=updates)

    async def review(self, state: AgentState) -> Transition:
        action = state["action"]
        try:
            async with diagnostic_span(
                self.emit, "review_request_build", step=self.steps
            ) as diagnostic:
                request = security_review_request(action.proposal, action.metadata)
                # The exact packet stays in local diagnostics, outside tracing and UI.
                diagnostic.update(
                    status="ok",
                    review_request={
                        "instructions": request["instructions"],
                        "input": request["input"],
                    },
                )
            response = await self._model_response(request, purpose="security")
            async with diagnostic_span(
                self.emit, "reviewer_parse", step=self.steps
            ) as diagnostic:
                needed = parse_security_review(response)
                diagnostic.update(status="ok", needs_approval=needed)
        except Exception as error:  # noqa: BLE001 - any classifier failure falls back to human approval
            fallback = (
                "Automatic safety check unavailable; confirm this action manually."
            )
            self.emit(
                "security_review",
                {
                    "step": self.steps,
                    "needs_approval": True,
                    "fallback": fallback,
                    "error_type": type(error).__name__,
                },
            )
            return Command(
                goto="approve", update={"action": replace(action, fallback=fallback)}
            )
        self.emit("security_review", {"step": self.steps, "needs_approval": needed})
        return Command(goto="approve" if needed else "execute")

    async def approve(self, state: AgentState) -> Transition:
        action = state["action"]
        question = {
            "kind": "approval",
            "request_id": str(uuid4()),
            "question": " ".join(
                filter(
                    None,
                    (
                        action.fallback,
                        approval_question(action.proposal, action.metadata),
                    ),
                )
            ),
            "action": action.proposal,
        }
        if action.fallback:
            question["reason"] = action.fallback
        self.emit("approval_requested", question)
        answer = await self._ask_user(question)
        if answer is None:
            return _stop(
                "User approval is required before this browser action.",
                "needs_user",
                question=question,
                pending_approval=question,
            )
        approved = (
            isinstance(answer, dict)
            and answer.get("request_id") == question["request_id"]
            and answer.get("approved") is True
        )
        self.emit(
            "approval_answer",
            {"request_id": question["request_id"], "approved": approved},
        )
        if approved:
            return Command(
                goto="execute", update={"action": replace(action, approved=True)}
            )
        return Command(
            goto="record",
            update={
                "pending_result": {
                    "status": "skipped_by_user",
                    "tool": action.call["name"],
                }
            },
        )

    async def execute(self, state: AgentState) -> Transition:
        call = state["action"].call
        try:
            async with diagnostic_span(
                self.emit,
                "browser_execute",
                step=self.steps,
                tool=call["name"],
                call_id=call["call_id"],
            ) as diagnostic:
                result = await self.browser.execute(call["name"], call["arguments"])
                if not isinstance(result, dict):
                    result = {"status": "executed", "output": result}
                diagnostic["status"] = result.get("status", "returned")
        except BrowserError as error:
            result = {
                "status": "error",
                "tool": call["name"],
                "error": error.as_dict(),
                "instruction": "The browser command was not replayed. Inspect this actual error and current browser output before choosing another command.",
            }
            self.emit("recovery", {"step": self.steps, **result["error"]})
        return Command(goto="record", update={"pending_result": result})

    async def record(self, state: AgentState) -> Transition:
        action, result = state["action"], state["pending_result"]
        self.emit(
            "tool_result",
            {
                "tool": action.call["name"],
                "step": self.steps,
                "result": result,
                "user_decision": "approved" if action.approved else None,
            },
        )
        return Command(
            goto="decide",
            update={
                "history": state["history"] + tool_result_items(action.call, result),
                "feedback": "",
                "failures": 0,
            },
        )

    async def _model_response(
        self, request: dict[str, Any], *, purpose: Literal["actor", "security"]
    ) -> Any:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("task deadline reached")
        phase = "actor_call" if purpose == "actor" else "reviewer_call"
        async with diagnostic_span(self.emit, phase, step=self.steps) as diagnostic:
            response = await asyncio.wait_for(
                self.model.call(request, purpose=purpose), timeout=remaining
            )
            diagnostic["status"] = "returned"
            return response

    async def _ask_user(self, question: dict[str, Any]) -> Any:
        self.emit("waiting", question)
        if self.responder is None:
            return None
        started = time.monotonic()
        try:
            return await self.responder(question)
        finally:
            self.deadline += time.monotonic() - started
