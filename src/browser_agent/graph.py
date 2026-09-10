"""LangGraph orchestration for one Playwright CLI actor."""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import nullcontext
from typing import Any, TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph

from .browser_cli import BrowserError
from .budget import BudgetExceeded
from .context import ContextOverflow, build_request
from .llm import ProviderFailure, extract_compaction
from .safety import (
    approval_question,
    parse_security_review,
    review_required,
    security_review_request,
)
from .telemetry import diagnostic_span
from .tools import ProtocolError, parse_call, protocol_pair


class State(TypedDict, total=False):
    history: list
    compaction: dict | None
    steps: int
    failures: int
    feedback: str
    route: str
    call: dict
    metadata: dict
    approval_fallback: str
    approval_granted: bool
    pending_result: dict
    result: dict


def _model_failure_summary(exc):
    if isinstance(exc, BudgetExceeded):
        return "I reached the task's model budget before completing the request."
    if isinstance(exc, ContextOverflow):
        return "The task history exceeded the model input limit; start a new task to continue."
    if isinstance(exc, TimeoutError):
        return "I did not receive a model decision before the task timeout."
    return "I could not get a usable model response; the browser remains open."


def _browser_error_result(call, exc):
    if hasattr(exc, "as_dict"):
        error = exc.as_dict()
    else:
        error = {"code": type(exc).__name__, "message": str(exc)}
    return {
        "status": "error",
        "tool": call.get("name"),
        "error": error,
        "instruction": (
            "The browser command was not replayed. Inspect this actual error and current "
            "browser output before choosing another command."
        ),
    }


class AgentGraph:
    def __init__(self, browser, gateway, settings, task, emit, responder=None):
        self.browser, self.gateway, self.settings = browser, gateway, settings
        self.task, self.emit, self.responder = task, emit, responder
        self.deadline = time.monotonic() + settings.active_seconds
        self.steps = 0

    def compile(self):
        graph = StateGraph(State)
        for name in ("decide", "review", "approve", "execute", "record"):
            graph.add_node(name, self.traced_node(name))
        graph.add_edge(START, "decide")
        routes = {"decide", "review", "approve", "execute", "record"}
        for name in routes:
            graph.add_conditional_edges(
                name,
                lambda state: state["route"],
                {route: route for route in routes} | {"end": END},
            )
        return graph.compile()

    def traced_node(self, name):
        async def invoke(state):
            context = (
                self.emit.span(name, step=state.get("steps", 0))
                if hasattr(self.emit, "span")
                else nullcontext()
            )
            with context:
                return await getattr(self, name)(state)

        return invoke

    @staticmethod
    def stop(summary, status="partial", question=None, **extra):
        result = {
            "status": status,
            "summary": summary,
            "remaining": [summary],
            **extra,
        }
        if question is not None:
            result["question"] = question
        return {"route": "end", "result": result}

    async def ask(self, question):
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
    def _pending_history(state):
        history = list(state.get("history", []))
        pending = state.get("pending_result")
        call = state.get("call")
        if pending is not None and call:
            history.extend(protocol_pair(call, pending))
        return history

    def _evidence(self):
        evidence = getattr(self.browser, "evidence", "")
        if isinstance(evidence, (dict, list)):
            return json.dumps(evidence, ensure_ascii=False)
        return str(evidence or "")

    def _instructions(self):
        return str(getattr(self.browser, "instructions", "") or "")

    async def decide(self, state):
        steps = state.get("steps", 0)
        if steps >= self.settings.max_decisions or time.monotonic() >= self.deadline:
            return self.stop("I reached the task's step or time limit before completing the request.")
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
                    "user_decision": "approved" if state.get("approval_granted") else None,
                },
            )
        try:
            async with diagnostic_span(self.emit, "actor_request_build", step=step) as diagnostic:
                request = build_request(
                    self.task,
                    self._evidence(),
                    history,
                    feedback=state.get("feedback", ""),
                    instructions=self._instructions(),
                    compaction=state.get("compaction"),
                    compact_threshold=self.settings.compact_threshold,
                )
                diagnostic["status"] = "ok"
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("task deadline reached")
            async with diagnostic_span(self.emit, "actor_call", step=step) as diagnostic:
                response = await asyncio.wait_for(self.gateway.call(request), timeout=remaining)
                diagnostic["status"] = "returned"
            async with diagnostic_span(self.emit, "actor_parse", step=step) as diagnostic:
                call = parse_call(response)
                diagnostic.update(status="ok", call_id=call.get("call_id"))
        except (BudgetExceeded, ContextOverflow, ProviderFailure, TimeoutError) as exc:
            return self.stop(_model_failure_summary(exc)) | {"steps": step}
        except ProtocolError as exc:
            failures = state.get("failures", 0) + 1
            self.emit(
                "recovery",
                {"step": step, "code": "invalid_model_response", "message": str(exc)},
            )
            if failures > self.settings.max_retries:
                return self.stop("I could not get a valid action from the model.") | {
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
            answer = await self.ask(args)
            if answer is None:
                return updates | self.stop(args["question"], "needs_user", args)
            result = {"status": "answered", "answer": str(answer)[:6000]}
            self.emit("tool_result", {"tool": tool, "step": step, "result": result})
            return updates | {
                "route": "decide",
                "history": list(updates.get("history", [])) + protocol_pair(call, result),
            }

        metadata = {
            "evidence": self._evidence(),
            "url": getattr(self.browser, "current_url", None) or "",
        }
        if review_required(tool, {"args": args}):
            return updates | {
                "route": "review",
                "metadata": metadata,
            }
        return updates | {"route": "execute", "metadata": metadata}

    async def review(self, state):
        action = {"tool": state["call"]["name"], "args": state["call"]["arguments"]}
        try:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("security review deadline reached")
            async with diagnostic_span(self.emit, "review_request_build", step=state.get("steps", 0)) as diagnostic:
                request = security_review_request(action, state.get("metadata", {}))
                diagnostic["status"] = "ok"
            async with diagnostic_span(self.emit, "reviewer_call", step=state.get("steps", 0)) as diagnostic:
                response = await asyncio.wait_for(
                    self.gateway.call(request, purpose="security"), timeout=remaining
                )
                diagnostic["status"] = "returned"
            async with diagnostic_span(self.emit, "reviewer_parse", step=state.get("steps", 0)) as diagnostic:
                needs_approval = parse_security_review(response)
                diagnostic.update(status="ok", needs_approval=needs_approval)
            self.emit("security_review", {"step": state.get("steps", 0), "needs_approval": needs_approval})
            return {"route": "approve" if needs_approval else "execute"}
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - provider/protocol failures require human fallback
            fallback = "Automatic safety check unavailable; confirm this action manually."
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

    async def approve(self, state):
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
        answer = await self.ask(question)
        if answer is None:
            return self.stop(
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
        self.emit("approval_answer", {"request_id": question["request_id"], "approved": approved})
        if not approved:
            return {
                "route": "record",
                "pending_result": {"status": "skipped_by_user", "tool": action["tool"]},
                "approval_granted": False,
            }
        return {"route": "execute", "approval_granted": True}

    async def execute(self, state):
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
            self.emit(
                "recovery",
                {"step": state.get("steps", 0), **_browser_error_result(call, exc)["error"]},
            )
            return {"route": "record", "pending_result": _browser_error_result(call, exc)}

    async def record(self, state):
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
        feedback = (
            "The host returned the actual browser result. Verify it before choosing the next action."
        )
        if isinstance(pending, dict) and pending.get("status") == "error":
            feedback = (
                "The browser command returned an actual error. Inspect its reason and current "
                "browser evidence, then choose a different command; do not replay automatically."
            )
        return {
            "route": "decide",
            "history": self._pending_history(state),
            "pending_result": None,
            "feedback": feedback,
            "failures": 0,
        }
