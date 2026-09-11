"""LangGraph orchestration for one Playwright CLI actor."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from contextlib import nullcontext
from typing import Any, Literal, TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph

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
from .protocol import ProtocolError, ToolCall, parse_tool_call, tool_result_items
from .safety import (
    approval_question,
    parse_security_review,
    review_required,
    security_review_request,
)
from .telemetry import diagnostic_span

type Route = Literal["decide", "review", "approve", "execute", "record", "end"]
type HumanResponder = Callable[[dict[str, Any]], Awaitable[Any]]


class AgentState(TypedDict, total=False):
    """Task progress shared by decision, approval, and execution nodes."""

    history: list[dict[str, Any]]
    response_items: list[dict[str, Any]]
    compaction: dict[str, Any] | None
    steps: int
    failures: int
    feedback: str
    route: Route
    call: ToolCall
    metadata: dict[str, Any]
    approval_fallback: str
    approval_granted: bool
    pending_result: dict[str, Any] | None
    result: dict[str, Any]


def _model_failure_summary(exc: Exception) -> str:
    if isinstance(exc, BudgetExceeded):
        return "I reached the task's model budget before completing the request."
    if isinstance(exc, ContextOverflow):
        return "The task history exceeded the model input limit; start a new task to continue."
    if isinstance(exc, TimeoutError):
        return "I did not receive a model decision before the task timeout."
    return "I could not get a usable model response; the browser remains open."


def _browser_error_result(call: ToolCall, exc: BrowserError) -> dict[str, Any]:
    error = exc.as_dict()
    return {
        "status": "error",
        "tool": call.get("name"),
        "error": error,
        "instruction": (
            "The browser command was not replayed. Inspect this actual error and current "
            "browser output before choosing another command."
        ),
    }


def _next_node(state: AgentState) -> Route:
    return state["route"]


class AgentGraph:
    """Coordinate one task while the browser and model remain explicit dependencies."""

    def __init__(
        self,
        browser: PlaywrightCLI,
        model: ModelClient,
        settings: Settings,
        task: str,
        emit: Callable[[str, dict[str, Any]], None],
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
        """Keep approval and execution transitions visible in the graph definition."""
        graph = StateGraph(AgentState)
        nodes = {
            "decide": self.decide,
            "review": self.review,
            "approve": self.approve,
            "execute": self.execute,
            "record": self.record,
        }
        for name, node in nodes.items():
            graph.add_node(name, self._trace_node(name, node))
        graph.add_edge(START, "decide")
        for name, destinations in {
            "decide": ("decide", "review", "execute", "end"),
            "review": ("approve", "execute"),
            "approve": ("record", "execute", "end"),
        }.items():
            graph.add_conditional_edges(
                name,
                _next_node,
                {route: END if route == "end" else route for route in destinations},
            )
        graph.add_edge("execute", "record")
        graph.add_edge("record", "decide")
        return graph.compile()

    def _trace_node(
        self, name: str, node: Callable[[AgentState], Awaitable[AgentState]]
    ) -> Callable[[AgentState], Awaitable[AgentState]]:
        async def invoke(state: AgentState) -> AgentState:
            span = (
                self.emit.span(name, step=state.get("steps", 0))
                if hasattr(self.emit, "span")
                else nullcontext()
            )
            with span:
                return await node(state)

        return invoke

    @staticmethod
    def _stop(
        summary: str,
        status: str = "partial",
        question: dict[str, Any] | None = None,
        **extra: Any,
    ) -> AgentState:
        result = {
            "status": status,
            "summary": summary,
            "remaining": [summary],
            **extra,
        }
        if question is not None:
            result["question"] = question
        return {"route": "end", "result": result}

    async def _ask_user(self, question: dict[str, Any]) -> Any:
        self.emit("waiting", question)
        if self.responder is None:
            return None
        started = time.monotonic()
        try:
            return await self.responder(question)
        finally:
            # Human time does not consume the active task deadline.
            self.deadline += time.monotonic() - started

    @staticmethod
    def _pending_history(state: AgentState) -> list[dict[str, Any]]:
        history = list(state.get("history", []))
        pending = state.get("pending_result")
        call = state.get("call")
        if pending is not None and call:
            history.extend(
                tool_result_items(call, pending, state.get("response_items"))
            )
        return history

    def _browser_evidence(self) -> str:
        evidence = getattr(self.browser, "evidence", "")
        if isinstance(evidence, (dict, list)):
            return json.dumps(evidence, ensure_ascii=False)
        return str(evidence or "")

    def _browser_instructions(self) -> str:
        return str(getattr(self.browser, "instructions", "") or "")

    async def decide(self, state: AgentState) -> AgentState:
        steps = state.get("steps", 0)
        if time.monotonic() >= self.deadline:
            return self._stop(
                "I reached the task's active time limit before completing the request."
            )
        step = steps + 1
        self.steps = step
        history = self._pending_history(state)
        pending = state.get("pending_result")
        if pending is not None:
            self.emit(
                "tool_result",
                {
                    "tool": state.get("call", {}).get("name"),
                    "step": steps,
                    "result": pending,
                    "user_decision": "approved"
                    if state.get("approval_granted")
                    else None,
                },
            )
        try:
            async with diagnostic_span(
                self.emit, "actor_request_build", step=step
            ) as diagnostic:
                request = build_request(
                    self.task,
                    history,
                    feedback=state.get("feedback", ""),
                    instructions=self._browser_instructions(),
                    compaction=state.get("compaction"),
                    compact_threshold=self.settings.compact_threshold,
                )
                diagnostic.update(input_item_diagnostics(request.get("input", [])))
                diagnostic["status"] = "ok"
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("task deadline reached")
            async with diagnostic_span(
                self.emit, "actor_call", step=step
            ) as diagnostic:
                response = await asyncio.wait_for(
                    self.model.call(request), timeout=remaining
                )
                diagnostic["status"] = "returned"
            async with diagnostic_span(
                self.emit, "actor_parse", step=step
            ) as diagnostic:
                call = parse_tool_call(response)
                diagnostic.update(status="ok", call_id=call.get("call_id"))
        except (BudgetExceeded, ContextOverflow, ProviderFailure, TimeoutError) as exc:
            return self._stop(_model_failure_summary(exc)) | {"steps": step}
        except ProtocolError as exc:
            failures = state.get("failures", 0) + 1
            self.emit(
                "recovery",
                {"step": step, "code": "invalid_model_response", "message": str(exc)},
            )
            if failures > self.settings.max_retries:
                return self._stop("I could not get a valid action from the model.") | {
                    "steps": step,
                    "failures": failures,
                }
            return {
                "route": "decide",
                "steps": step,
                "failures": failures,
                "feedback": str(exc),
                "history": history,
                "pending_result": None,
            }

        updates: dict[str, Any] = {
            "call": call,
            "response_items": list(call.get("response_items", [])),
            "steps": step,
            "feedback": "",
            "pending_result": None,
            "approval_fallback": "",
            "approval_granted": False,
            "history": history,
        }
        compaction = extract_compaction(response)
        if compaction is not None:
            updates["compaction"] = compaction
            updates["history"] = []
            updates["response_items"] = continuation_items(
                updates["response_items"], compaction
            )
        tool, args = call["name"], call["arguments"]
        self.emit("tool_proposed", {"step": step, "tool": tool, "arguments": args})

        if tool == "finish":
            if args["status"] == "completed" and args["remaining"]:
                return updates | {
                    "route": "decide",
                    "feedback": "A completed report cannot contain unmet requested work; use partial.",
                }
            return updates | {"route": "end", "result": args}

        if tool == "ask_user":
            answer = await self._ask_user(args)
            if answer is None:
                return updates | self._stop(args["question"], "needs_user", args)
            result = {"status": "answered", "answer": str(answer)[:6000]}
            self.emit("tool_result", {"tool": tool, "step": step, "result": result})
            return updates | {
                "route": "decide",
                "history": list(updates.get("history", []))
                + tool_result_items(call, result, updates.get("response_items")),
            }

        metadata = {
            "evidence": self._browser_evidence(),
            "url": getattr(self.browser, "current_url", None) or "",
        }
        if review_required(tool, {"args": args}):
            return updates | {
                "route": "review",
                "metadata": metadata,
            }
        return updates | {"route": "execute", "metadata": metadata}

    async def review(self, state: AgentState) -> AgentState:
        action = {"tool": state["call"]["name"], "args": state["call"]["arguments"]}
        try:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("security review deadline reached")
            async with diagnostic_span(
                self.emit, "review_request_build", step=state.get("steps", 0)
            ) as diagnostic:
                request = security_review_request(action, state.get("metadata", {}))
                # This phase is local-only diagnostic evidence. It lets us
                # explain a classifier decision from the exact packet it saw
                # without sending page content to the terminal or LangSmith.
                diagnostic["review_request"] = {
                    "instructions": request.get("instructions", ""),
                    "input": request.get("input", []),
                }
                diagnostic["status"] = "ok"
            async with diagnostic_span(
                self.emit, "reviewer_call", step=state.get("steps", 0)
            ) as diagnostic:
                response = await asyncio.wait_for(
                    self.model.call(request, purpose="security"), timeout=remaining
                )
                diagnostic["status"] = "returned"
            async with diagnostic_span(
                self.emit, "reviewer_parse", step=state.get("steps", 0)
            ) as diagnostic:
                needs_approval = parse_security_review(response)
                diagnostic.update(status="ok", needs_approval=needs_approval)
            self.emit(
                "security_review",
                {"step": state.get("steps", 0), "needs_approval": needs_approval},
            )
            return {"route": "approve" if needs_approval else "execute"}
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - provider/protocol failures require human fallback
            fallback = (
                "Automatic safety check unavailable; confirm this action manually."
            )
            self.emit(
                "security_review",
                {
                    "step": state.get("steps", 0),
                    "needs_approval": True,
                    "fallback": fallback,
                    "error_type": type(exc).__name__,
                },
            )
            return {"route": "approve", "approval_fallback": fallback}

    async def approve(self, state: AgentState) -> AgentState:
        action = {"tool": state["call"]["name"], "args": state["call"]["arguments"]}
        fallback = state.get("approval_fallback", "")
        question = {
            "kind": "approval",
            "request_id": str(uuid4()),
            "question": (fallback + " " if fallback else "")
            + approval_question(action, state.get("metadata", {})),
            "action": action,
        }
        if fallback:
            question["reason"] = fallback
        self.emit("approval_requested", question)
        answer = await self._ask_user(question)
        if answer is None:
            return self._stop(
                "User approval is required before this browser action.",
                "needs_user",
                question,
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
        if not approved:
            return {
                "route": "record",
                "pending_result": {"status": "skipped_by_user", "tool": action["tool"]},
                "approval_granted": False,
            }
        return {"route": "execute", "approval_granted": True}

    async def execute(self, state: AgentState) -> AgentState:
        call = state["call"]
        try:
            async with diagnostic_span(
                self.emit,
                "browser_execute",
                step=state.get("steps", 0),
                tool=call["name"],
                call_id=call.get("call_id"),
            ) as diagnostic:
                result = await self.browser.execute(call["name"], call["arguments"])
                if not isinstance(result, dict):
                    result = {"status": "executed", "output": result}
                diagnostic["status"] = result.get("status", "returned")
            return {"route": "record", "pending_result": result}
        except asyncio.CancelledError:
            raise
        except BrowserError as exc:
            result = _browser_error_result(call, exc)
            self.emit("recovery", {"step": state.get("steps", 0), **result["error"]})
            return {"route": "record", "pending_result": result}

    async def record(self, state: AgentState) -> AgentState:
        pending = state.get("pending_result")
        call = state.get("call")
        if pending is None or call is None:
            return {"route": "decide"}
        self.emit(
            "tool_result",
            {
                "tool": call.get("name"),
                "step": state.get("steps", 0),
                "result": pending,
                "user_decision": "approved" if state.get("approval_granted") else None,
            },
        )
        return {
            "route": "decide",
            "history": self._pending_history(state),
            "pending_result": None,
            # The actual result is already a native function_call_output.  Do
            # not add a synthetic user turn after every browser action; doing
            # so separates the carried reasoning item from its result and
            # needlessly changes the Responses conversation shape.
            "feedback": "",
            "failures": 0,
        }
