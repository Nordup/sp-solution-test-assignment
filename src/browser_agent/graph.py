"""Generic observe/decide/review/approve/execute loop with durable interrupts."""

from __future__ import annotations

import base64
import json
import os
import re
import tempfile
import time
import uuid
from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from .browser import BrowserError
from .context import HISTORY_GROUPS, ContextOverflow, memory_due, request, task_context
from .llm import ProviderFailure
from .safety import Policy
from .storage import AdmissionError, BudgetExceeded, DuplicateAction
from .tools import CollectionScope, ProtocolError, parse_call, protocol_pair, tool_specs


class State(TypedDict, total=False):
    run_id: str
    task: str
    status: str
    observation: dict
    history: list
    notes: str
    scope: dict | None
    progress: list
    memory_step: int
    memory_required: bool
    review_scope: dict | None
    pending_call: dict | None
    feedback: str
    clarifications: list[str]
    retry_not_before: float
    call: dict
    action_id: str
    action: dict
    metadata: dict
    assessment: dict
    review: dict
    approval_id: str | None
    human: dict
    question: dict
    result: dict
    steps: int
    repairs: int
    failures: int
    repetitions: dict
    evidence_ids: list
    visited: list
    active_seconds: float
    route: str
    screenshot_path: str | None


def challenge(observation):
    text = (
        observation.get("title", "") + "\n" + observation.get("text", "")
    ).casefold()
    markers = (
        "verify you are human",
        "confirm you are human",
        "подтвердите, что вы не робот",
        "проверка браузера",
        "checking your browser",
        "captcha",
        "this browser or app may not be secure",
        "access denied",
        "security verification",
    )
    return any(marker in text for marker in markers)


class AgentGraph:
    def __init__(self, browser, gateway, store, settings, run_dir: Path, emit):
        self.browser, self.gateway, self.store = browser, gateway, store
        self.settings, self.run_dir, self.emit = settings, run_dir, emit
        self.safety_policy = Policy(store)
        self.run_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        (run_dir / "evidence").mkdir(exist_ok=True, mode=0o700)

    def compile(self, checkpointer):
        graph = StateGraph(State)
        for name in (
            "observe",
            "decide",
            "policy",
            "approval",
            "execute",
            "verify",
            "recover",
            "ask",
            "finalize",
        ):
            method = getattr(self, name)

            async def measured(state, method=method):
                started = time.monotonic()
                update = await method(state)
                update["active_seconds"] = (
                    state.get("active_seconds", 0) + time.monotonic() - started
                )
                return update

            graph.add_node(name, measured)
        graph.add_edge(START, "observe")
        graph.add_conditional_edges(
            "observe",
            lambda s: s["route"],
            {"decide": "decide", "ask": "ask", "finalize": "finalize"},
        )
        graph.add_conditional_edges(
            "decide",
            lambda s: s["route"],
            {
                "policy": "policy",
                "ask": "ask",
                "finalize": "finalize",
                "decide": "decide",
                "recover": "recover",
                "verify": "verify",
            },
        )
        graph.add_conditional_edges(
            "policy",
            lambda s: s["route"],
            {
                "approval": "approval",
                "decide": "decide",
                "execute": "execute",
                "recover": "recover",
                "ask": "ask",
                "finalize": "finalize",
            },
        )
        graph.add_edge("approval", "execute")
        graph.add_conditional_edges(
            "execute",
            lambda s: s["route"],
            {
                "verify": "verify",
                "recover": "recover",
                "ask": "ask",
                "finalize": "finalize",
            },
        )
        graph.add_conditional_edges(
            "verify",
            lambda s: s["route"],
            {"decide": "decide", "ask": "ask", "finalize": "finalize"},
        )
        graph.add_conditional_edges(
            "recover",
            lambda s: s["route"],
            {"observe": "observe", "ask": "ask", "finalize": "finalize"},
        )
        graph.add_edge("ask", "observe")
        graph.add_edge("finalize", END)
        return graph.compile(checkpointer=checkpointer)

    def save_observation(self, observation):
        path = self.run_dir / "evidence" / f"{observation['id']}.json"
        path.write_text(json.dumps(observation, ensure_ascii=False), encoding="utf-8")
        path.chmod(0o600)

    def restore_memory(self, state):
        """Authoritative memory survives old graph checkpoints and browser restart."""
        path = self.run_dir / "memory.json"
        if not path.exists():
            return state
        saved = json.loads(path.read_text())
        if saved.get("scope"):
            CollectionScope.model_validate(saved["scope"])
        return state | {key: saved.get(key) for key in ("scope", "notes", "progress")}

    def persist_memory(self, state):
        path = self.run_dir / "memory.json"
        payload = {
            "scope": state.get("scope"),
            "notes": state.get("notes", ""),
            "progress": state.get("progress", []),
        }
        if path.exists():
            previous = json.loads(path.read_text())
            if previous.get("scope") and previous["scope"] != payload["scope"]:
                raise ProtocolError(
                    "Original collection scope is immutable; new observations cannot replace it."
                )
        # Atomic replacement and synchronous write: no later dispatch can use an
        # unpersisted scope. A filesystem failure propagates before browser effects.
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.run_dir, delete=False
            ) as handle:
                temporary = Path(handle.name)
                os.chmod(temporary, 0o600)
                json.dump(payload, handle, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            directory_fd = os.open(self.run_dir, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if temporary and temporary.exists():
                temporary.unlink()

    def validate_scope(self, proposed, state):
        if proposed is None:
            return state.get("scope")
        if state.get("scope") and proposed != state["scope"]:
            raise ProtocolError(
                "Frozen collection identities cannot be replaced. Return scope=null and retain the original boundary."
            )
        allowed = set(state.get("evidence_ids", [])) | {
            item["evidence_id"] for item in state.get("visited", [])
        }
        identities = set()
        for item in proposed["items"]:
            if item["identity"].casefold() not in item["quote"].casefold():
                raise ProtocolError(
                    "Scope identity must appear in its supporting observed quote; invented identifiers are not accepted."
                )
            if item["identity"] in identities or item["evidence_id"] not in allowed:
                raise ProtocolError(
                    "Scope requires distinct identities and previously observed evidence IDs."
                )
            identities.add(item["identity"])
            evidence = json.loads(
                (self.run_dir / "evidence" / f"{item['evidence_id']}.json").read_text()
            )
            if item["quote"] not in evidence["text"]:
                raise ProtocolError(
                    f"Scope quote for {item['identity']!r} is absent from actual observed evidence. "
                    "Use one unchanged contiguous substring, not combined page nodes. "
                    "The exact observed identity alone is sufficient as its quote. Put dates and other combined facts in notes."
                )
        return proposed

    async def observe(self, state):
        remaining = state.get("retry_not_before", 0) - time.time()
        if remaining > 0:
            return {
                "route": "ask",
                "question": {
                    "kind": "challenge",
                    "question": f"Website Retry-After is still active for {remaining:.0f} seconds. No automatic request will run before it expires.",
                },
            }
        try:
            observation = await self.browser.observe()
            self.save_observation(observation)
            self.emit(
                "observe",
                {
                    "id": observation["id"],
                    "url": observation["url"],
                    "text": observation["text"][:1000],
                    "truncated": observation["truncated"],
                },
            )
            visited = state.get("visited", [])
            entry = {
                "url": observation["url"][:1500],
                "title": observation["title"][:200],
                "evidence_id": observation["id"],
            }
            # Keep the earliest receipt for each distinct page; current snapshot is separate.
            if not any(item["url"] == entry["url"] for item in visited):
                visited = (visited + [entry])[-60:]
            update = {
                "visited": visited,
                "observation": observation,
                "evidence_ids": (state.get("evidence_ids", []) + [observation["id"]])[
                    -120:
                ],
                "route": "decide",
            }
            if challenge(observation):
                update.update(
                    route="ask",
                    question={
                        "kind": "challenge",
                        "question": "Complete the browser verification manually, then explicitly continue. Automated actions are paused.",
                    },
                )
            elif observation.get("http", {}).get("status") == 429:
                from datetime import UTC, datetime
                from email.utils import parsedate_to_datetime

                raw_delay = observation["http"].get("retry_after") or "60"
                try:
                    delay = float(raw_delay)
                except (TypeError, ValueError):
                    try:
                        delay = (
                            parsedate_to_datetime(raw_delay) - datetime.now(UTC)
                        ).total_seconds()
                    except (TypeError, ValueError):
                        delay = 60
                delay = max(0, delay)
                update["retry_not_before"] = time.time() + delay
                update.update(
                    route="ask",
                    question={
                        "kind": "challenge",
                        "question": f"The website is rate limiting requests. Wait at least {delay:.0f} seconds (Retry-After), then continue manually; no automatic refresh will run.",
                    },
                )
            return update
        except BrowserError as exc:
            return {
                "route": "ask",
                "question": {
                    "kind": "browser",
                    "question": f"Browser unavailable ({exc.code}). Close/reopen this task with resume; no action was replayed.",
                },
                "feedback": exc.code,
            }

    def outcome(self, state, value, **extra):
        return {
            "history": (
                state.get("history", []) + [protocol_pair(state["call"], value)]
            )[-HISTORY_GROUPS:],
            "feedback": json.dumps(value, ensure_ascii=False)[:2000],
            **extra,
        }

    async def decide(self, state):
        state = self.restore_memory(state)
        if (
            state.get("steps", 0) >= self.settings.max_decisions
            or state.get("active_seconds", 0) >= self.settings.active_seconds
        ):
            return {
                "route": "finalize",
                "status": "partial",
                "feedback": "Decision or active-time limit reached.",
            }
        started = time.monotonic()
        try:
            unresolved = self.store.unresolved_actions(state["run_id"])
            if unresolved:
                state = state | {
                    "feedback": state.get("feedback", "")
                    + "\nUncertain actions to inspect before more changes: "
                    + json.dumps(
                        [
                            {
                                "action_id": item["id"],
                                "details": json.loads(item["details"]),
                            }
                            for item in unresolved
                        ],
                        ensure_ascii=False,
                    )[:6000]
                }
            req = request(state, tool_specs())
            if state.get("screenshot_path"):
                data = base64.b64encode(
                    Path(state["screenshot_path"]).read_bytes()
                ).decode()
                req["input"].append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_image",
                                "image_url": "data:image/png;base64," + data,
                            }
                        ],
                    }
                )
            response = (
                await self.gateway.call(req, purpose="memory")
                if memory_due(state)
                else await self.gateway.call(req)
            )
            call = parse_call(response)
            if memory_due(state) and call["name"] != "remember":
                raise ProtocolError(
                    "A native remember call is required before rolling history expires; no browser action was dispatched."
                )
            update = {
                "scope": state.get("scope"),
                "progress": state.get("progress", []),
                "notes": state.get("notes", ""),
                "call": call,
                "steps": state.get("steps", 0) + 1,
                "repairs": 0,
                "screenshot_path": None,
                "active_seconds": state.get("active_seconds", 0)
                + time.monotonic()
                - started,
            }
            self.emit(
                "tool_proposed",
                {
                    "step": update["steps"],
                    "tool": call["name"],
                    "arguments": call["arguments"],
                },
            )
            if call["name"] == "ask_user":
                return update | {"route": "ask", "question": call["arguments"]}
            if call["name"] == "finish":
                return update | {"route": "finalize", "result": call["arguments"]}
            if call["name"] == "recall":
                evidence_id = call["arguments"]["evidence_id"]
                allowed = set(state.get("evidence_ids", [])) | {
                    item["evidence_id"] for item in state.get("visited", [])
                }
                if evidence_id not in allowed:
                    raise ProtocolError(
                        "Recall requires a previously delivered evidence ID."
                    )
                saved = json.loads(
                    (self.run_dir / "evidence" / f"{evidence_id}.json").read_text()
                )
                historic = re.sub(
                    r"\[ref=[A-Za-z0-9]+\]",
                    "[historical; not actionable]",
                    saved["text"],
                )
                offset = call["arguments"]["offset"]
                if offset > len(historic):
                    raise ProtocolError(
                        "Recall offset is beyond this saved observation."
                    )
                excerpt = (
                    historic[offset:]
                    .encode("utf-8")[:8000]
                    .decode("utf-8", errors="ignore")
                )
                end = offset + len(excerpt)
                return update | self.outcome(
                    state | update,
                    {
                        "evidence_id": evidence_id,
                        "url": saved["url"],
                        "historical_text": excerpt,
                        "next_offset": end if end < len(historic) else None,
                    },
                    route="decide",
                )
            if call["name"] == "reconcile":
                args = call["arguments"]
                pending = {
                    item["id"]: item
                    for item in self.store.unresolved_actions(state["run_id"])
                }
                if args["action_id"] not in pending or args[
                    "evidence_id"
                ] not in state.get("evidence_ids", []):
                    raise ProtocolError(
                        "Reconciliation requires an unresolved action and actual observed evidence ID."
                    )
                obs = json.loads(
                    (
                        self.run_dir / "evidence" / f"{args['evidence_id']}.json"
                    ).read_text()
                )
                if args["quote"] not in obs["text"]:
                    raise ProtocolError(
                        "Reconciliation quote is absent from observation."
                    )
                review = await self.gateway.verify_completion(
                    "Verify that this exact prior action actually took effect. Do not accept a proposed action or absence of an error as proof: "
                    + pending[args["action_id"]]["details"],
                    args,
                    {args["evidence_id"]: obs["text"]},
                )
                if review["supported"]:
                    self.store.finish_action(args["action_id"], "verified", args)
                return update | self.outcome(
                    state | update,
                    {"reconciled": review["supported"], "reason": review["reason"]},
                    route="decide",
                )
            if call["name"] == "remember":
                notes = call["arguments"]["notes"]
                if len(notes.encode("utf-8")) > 12000:
                    raise ProtocolError(
                        "Cumulative memory must fit 12000 UTF-8 bytes; summarize before saving."
                    )
                scope = self.validate_scope(call["arguments"]["scope"], state)
                self.persist_memory(state | {"notes": notes, "scope": scope})
                self.emit(
                    "memory_saved",
                    {
                        "step": update["steps"],
                        "scope_fixed": scope is not None,
                        "scope_items": len(scope["items"]) if scope else 0,
                        "receipts": len(state.get("progress", [])),
                    },
                )
                return update | self.outcome(
                    state | update,
                    {"saved": True},
                    notes=notes,
                    scope=scope,
                    memory_step=update["steps"],
                    memory_required=False,
                    call=state["pending_call"] if state.get("pending_call") else call,
                    pending_call=None,
                    route="policy" if state.get("pending_call") else "decide",
                )
            if call["name"] in ("read", "screenshot", "tabs"):
                if call["name"] == "read":
                    obs = await self.browser.observe(**call["arguments"])
                    self.save_observation(obs)
                    if challenge(obs) or obs.get("http", {}).get("status") == 429:
                        return update | {
                            "observation": obs,
                            "route": "ask",
                            "question": {
                                "kind": "challenge",
                                "question": "Complete the browser verification or wait for its Retry-After interval, then explicitly continue. Automated actions are paused.",
                            },
                        }
                    return update | self.outcome(
                        state | update,
                        {"observation_id": obs["id"], "text": obs["text"][:1000]},
                        observation=obs,
                        evidence_ids=(state.get("evidence_ids", []) + [obs["id"]])[
                            -120:
                        ],
                        route="decide",
                    )
                if call["name"] == "screenshot":
                    shot = await self.browser.screenshot()
                    return update | self.outcome(
                        state | update,
                        {"screenshot_saved": True},
                        screenshot_path=shot["path"],
                        route="decide",
                    )
                result = await self.browser.execute(
                    "tabs", {}, state["observation"]["id"]
                )
                return update | self.outcome(state | update, result, route="decide")
            return update | {
                "route": "policy",
                "action_id": str(uuid.uuid4()),
                "action": {"tool": call["name"], "args": call["arguments"]},
                "approval_id": None,
                "human": {},
            }
        except (ProtocolError, ContextOverflow) as exc:
            repairs = state.get("repairs", 0) + 1
            self.emit("protocol_repair", {"error": str(exc), "attempt": repairs})
            return {
                "repairs": repairs,
                "history": state.get("history", [])[-2:],
                "feedback": str(exc),
                "route": "decide" if repairs <= 2 else "finalize",
                "status": "failed" if repairs > 2 else "running",
                "steps": state.get("steps", 0) + 1,
            }
        except (BudgetExceeded, ProviderFailure) as exc:
            return {
                "route": "finalize",
                "status": "budget_exhausted"
                if isinstance(exc, BudgetExceeded)
                else "failed",
                "feedback": str(exc),
            }
        except BrowserError as exc:
            return {
                "route": "recover",
                "feedback": exc.code,
                "failures": state.get("failures", 0) + 1,
            }

    async def policy(self, state):
        state = self.restore_memory(state)
        try:
            action = state["action"]
            if action["tool"] == "navigate":
                url = action["args"]["url"]
                observed = state["observation"]
                supplied = url in state["task"] or url in state.get("feedback", "")
                visible = url in observed.get("text", "") or url == observed.get("url")
                known_tab = any(
                    url == tab.get("url") for tab in observed.get("tabs", [])
                )
                if not (supplied or visible or known_tab):
                    raise BrowserError(
                        "unobserved_destination",
                        "Destination must come from the user or the current page. Follow an observed link ref instead of guessing a URL.",
                    )
            metadata = await self.browser.action_context(
                action["tool"], action["args"], state["observation"]["id"]
            )
            review = await self.gateway.review(
                state["task"], action, metadata | {"task_context": task_context(state)}
            )
            assessment = self.safety_policy.assess(
                action, metadata, review, state["run_id"]
            )
            update = {
                "metadata": metadata,
                "review": review,
                "review_scope": state.get("scope"),
                "assessment": {
                    "classification": assessment.classification,
                    "effect": assessment.effect,
                    "requires_approval": assessment.requires_approval,
                },
            }
            if assessment.forbidden:
                return update | self.outcome(
                    state,
                    {"error": "forbidden", "reason": assessment.reason},
                    route="recover",
                    failures=state.get("failures", 0) + 1,
                )
            if not assessment.details_complete:
                return update | {
                    "route": "ask",
                    "question": {
                        "kind": "clarification",
                        "question": assessment.reason,
                    },
                }
            viewing = review["classification"] == "ordinary" and (
                action["tool"] in {"back", "scroll", "switch_tab", "navigate"}
                or (metadata.get("tag") == "a" and not metadata.get("form_action"))
            )
            if assessment.requires_approval and not viewing:
                if not state.get("memory_step"):
                    return update | {
                        "memory_required": True,
                        "pending_call": state["call"],
                        "route": "decide",
                    }
                if review.get("scope_status", "uncertain") == "out_of_scope":
                    return update | self.outcome(
                        state,
                        {
                            "error": "out_of_scope",
                            "reason": "Proposed effect is outside the preserved original task boundary. Choose an in-scope action; no approval was requested.",
                        },
                        route="recover",
                        failures=state.get("failures", 0) + 1,
                    )
                if review.get("scope_status", "uncertain") != "in_scope":
                    return update | {
                        "route": "ask",
                        "question": {
                            "kind": "clarification",
                            "question": "The affected object's membership in the original task scope is not established. Clarify or inspect the original selection before making this change.",
                        },
                    }
            unresolved = self.store.unresolved_actions(state["run_id"])
            viewing = assessment.classification == "ordinary" and (
                action["tool"] in {"back", "scroll", "switch_tab", "navigate"}
            )
            same_uncertain_destination = any(
                json.loads(item["details"])["effect"]["destination"]
                == assessment.effect["destination"]
                for item in unresolved
            )
            if unresolved and (not viewing or same_uncertain_destination):
                return update | {
                    "route": "ask",
                    "question": {
                        "kind": "uncertain",
                        "question": "A prior action has an uncertain outcome. Inspect the page and saved journal before permitting further changes; this run will not repeat it automatically.",
                    },
                }
            if assessment.requires_approval:
                approval_id = self.store.request_approval(
                    state["run_id"],
                    state["action_id"],
                    action,
                    assessment.effect,
                    metadata["generation"],
                )
                return update | {"route": "approval", "approval_id": approval_id}
            return update | {"route": "execute"}
        except (BudgetExceeded, ProviderFailure) as exc:
            return {"route": "finalize", "status": "partial", "feedback": str(exc)}
        except (BrowserError, ProtocolError, AdmissionError) as exc:
            return {
                "route": "recover",
                "feedback": str(exc),
                "failures": state.get("failures", 0) + 1,
            }

    async def approval(self, state):
        # Re-executed on resume. No effects, paid calls or writes before interrupt.
        answer = interrupt(
            {
                "kind": "approval",
                "request_id": state["approval_id"],
                "effect_summary": state["review"]["effect_summary"],
                "details": state["assessment"]["effect"],
            }
        )
        valid = (
            isinstance(answer, dict)
            and answer.get("request_id") == state["approval_id"]
            and type(answer.get("approved")) is bool
        )
        return {
            "human": answer
            if valid
            else {"request_id": state["approval_id"], "approved": False}
        }

    async def execute(self, state):
        restored = self.restore_memory(state)
        action = state["action"]
        action_id = state["action_id"]
        try:
            if restored.get("scope") != state.get("review_scope"):
                raise AdmissionError(
                    "Original scope changed since this checkpoint's review; reobserve and review before dispatch."
                )
            if self.store.action(action_id):
                raise DuplicateAction(
                    "Action already journaled. Inspect the outcome; do not replay."
                )
            approval_id = state.get("approval_id")
            if approval_id:
                self.store.decide_approval(
                    approval_id, state["human"].get("approved") is True
                )
                if not state["human"].get("approved"):
                    return self.outcome(
                        state,
                        {"denied": True, "reason": "User denied the exact effect."},
                        route="finalize",
                        status="partial",
                    )
            fresh = await self.browser.action_context(
                action["tool"], action["args"], state["observation"]["id"]
            )
            if fresh["fingerprint"] != state["metadata"]["fingerprint"]:
                raise BrowserError(
                    "approval_changed",
                    "Browser details changed during review; observe and request a new approval.",
                )
            assessment = self.safety_policy.assess(
                action, fresh, state["review"], state["run_id"]
            )
            if assessment.forbidden or not assessment.details_complete:
                raise AdmissionError("Fresh action policy no longer permits dispatch.")

            def admit():
                self.store.dispatch(
                    state["run_id"],
                    action_id,
                    action,
                    assessment.effect,
                    fresh["generation"],
                    approval_id,
                    assessment.requires_approval,
                )

            result = await self.browser.execute(
                action["tool"],
                action["args"],
                state["observation"]["id"],
                expected_fingerprint=fresh["fingerprint"],
                before_dispatch=admit,
            )
            self.store.finish_action(
                action_id, "observed", {"dispatch_only": True, "result": result}
            )
            self.emit("tool_result", {"action_id": action_id, "result": result})
            return self.outcome(state, result, route="verify", failures=0)
        except BrowserError as exc:
            if self.store.action(action_id):
                self.store.finish_action(
                    action_id,
                    "uncertain" if exc.uncertain else "failed",
                    {"error": exc.code},
                )
            return self.outcome(
                state,
                {"error": exc.code, "uncertain": exc.uncertain},
                route="recover",
                failures=state.get("failures", 0) + 1,
            )
        except AdmissionError as exc:
            return self.outcome(
                state,
                {"error": "admission_denied", "reason": str(exc)},
                route="recover",
                failures=state.get("failures", 0) + 1,
            )
        except BaseException:
            if self.store.action(action_id):
                self.store.finish_action(action_id, "uncertain", {"interrupted": True})
            raise

    async def verify(self, state):
        update = await self.observe(state)
        if update.get("route") != "decide":
            # verify has a conditional edge too: security challenges must pause immediately.
            return update
        observation = update["observation"]
        durable = self.restore_memory(state)
        progress = list(durable.get("progress", []))
        if state.get("action_id") and not any(
            item["action_id"] == state["action_id"] for item in progress
        ):
            progress.append(
                {
                    "action_id": state["action_id"],
                    "tool": state.get("action", {}).get("tool"),
                    "target": state.get("metadata", {}).get("name", "")[:250],
                    "result_title": observation["title"][:200],
                    "evidence_id": observation["id"],
                    "status": "observed_after_dispatch",
                }
            )
            self.persist_memory(durable | {"progress": progress})
        update.update(
            progress=progress,
            scope=durable.get("scope"),
            notes=durable.get("notes", ""),
        )
        # Attach actual resulting page evidence to the native call result. A URL-only
        # receipt loses the content needed to make the next multi-step decision.
        history = list(state.get("history", []))
        if history and state.get("call"):
            last = [dict(item) for item in history[-1]]
            result = json.loads(last[-1]["output"])
            result["observation"] = {
                "id": observation["id"],
                "url": observation["url"],
                "title": observation["title"],
                "text": observation["text"][:6000],
            }
            last[-1]["output"] = json.dumps(result, ensure_ascii=False)
            history[-1] = last
            update["history"] = history

        def semantic_snapshot(text):
            # Keyboard focus and regenerated references are not task progress.
            return "\n".join(
                line.rstrip()
                for line in re.sub(
                    r"[ \t]*\[(?:ref=[A-Za-z0-9]+|active)\]", "", text
                ).splitlines()
            )

        semantic_text = semantic_snapshot(observation["text"])
        action = state.get("action", {})
        target = state.get("metadata", {})
        signature = json.dumps(
            {
                "tool": action.get("tool"),
                "args": {k: v for k, v in action.get("args", {}).items() if k != "ref"},
                "target": target.get("name"),
                "source_url": state["observation"]["url"],
                "source_text": semantic_snapshot(state["observation"]["text"]),
                "url": observation["url"],
                "text": semantic_text,
            },
            sort_keys=True,
        )
        repetitions = dict(state.get("repetitions", {}))
        import hashlib

        key = hashlib.sha256(signature.encode()).hexdigest()
        repetitions[key] = repetitions.get(key, 0) + 1
        if len(repetitions) > 60:
            repetitions = dict(list(repetitions.items())[-60:])
        update["repetitions"] = repetitions
        if repetitions[key] == 2:
            update["feedback"] = (
                "This same action and resulting page have already occurred twice. Use the visited-page receipts and working notes; choose a different strategy instead of repeating it."
            )
        if repetitions[key] >= 3:
            update.update(
                route="ask",
                question={
                    "kind": "clarification",
                    "question": "The same action produced no new observable progress three times. Please clarify the missing outcome or constraint.",
                },
            )
        return update

    async def recover(self, state):
        self.emit(
            "recover",
            {"reason": state.get("feedback"), "failures": state.get("failures", 0)},
        )
        if state.get("failures", 0) >= 3:
            return {
                "route": "ask",
                "question": {
                    "kind": "recovery",
                    "question": "Three action failures occurred. "
                    + state.get("feedback", "")
                    + " Please resolve the obstruction or clarify the task.",
                },
            }
        return {"route": "observe"}

    async def ask(self, state):
        # Pure interrupt: the caller may leave this saved indefinitely without polling.
        answer = interrupt(state["question"])
        text = answer.get("answer", "") if isinstance(answer, dict) else str(answer)
        feedback = "User clarification: " + text[:3000]
        update = {
            "feedback": feedback,
            "failures": 0,
            "clarifications": state.get("clarifications", []) + [text]
            if text
            else state.get("clarifications", []),
        }
        if state.get("call", {}).get("name") == "ask_user":
            update.update(
                self.outcome(state, {"user_answer": text[:3000]}, feedback=feedback)
            )
        return update

    async def finalize(self, state):
        result = state.get("result")
        if result:
            evidence = {}
            valid = True
            for claim in result["claims"]:
                evidence_id = claim["evidence_id"]
                if evidence_id not in state.get("evidence_ids", []):
                    valid = False
                    break
                obs = json.loads(
                    (self.run_dir / "evidence" / f"{evidence_id}.json").read_text()
                )
                if claim["quote"] not in obs["text"]:
                    valid = False
                    break
                evidence[evidence_id] = obs["text"]
            if result["status"] == "completed" and (
                not valid
                or not evidence
                or result["remaining"]
                or self.store.unresolved_actions(state["run_id"])
            ):
                result = result | {
                    "status": "partial",
                    "remaining": [
                        "Completion evidence is missing, inconsistent, or an action remains uncertain."
                    ],
                }
            elif result["status"] == "completed":
                try:
                    review = await self.gateway.verify_completion(
                        state["task"], result, evidence
                    )
                    if not review["supported"]:
                        result = result | {
                            "status": "partial",
                            "remaining": [review["reason"]],
                        }
                except (
                    ProviderFailure,
                    BudgetExceeded,
                    ProtocolError,
                    ContextOverflow,
                ) as exc:
                    result = result | {
                        "status": "partial",
                        "remaining": ["Completion review unavailable: " + str(exc)],
                    }
        else:
            result = {
                "status": state.get("status", "partial"),
                "summary": state.get(
                    "feedback", "Stopped without verified completion."
                ),
                "claims": [],
                "remaining": ["Task was not verified complete."],
            }
        result = result | {
            "run_id": state["run_id"],
            "steps": state.get("steps", 0),
            "budget": self.store.budget(state["run_id"]),
            "evidence_ids": state.get("evidence_ids", []),
        }
        (self.run_dir / "result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2)
        )
        self.emit("result", result)
        return {"result": result, "status": result["status"]}
