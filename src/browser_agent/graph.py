"""Small LangGraph: observe, one actor, bounded review, exact approval, execute.

State lives only for this run. The private security review has no browser tools,
and browser effects are never retried by the executor.
"""

import asyncio
import base64
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph

from .browser import BrowserError
from .budget import BudgetExceeded
from .context import HISTORY_MESSAGES, ContextOverflow, build_request
from .llm import ProviderFailure
from .safety import (
    SecurityReviewError,
    assess,
    parse_security_review,
    security_review_complete,
    security_review_request,
)
from .tools import ProtocolError, parse_call, protocol_pair


class State(TypedDict, total=False):
    observation: dict
    history: list
    notebook: str
    steps: int
    failures: int
    feedback: str
    route: str
    call: dict
    metadata: dict
    assessment: Any
    read_args: dict
    pending_result: dict
    image: str | None
    error: dict
    result: dict


def human_gate(observation):
    title = str(observation.get("title", "")).lower()
    text = str(observation.get("text", "")).lower()
    if any(
        phrase in title for phrase in ("captcha", "security challenge", "access denied")
    ) or any(
        phrase in text
        for phrase in (
            "verify you are human",
            "checking your browser",
            "complete the security check",
        )
    ):
        return "challenge"
    if "[password redacted]" in text or "sign in to continue" in text:
        return "login"
    return None


class AgentGraph:
    def __init__(self, browser, gateway, settings, task, emit, responder=None):
        self.browser, self.gateway, self.settings = browser, gateway, settings
        self.task, self.emit, self.responder = task, emit, responder
        self.deadline = time.monotonic() + settings.active_seconds
        self.steps = 0

    def compile(self):
        graph = StateGraph(State)
        for name in ("observe", "decide", "approve", "execute", "recover"):
            graph.add_node(name, getattr(self, name))
        graph.add_edge(START, "observe")
        for name in ("observe", "decide", "approve", "execute", "recover"):
            graph.add_conditional_edges(
                name,
                lambda state: state["route"],
                {
                    key: key
                    for key in ("observe", "decide", "approve", "execute", "recover")
                }
                | {"end": END},
            )
        return graph.compile()

    @staticmethod
    def stop(summary, status="partial", question=None):
        result = {"status": status, "summary": summary, "remaining": [summary]}
        if question:
            result["question"] = question
        return {"route": "end", "result": result}

    async def ask(self, question):
        self.emit("waiting", question)
        if self.responder is None:
            return None
        started = time.monotonic()
        answer = await self.responder(question)
        self.deadline += time.monotonic() - started  # Human time is not active runtime.
        return answer

    async def security_review(self, action, metadata, assessment):
        """Ask the same budgeted gateway for one structured, tool-free review."""
        request = security_review_request(
            self.task, action, metadata, assessment
        )
        try:
            response = await asyncio.wait_for(
                self.gateway.call(request, purpose="security"),
                timeout=max(0.1, self.deadline - time.monotonic()),
            )
            return parse_security_review(response)
        except SecurityReviewError:
            raise
        except (BudgetExceeded, ContextOverflow, ProviderFailure, TimeoutError) as exc:
            raise SecurityReviewError(
                "security_review_failed",
                "Security review could not complete within the shared budget or time limit",
            ) from exc
        except Exception as exc:
            raise SecurityReviewError(
                "security_review_failed", "Security review failed before dispatch"
            ) from exc

    def append_result(self, state, result):
        history = [
            item
            for item in state.get("history", [])
            if item.get("call_id") != state["call"]["call_id"]
        ]
        history += protocol_pair(state["call"], result)
        return history[-HISTORY_MESSAGES:]

    async def observe(self, state):
        try:
            observation = await self.browser.observe(**state.get("read_args", {}))
            self.emit("observe", observation)
            gate = human_gate(observation)
            if gate:
                question = {
                    "kind": gate,
                    "question": "Complete the login/security check manually in the browser, then reply ready.",
                }
                answer = await self.ask(question)
                if answer is None:
                    return self.stop(question["question"], "needs_user", question) | {
                        "observation": observation
                    }
                observation = await self.browser.observe()
                self.emit("observe", observation)
                if human_gate(observation):
                    return self.stop(
                        "The login/security check is still present.",
                        "needs_user",
                        question,
                    ) | {"observation": observation}
            updates = {"observation": observation, "read_args": {}, "route": "decide"}
            if state.get("pending_result") is not None:
                result = state["pending_result"] | {
                    "observation_id": observation["id"],
                    "url": observation.get("url"),
                    "title": observation.get("title"),
                    "requires_observation": False,
                    "observed_excerpt": observation.get("text", "")[:1200],
                    "excerpt_truncated": len(observation.get("text", "")) > 1200,
                }
                self.emit(
                    "tool_result",
                    {
                        "tool": state["call"]["name"],
                        "step": state["steps"],
                        "result": result,
                    },
                )
                updates.update(
                    history=self.append_result(state, result),
                    pending_result=None,
                    feedback="Action/read result observed; inspect the current page before choosing the next action.",
                )
            return updates
        except BrowserError as exc:
            error = exc.as_dict()
            if (state.get("pending_result") or {}).get("status") == "executed":
                error["uncertain"] = True
            return {"route": "recover", "error": error, "read_args": {}}

    async def decide(self, state):
        if (
            state.get("steps", 0) >= self.settings.max_decisions
            or time.monotonic() >= self.deadline
        ):
            return self.stop(
                "Step or active-time limit reached; remaining work was not completed."
            )
        step = state.get("steps", 0) + 1
        self.steps = step
        request = build_request(
            self.task,
            state["observation"],
            state.get("notebook", ""),
            state.get("history", []),
            state.get("image"),
            state.get("feedback", ""),
        )
        try:
            response = await asyncio.wait_for(
                self.gateway.call(request),
                timeout=max(0.1, self.deadline - time.monotonic()),
            )
            call = parse_call(response)
        except (BudgetExceeded, ContextOverflow, ProviderFailure, TimeoutError) as exc:
            return self.stop(
                str(exc) or "Model call exceeded the active-time limit."
            ) | {"steps": step}
        except ProtocolError as exc:
            failures = state.get("failures", 0) + 1
            if failures > self.settings.max_retries:
                return self.stop(str(exc)) | {"steps": step}
            return {
                "route": "decide",
                "steps": step,
                "failures": failures,
                "feedback": str(exc),
                "image": None,
            }
        tool, args = call["name"], call["arguments"]
        self.emit(
            "tool_proposed",
            {
                "step": step,
                "tool": tool,
                "arguments": args,
                "notebook": call["notebook"],
            },
        )
        updates = {
            "call": call,
            "steps": step,
            "image": None,
            "feedback": "",
            "notebook": call["notebook"],
        }
        current = state | updates
        if tool == "finish":
            if args["status"] == "completed" and args["remaining"]:
                return updates | {
                    "route": "decide",
                    "feedback": "A completed report cannot contain unmet requested work. Use partial or complete that work.",
                }
            return updates | {
                "route": "end",
                "result": args | {"details": call["notebook"]},
            }
        if tool == "ask_user":
            answer = await self.ask(args)
            if answer is None:
                return updates | self.stop(args["question"], "needs_user", args)
            return updates | {
                "route": "observe",
                "history": self.append_result(current, {"answer": str(answer)[:6000]}),
                "failures": 0,
            }
        if tool == "read":
            return updates | {
                "route": "observe",
                "read_args": args,
                "pending_result": {"status": "read"},
            }
        if tool == "screenshot":
            try:
                shot = await self.browser.screenshot()
                image = (
                    "data:image/png;base64,"
                    + base64.b64encode(Path(shot["path"]).read_bytes()).decode()
                )
                return updates | {
                    "route": "decide",
                    "image": image,
                    "history": self.append_result(
                        current,
                        {
                            "screenshot": "Current viewport attached to the next request."
                        },
                    ),
                    "failures": 0,
                }
            except BrowserError as exc:
                return updates | {"route": "recover", "error": exc.as_dict()}
        try:
            metadata = await self.browser.action_context(
                tool, args, state["observation"]["id"]
            )
            assessment = assess({"tool": tool, "args": args}, metadata)
            if assessment.forbidden or not assessment.details_complete:
                question = {"kind": "clarification", "question": assessment.reason}
                return updates | self.stop(assessment.reason, "needs_user", question)
            if assessment.requires_review:
                if not security_review_complete(metadata):
                    return updates | {
                        "route": "recover",
                        "error": {
                            "code": "incomplete_security_review_context",
                            "message": "Security review context was truncated; choose a fresh target",
                            "uncertain": False,
                        },
                    }
                try:
                    review = await self.security_review(
                        {"tool": tool, "args": args}, metadata, assessment
                    )
                except SecurityReviewError as exc:
                    return updates | {"route": "recover", "error": exc.as_dict()}
                self.emit(
                    "security_review",
                    {
                        "step": step,
                        "decision": review["decision"],
                        "reason": review["reason"][:500],
                    },
                )
                if review["decision"] == "deny":
                    return updates | self.stop(
                        "The independent security review denied this ambiguous action.",
                        "needs_user",
                        {
                            "kind": "clarification",
                            "question": review["reason"],
                        },
                    )
                assessment = replace(
                    assessment,
                    classification=(
                        "consequential"
                        if review["decision"] == "approval"
                        else "ordinary"
                    ),
                    reason=(
                        "Confirm this exact action after independent security review"
                        if review["decision"] == "approval"
                        else "Independent security review classified this as ordinary"
                    ),
                    requires_review=False,
                )
            return updates | {
                "route": "approve" if assessment.requires_approval else "execute",
                "metadata": metadata,
                "assessment": assessment,
            }
        except BrowserError as exc:
            return updates | {"route": "recover", "error": exc.as_dict()}

    async def approve(self, state):
        call, assessment = state["call"], state["assessment"]
        question = {
            "kind": "approval",
            "request_id": str(uuid4()),
            "question": assessment.reason,
            "action": {"tool": call["name"], "args": call["arguments"]},
            "effect": assessment.effect,
            "details": assessment.effect,
        }
        self.emit("approval_requested", question)
        answer = await self.ask(question)
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
            return self.stop(
                "Exact action approval was not granted; no action was executed.",
                "needs_user" if answer is None else "partial",
                question if answer is None else None,
            )
        return {"route": "execute"}

    async def execute(self, state):
        call = state["call"]
        try:
            result = await self.browser.execute(
                call["name"],
                call["arguments"],
                state["observation"]["id"],
                expected_fingerprint=state["metadata"]["fingerprint"],
            )
            return {"route": "observe", "pending_result": result, "failures": 0}
        except BrowserError as exc:
            return {"route": "recover", "error": exc.as_dict()}

    async def recover(self, state):
        error = state["error"]
        self.emit("recovery", {"step": state.get("steps", 0), **error})
        if error.get("uncertain"):
            return self.stop(
                "The action may already have taken effect. It will not be repeated; inspect the browser manually.",
                "needs_user",
                {
                    "kind": "clarification",
                    "question": "Inspect the uncertain action before starting another task.",
                },
            )
        failures = state.get("failures", 0) + 1
        if failures > self.settings.max_retries or error.get("code") in {
            "browser_disconnected",
            "browser_closed",
            "profile_locked",
        }:
            return self.stop(
                "Browser recovery stopped: "
                + error.get("message", error.get("code", "unknown error")),
                "needs_user",
                {
                    "kind": "clarification",
                    "question": "Check the browser and start a fresh task when ready.",
                },
            )
        history = state.get("history", [])
        if state.get("call"):
            history = self.append_result(
                state,
                {
                    "error": error,
                    "instruction": "Observe again and choose a different strategy; no automatic replay.",
                },
            )
        return {
            "route": "observe",
            "failures": failures,
            "history": history,
            "pending_result": None,
            "feedback": "Action failed before confirmed dispatch. Fresh observation follows; reconsider target/strategy. "
            + error.get("message", ""),
        }
