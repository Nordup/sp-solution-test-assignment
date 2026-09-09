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
from urllib.parse import urlsplit, urlunsplit

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from .browser import BrowserError
from .context import (
    COMPLETION_EVIDENCE_BYTES,
    HISTORY_GROUPS,
    ContextOverflow,
    initial_url_source,
    memory_due,
    request,
    task_context,
)
from .llm import ProviderFailure, ScopeObligation, ScopeResolution
from .safety import Policy
from .storage import AdmissionError, BudgetExceeded, DuplicateAction
from .tools import CollectionScope, ProtocolError, parse_call, protocol_pair, tool_specs


class ScopeReviewError(ProtocolError):
    """Grounding feedback belongs to the reviewer that supplied the bad fields."""

    def __init__(self, message, errors):
        super().__init__(message)
        self.errors = errors


class State(TypedDict, total=False):
    run_id: str
    task: str
    initial_url: str | None
    status: str
    observation: dict
    history: list
    notes: str
    scope: dict | None
    progress: list
    memory_step: int
    memory_required: bool
    recalled_evidence_ids: list
    scope_question_reviews: int
    scope_evidence_feedback: dict | None
    scope_review_repairs: int
    scope_obligations: list
    review_obligations: list
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
    completion_repairs: int
    completion_feedback: dict | None
    clarification_repairs: int
    failures: int
    repetitions: dict
    evidence_ids: list
    visited: list
    active_seconds: float
    route: str
    screenshot_path: str | None


def canonical_navigation_url(value):
    """Conservative URL identity; never broaden a path into origin permission."""
    if not isinstance(value, str) or any(
        ord(c) <= 32 or ord(c) == 127 or c == "\\" for c in value
    ):
        return None
    try:
        parts = urlsplit(value)
        if (
            parts.scheme.lower() not in {"http", "https"}
            or not parts.hostname
            or not parts.netloc.isascii()
            or parts.username is not None
            or parts.password is not None
        ):
            return None
        host = parts.hostname.lower()
        if ":" in host:
            host = "[" + host + "]"
        port = parts.port  # Also rejects malformed/out-of-range ports.
        if (
            port is not None
            and port != {"http": 80, "https": 443}[parts.scheme.lower()]
        ):
            host += ":" + str(port)
        return urlunsplit(
            (parts.scheme.lower(), host, parts.path or "/", parts.query, parts.fragment)
        )
    except ValueError:
        return None


def navigation_destination_known(url, state):
    """Grant provenance only from actual user input or current browser evidence.

    Extract complete URL tokens rather than authorizing arbitrary substrings.
    Parentheses/quotes delimit prose and Markdown; ambiguous raw URLs must be
    provided unambiguously/encoded. This is not model-response JSON parsing.
    """
    wanted = canonical_navigation_url(url)
    if wanted is None:
        return False
    observed = state.get("observation", {})
    texts = [
        state.get("task", ""),
        *state.get("clarifications", []),
        observed.get("text", ""),
    ]
    candidates = [state.get("initial_url"), observed.get("url")]
    candidates.extend(tab.get("url") for tab in observed.get("tabs", []))
    for text in texts:
        candidates.extend(
            re.findall(
                r"""(?<![\w:/])https?://[^\s<>"'`\[\](){}]+""",
                text,
                flags=re.IGNORECASE,
            )
        )
    return any(
        canonical_navigation_url(candidate) == wanted for candidate in candidates
    )


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
                "policy": "policy",
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
        graph.add_conditional_edges(
            "finalize", lambda s: s["route"], {"decide": "decide", "done": END}
        )
        return graph.compile(checkpointer=checkpointer)

    def save_observation(self, observation):
        observation.setdefault("saved_at_unix", time.time())
        path = self.run_dir / "evidence" / f"{observation['id']}.json"
        path.write_text(json.dumps(observation, ensure_ascii=False), encoding="utf-8")
        path.chmod(0o600)

    @staticmethod
    def registered_observations(state):
        return set(state.get("evidence_ids", [])) | {
            item["evidence_id"] for item in state.get("visited", [])
        }

    def completion_packet(self, state, proposal, max_bytes=COMPLETION_EVIDENCE_BYTES):
        """Bound actual archived evidence, never summaries or inferred page bodies.

        Whole saved snapshots are admitted in priority order. Omitting a snapshot
        is explicit; its absence cannot establish that a task outcome is absent.
        A saved snapshot may itself be a browser continuation, which remains
        labelled with its original truncation/offset metadata.
        """
        registered = self.registered_observations(state)
        candidates = list(
            dict.fromkeys(
                [claim["evidence_id"] for claim in proposal.get("claims", [])]
                + [
                    item["evidence_id"]
                    for item in (state.get("scope") or {}).get("items", [])
                ]
                + [
                    source["source_id"]
                    for obligation in state.get("scope_obligations", [])
                    for source in obligation.get("evidence", [])
                    if source["source_id"] in registered
                ]
                + [item["evidence_id"] for item in state.get("visited", [])]
                + state.get("evidence_ids", [])
            )
        )
        evidence = {}
        manifest = {
            "sources": {},
            "omitted": [
                {"evidence_id": evidence_id, "reason": "packet_byte_limit"}
                for evidence_id in candidates
            ],
            "byte_limit": max_bytes,
        }

        def size():
            return len(
                json.dumps(
                    {"evidence": evidence, "manifest": manifest}, ensure_ascii=False
                ).encode("utf-8")
            )

        for omission in list(manifest["omitted"]):
            evidence_id = omission["evidence_id"]
            if evidence_id not in registered:
                omission["reason"] = "unregistered_observation"
                continue
            try:
                saved = json.loads(
                    (self.run_dir / "evidence" / f"{evidence_id}.json").read_text()
                )
                if (
                    not isinstance(saved, dict)
                    or saved.get("id") != evidence_id
                    or not isinstance(saved.get("text"), str)
                ):
                    omission["reason"] = "saved_observation_invalid"
                    continue
            except (OSError, ValueError):
                omission["reason"] = "saved_observation_unavailable"
                continue
            source = {
                "provenance": "registered_browser_observation",
                "url": saved.get("url"),
                "title": saved.get("title"),
                "saved_at_unix": saved.get("saved_at_unix"),
                "generation": saved.get("generation"),
                "revision": saved.get("revision"),
                "snapshot_truncated": bool(saved.get("truncated")),
                "offset": saved.get("offset", 0),
                "next_offset": saved.get("next_offset"),
            }
            evidence[evidence_id] = saved["text"]
            manifest["sources"][evidence_id] = source
            manifest["omitted"].remove(omission)
            if size() > max_bytes:
                del evidence[evidence_id]
                del manifest["sources"][evidence_id]
                manifest["omitted"].append(omission)
        # Even an unusually large omission index has a visible bounded summary.
        if size() > max_bytes:
            manifest["omitted_count"] = len(manifest["omitted"])
            manifest["omission_index_truncated"] = True
            while manifest["omitted"] and size() > max_bytes:
                manifest["omitted"].pop()
        return evidence, manifest

    def restore_memory(self, state):
        """Authoritative memory survives old graph checkpoints and browser restart."""
        path = self.run_dir / "memory.json"
        if path.exists():
            saved = json.loads(path.read_text())
            if saved.get("scope"):
                CollectionScope.model_validate(saved["scope"])
            state = state | {
                key: saved.get(key) for key in ("scope", "notes", "progress")
            }
        path = self.run_dir / "scope-obligations.json"
        if path.exists():
            state = state | {"scope_obligations": json.loads(path.read_text())}
        return state

    def obligation_sources(self, state):
        # Only actually delivered observations and actual human inputs are sources.
        sources = {"user_task": state["task"]} | {
            f"user_answer:{index}": answer
            for index, answer in enumerate(state.get("clarifications", []))
        }
        for evidence_id in self.registered_observations(state):
            try:
                obs = json.loads(
                    (self.run_dir / "evidence" / f"{evidence_id}.json").read_text()
                )
                if obs.get("id") == evidence_id and isinstance(obs.get("text"), str):
                    sources[evidence_id] = obs["text"]
            except (OSError, ValueError, AttributeError):
                continue
        return sources

    def update_obligations(self, state, review, sources):
        obligations = list(state.get("scope_obligations", []))

        def checked(items):
            errors = [
                item
                | {
                    "error": "unknown_source_id"
                    if item["source_id"] not in sources
                    else "quote_not_exact_substring"
                }
                for item in items
                if item["source_id"] not in sources
                or item["quote"] not in sources[item["source_id"]]
            ]
            if errors:
                raise ScopeReviewError(
                    "Scope decision requires exact quotes from supplied actual evidence or human answers; notes are not a resolution.",
                    errors,
                )
            return items

        for raw in review.get("scope_resolutions", []):
            resolution = ScopeResolution.model_validate(raw).model_dump()
            checked(resolution["evidence"])
            found = next(
                (
                    item
                    for item in obligations
                    if item["id"] == resolution["obligation_id"]
                ),
                None,
            )
            if found is None:
                raise ScopeReviewError(
                    "Scope resolution names an unknown obligation.",
                    [
                        {
                            "obligation_id": resolution["obligation_id"],
                            "error": "unknown_obligation_id",
                        }
                    ],
                )
            # Preserve the original question and evidence even after resolution.
            obligations[obligations.index(found)] = found | {
                "status": "resolved",
                "resolution": resolution,
            }
        for raw in review.get("new_obligations", []):
            obligation = ScopeObligation.model_validate(raw).model_dump()
            checked(obligation["evidence"])
            if not any(
                item["description"] == obligation["description"] for item in obligations
            ):
                obligations.append(
                    obligation | {"id": uuid.uuid4().hex, "status": "open"}
                )
        if review.get("scope_status") == "uncertain" and not any(
            item["status"] == "open" for item in obligations
        ):
            # Fail closed for older or incomplete reviewer outputs. Keep actual
            # source provenance without pretending the review reason is a quote.
            obs = state["observation"]
            obligations.append(
                {
                    "id": uuid.uuid4().hex,
                    "status": "open",
                    "description": review["reason"],
                    "affects_collection_selection": True,
                    "evidence": [{"source_id": obs["id"], "quote": obs["text"][:1500]}],
                }
            )
        if (
            len(obligations) > 16
            or len(json.dumps(obligations, ensure_ascii=False).encode()) > 16000
        ):
            raise ContextOverflow(
                "Durable scope decisions exceed their bound; no unresolved choice was silently discarded."
            )
        if obligations != state.get("scope_obligations", []):
            self.atomic_json(self.run_dir / "scope-obligations.json", obligations)
            self.emit("scope_obligations", {"obligations": obligations})
        return obligations

    def scope_packet(self, state, max_bytes=32000):
        """Action review shares actual archives, not just the current page."""
        user_sources = {"user_task": state["task"]} | {
            f"user_answer:{index}": answer
            for index, answer in enumerate(state.get("clarifications", []))
        }
        priority = [state["observation"]["id"]] + list(
            reversed(state.get("recalled_evidence_ids", []))
        )
        priority += [
            item["source_id"]
            for item in (state.get("scope_evidence_feedback") or {}).get("evidence", [])
            if item["source_id"] not in user_sources
        ]
        priority += [
            item["evidence_id"] for item in (state.get("scope") or {}).get("items", [])
        ]
        priority += [
            source["source_id"]
            for obligation in state.get("scope_obligations", [])
            for source in obligation.get("evidence", [])
            + obligation.get("resolution", {}).get("evidence", [])
            if source["source_id"] not in user_sources
        ]
        priority += list(reversed(state.get("evidence_ids", [])))
        user_bytes = len(json.dumps(user_sources, ensure_ascii=False).encode())
        if user_bytes + 1000 >= max_bytes:
            raise ContextOverflow(
                "Actual user inputs exceed scope-evidence packet capacity; no user constraint was silently truncated."
            )
        evidence, manifest = self.completion_packet(
            state,
            {"claims": [{"evidence_id": key} for key in dict.fromkeys(priority)]},
            max_bytes=max_bytes - user_bytes - 1000,
        )
        sources = evidence | user_sources
        manifest = manifest | {
            "byte_limit": max_bytes,
            "user_source_ids": list(user_sources),
        }
        if (
            len(
                json.dumps(
                    {"sources": sources, "manifest": manifest}, ensure_ascii=False
                ).encode()
            )
            > max_bytes
        ):
            raise ContextOverflow(
                "Scope-evidence packet exceeds its bound; no snapshot was silently clipped."
            )
        return sources, manifest

    @staticmethod
    def scope_quote_candidates(sources):
        """Small exact-copy fragments; full sources remain the semantic evidence."""
        candidates = []
        for source_id, text in sources.items():
            quotes = []
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                # This is explicitly a fragment, not a claim to contain all facts.
                quote = line[:320]
                candidate = {
                    "source_id": source_id,
                    "exact_fragments": quotes + [quote],
                }
                if (
                    len(
                        json.dumps(
                            candidates + [candidate], ensure_ascii=False
                        ).encode()
                    )
                    > 6000
                ):
                    break
                quotes.append(quote)
            if quotes:
                candidates.append({"source_id": source_id, "exact_fragments": quotes})
        return candidates

    def action_journal(self, state):
        records = []
        omitted = 0
        for receipt in state.get("progress", []):
            row = self.store.action(receipt["action_id"])
            if not row or row["run_id"] != state["run_id"]:
                continue
            details = json.loads(row["details"])
            record = {
                "action_id": row["id"],
                "created": row["created"],
                "status": row["status"],
                "approval_id": row["approval_id"],
                "action": details["action"],
                "destination": details["effect"].get("destination"),
                "result_evidence_id": receipt["evidence_id"],
            }
            if len(json.dumps(records + [record], ensure_ascii=False).encode()) > 12000:
                omitted += 1
            else:
                records.append(record)
        return {
            "records": records,
            "omitted_count": omitted,
            "provenance": "SQLite dispatch journal matched to host-generated post-dispatch observation receipts. Dispatch alone is not semantic success. Records without a post-dispatch receipt are not included; missing records cannot establish authorship.",
        }

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
        self.atomic_json(path, payload)

    def atomic_json(self, path, payload):
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
        if not state.get("scope") and any(
            item["status"] == "open" and item.get("affects_collection_selection", True)
            for item in state.get("scope_obligations", [])
        ):
            raise ProtocolError(
                "The original selection is unresolved. Return scope=null and retain the candidate evidence; do not freeze an explored candidate as the user's selected collection. A later grounded choice can establish it."
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
                return update | await self.admit_clarification(state | update)
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
                    recalled_evidence_ids=(
                        state.get("recalled_evidence_ids", []) + [evidence_id]
                    )[-8:],
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
        started = time.monotonic()
        review_repairs = state.get("scope_review_repairs", 0)
        try:
            action = state["action"]
            if action["tool"] == "navigate":
                url = action["args"]["url"]
                if not navigation_destination_known(url, state):
                    raise BrowserError(
                        "unobserved_destination",
                        "Destination must come from the user or the current page. Follow an observed link ref instead of guessing a URL.",
                    )
            metadata = await self.browser.action_context(
                action["tool"], action["args"], state["observation"]["id"]
            )
            supplied, scope_manifest = self.scope_packet(state)
            review_metadata = metadata | {
                "task_context": task_context(state),
                "scope_sources": supplied,
                "scope_evidence_manifest": scope_manifest,
                "scope_evidence_feedback": state.get("scope_evidence_feedback"),
                "scope_quote_candidates": self.scope_quote_candidates(supplied),
            }
            while True:
                if (
                    state.get("active_seconds", 0) + time.monotonic() - started
                    >= self.settings.active_seconds
                ):
                    return {
                        "route": "finalize",
                        "status": "partial",
                        "scope_review_repairs": review_repairs,
                        "feedback": "Active-time limit reached during scope review; no effect dispatched.",
                    }
                review = await self.gateway.review(
                    state["task"], action, review_metadata
                )
                try:
                    obligations = self.update_obligations(state, review, supplied)
                    break
                except ScopeReviewError as exc:
                    # Never ask the actor to fix a hidden reviewer output. The
                    # rejected fields are bounded by the native evidence schema.
                    feedback = {
                        "errors": exc.errors,
                        "allowed_source_ids": list(supplied),
                        "allowed_obligation_ids": [
                            item["id"] for item in state.get("scope_obligations", [])
                        ],
                        "instruction": "Your scope evidence was rejected. Correct only by copying exact source IDs and unchanged contiguous quotes from scope_sources or the exact-fragment copy aid. Use separate evidence entries for facts on separate lines; never combine or reformat them. Reassess against all original evidence. If ambiguity persists, keep it open; do not fabricate a resolution. A fact not yet looked up is ordinary discovery, not evidence of competing choices.",
                    }
                    self.emit(
                        "scope_review_rejected",
                        feedback | {"repairs_used": review_repairs, "repair_limit": 2},
                    )
                    if review_repairs >= 2:
                        return {
                            "route": "ask",
                            "scope_review_repairs": review_repairs,
                            "question": {
                                "kind": "clarification",
                                "question": "The independent reviewer could not bind its decision to actual source evidence after two repairs. No effect was dispatched. Manual review is needed; this is not a request to approve an ungrounded action.",
                            },
                        }
                    review_repairs += 1
                    review_metadata = review_metadata | {
                        "scope_review_feedback": feedback
                    }
            state = state | {"scope_obligations": obligations}
            assessment = self.safety_policy.assess(
                action, metadata, review, state["run_id"]
            )
            update = {
                "scope_review_repairs": review_repairs,
                "metadata": metadata,
                "review": review,
                "scope_obligations": obligations,
                "review_obligations": obligations,
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
                blocking = [
                    item
                    for item in obligations
                    if item["status"] == "open"
                    and item["id"] not in review.get("unaffected_obligation_ids", [])
                ]
                if blocking:
                    question = {
                        "kind": "clarification",
                        "question": "A material task choice remains unresolved before this change: "
                        + "; ".join(item["description"] for item in blocking),
                    }
                    return update | await self.admit_scope_question(
                        state
                        | update
                        | {
                            "active_seconds": state.get("active_seconds", 0)
                            + time.monotonic()
                            - started
                        },
                        question,
                    )
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
        except (BudgetExceeded, ProviderFailure, ContextOverflow) as exc:
            return {
                "route": "finalize",
                "status": "partial",
                "scope_review_repairs": review_repairs,
                "feedback": str(exc),
            }
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
            if restored.get("scope_obligations", []) != state.get(
                "review_obligations", []
            ):
                raise AdmissionError(
                    "Scope obligations changed since review; reobserve and review before dispatch."
                )
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

    async def admit_scope_question(self, state, question):
        """Check proposed policy handover without inventing an actor tool call."""
        reviews = state.get("scope_question_reviews", 0)
        fallback = {
            "route": "ask",
            "question": question,
            "scope_question_reviews": reviews,
        }
        if (
            reviews >= 2
            or state.get("steps", 0) >= self.settings.max_decisions
            or state.get("active_seconds", 0) >= self.settings.active_seconds
        ):
            return fallback
        try:
            sources, manifest = self.scope_packet(state)
            user_sources = {key: sources[key] for key in manifest["user_source_ids"]}
            evidence = {
                key: value for key, value in sources.items() if key not in user_sources
            }
            review = await self.gateway.review_clarification(
                state["task"],
                question,
                {
                    "task_context": task_context(state),
                    "user_sources": user_sources,
                    "evidence_manifest": manifest,
                },
                evidence,
            )
            reviews += 1
            self.emit(
                "scope_question_review",
                review | {"review_count": reviews, "review_limit": 2},
            )
            if (
                review["classification"] == "already_available"
                and review["evidence"]
                and all(
                    item["source_id"] in sources
                    and item["quote"] in sources[item["source_id"]]
                    for item in review["evidence"]
                )
            ):
                return {
                    "route": "policy",
                    "scope_question_reviews": reviews,
                    "scope_evidence_feedback": {
                        "reason": review["reason"],
                        "evidence": review["evidence"],
                        "instruction": "Independent clarification admission located relevant actual evidence. Reassess the proposed action and open obligations against these exact sources. This is neither a human answer nor an approval and does not itself resolve a choice. Keep any genuine remaining ambiguity open.",
                    },
                }
            return fallback | {"scope_question_reviews": reviews}
        except (ProviderFailure, BudgetExceeded, ProtocolError, ContextOverflow) as exc:
            return fallback | {
                "scope_question_reviews": reviews,
                "question": question
                | {
                    "question": f"Scope clarification check unavailable ({type(exc).__name__}); manual review is needed. "
                    + question["question"]
                },
            }

    async def admit_clarification(self, state):
        """Review actor questions before the pure interrupt, never during resume."""
        question = state["call"]["arguments"]
        if question["kind"] != "clarification":
            return {"route": "ask", "question": question}

        def handover(reason):
            self.emit("clarification_handover", {"reason": reason})
            return {
                "route": "ask",
                "question": question
                | {
                    "question": f"{reason} Manual handover: {question['question']} This question does not approve a browser action; exact effect approval remains required."
                },
            }

        repairs = state.get("clarification_repairs", 0)
        if repairs >= 2:
            return handover("The agent exhausted two clarification repairs.")
        if (
            state.get("steps", 0) >= self.settings.max_decisions
            or state.get("active_seconds", 0) >= self.settings.active_seconds
        ):
            return handover(
                "The decision or active-time limit prevents further clarification review."
            )
        try:
            evidence, manifest = self.completion_packet(
                state, {"claims": [{"evidence_id": state["observation"]["id"]}]}
            )
            user_sources = {"user_task": state["task"]} | {
                f"user_answer:{index}": answer
                for index, answer in enumerate(state.get("clarifications", []))
            }
            initial_url = initial_url_source(state)
            if initial_url:
                user_sources["user_initial_url"] = initial_url
            review = await self.gateway.review_clarification(
                state["task"],
                question,
                {
                    "task_context": task_context(state),
                    "user_sources": user_sources,
                    "evidence_manifest": manifest,
                },
                evidence,
            )
            self.emit("clarification_review", review)
            classification = review["classification"]
            if classification in {"missing_information", "uncertain"}:
                return {"route": "ask", "question": question}
            if classification == "already_available":
                sources = evidence | user_sources
                if not review["evidence"] or any(
                    item["source_id"] not in sources
                    or item["quote"] not in sources[item["source_id"]]
                    for item in review["evidence"]
                ):
                    return handover(
                        "The reviewer could not ground the claimed existing answer in actual supplied sources."
                    )
            elif classification != "action_approval":
                return handover(
                    "The clarification reviewer returned an unsupported classification."
                )
            feedback = {
                "question_admitted": False,
                "classification": classification,
                "reason": review["reason"],
                "evidence": review["evidence"]
                if classification == "already_available"
                else [],
                "repair_attempt": repairs + 1,
                "repair_limit": 2,
                "instruction": "No human answer or approval was supplied. For an action approval request, propose the concrete browser action through its native tool; the host still resolves/reviews the actual effect and requests exact approval before dispatch. Existing denials remain binding. For an already available fact, inspect the cited source and continue from that evidence. Ask a human only for genuinely missing facts, choices or manual authentication.",
            }
            self.emit("clarification_repair", feedback)
            return self.outcome(
                state, feedback, clarification_repairs=repairs + 1, route="decide"
            )
        except (ProviderFailure, BudgetExceeded, ProtocolError, ContextOverflow) as exc:
            # Provider/schema exceptions may contain untrusted response content;
            # expose only the controlled failure class at the human boundary.
            return handover(
                f"Clarification review unavailable ({type(exc).__name__}); no valid semantic review was admitted or completed."
            )

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
        state = self.restore_memory(state)
        started = time.monotonic()
        result = state.get("result")
        if result and result["status"] == "completed":
            evidence = {}
            manifest = {"sources": {}, "omitted": []}
            problems = []
            unavailable = False
            for index, claim in enumerate(result["claims"], start=1):
                evidence_id = claim["evidence_id"]
                if evidence_id not in self.registered_observations(state):
                    problems.append(
                        f"Claim {index}: evidence_id {evidence_id!r} was not observed in this run."
                    )
                    continue
                try:
                    obs = json.loads(
                        (self.run_dir / "evidence" / f"{evidence_id}.json").read_text()
                    )
                except (OSError, ValueError):
                    problems.append(
                        f"Claim {index}: saved observation {evidence_id!r} is unavailable."
                    )
                    continue
                if (
                    not isinstance(obs, dict)
                    or obs.get("id") != evidence_id
                    or not isinstance(obs.get("text"), str)
                ):
                    problems.append(
                        f"Claim {index}: saved observation {evidence_id!r} is invalid."
                    )
                    continue
                if claim["quote"] not in obs["text"]:
                    problems.append(
                        f"Claim {index}: quote is not an exact contiguous substring of observation {evidence_id!r}."
                    )
                    continue
                evidence[evidence_id] = obs["text"]
            if not evidence:
                problems.append(
                    "No valid observed evidence supports the completion claims."
                )
            if result["remaining"]:
                problems.append(
                    "A completed result cannot have remaining work. remaining lists unmet requested work only; deliberately excluded future actions and stopping constraints belong in summary and do not make the requested task partial. Recheck the original requested outcome and boundary before correcting this result. Reported remaining work: "
                    + "; ".join(result["remaining"])
                )
            if self.store.unresolved_actions(state["run_id"]):
                problems.append(
                    "An action remains uncertain; inspect and reconcile it before claiming completion."
                )
            if not problems:
                evidence, manifest = self.completion_packet(state, result)
                if not evidence:
                    problems.append(
                        "No actual saved snapshot fits the bounded evidence packet; inspect smaller scoped observations before claiming completion."
                    )
            if not problems:
                try:
                    review = await self.gateway.verify_completion(
                        state["task"],
                        result
                        | {
                            "task_context": task_context(state),
                            "action_journal": self.action_journal(state),
                            "evidence_manifest": manifest,
                            "context_limitations": "Evidence contains actual previously registered browser snapshots: cited observations first, then original scope and indexed historical pages. The manifest records provenance, chronology, original truncation, and explicit omissions. Historical snapshots establish only what was observed then; do not treat them as the current page or infer missing facts from omitted/truncated snapshots. Preserved scope identifies the original task boundary. Working notes and dispatch receipts are not proof of successful effects; verify all outcome claims against actual observed content.",
                        },
                        evidence,
                    )
                    self.emit("completion_review", review)
                    if not review["supported"]:
                        problems.append(review["reason"])
                    if review["boundary_status"] in {"not_reached", "uncertain"}:
                        problems.append(
                            "Requested stopping boundary is "
                            + review["boundary_status"]
                            + ": "
                            + review["reason"]
                        )
                    if review["remaining_permitted_steps"]:
                        problems.append(
                            "Permitted requested steps still required before completion: "
                            + "; ".join(review["remaining_permitted_steps"])
                        )
                except (
                    ProviderFailure,
                    BudgetExceeded,
                    ProtocolError,
                    ContextOverflow,
                ) as exc:
                    unavailable = True
                    problems.append("Completion review unavailable: " + str(exc))
            if problems:
                repairs = state.get("completion_repairs", 0)
                if (
                    not unavailable
                    and repairs < 2
                    and state.get("steps", 0) < self.settings.max_decisions
                    and state.get("active_seconds", 0) + time.monotonic() - started
                    < self.settings.active_seconds
                    and self.store.budget(state["run_id"])["remaining"] > 0
                ):
                    feedback = {
                        "completion_accepted": False,
                        "problems": problems,
                        "repair_attempt": repairs + 1,
                        "repair_limit": 2,
                        "omitted_evidence": manifest["omitted"],
                        "omission_index_truncated": manifest.get(
                            "omission_index_truncated", False
                        ),
                        "omitted_count": manifest.get(
                            "omitted_count", len(manifest["omitted"])
                        ),
                        "instruction": "Evidence absent from current context or omitted from the bounded packet may still be available through recall and the observed-page index. Retrieve relevant saved content or inspect missing outcome evidence, and address genuinely remaining in-scope work through normal review and approval. Preserve these unresolved problems across memory refreshes. Never repeat an already dispatched effect. Finish with exact supported quotes; finish partial for a genuine blocker or exhausted recovery, not merely because indexed evidence has not yet been recalled.",
                    }
                    self.emit("completion_repair", feedback)
                    return self.outcome(
                        state,
                        feedback,
                        result=None,
                        status="running",
                        completion_repairs=repairs + 1,
                        completion_feedback=feedback,
                        route="decide",
                    )
                result = result | {
                    "status": "partial",
                    "summary": "Completion was not verified: " + "; ".join(problems),
                    "claims": [],
                    "remaining": problems,
                }
        elif not result:
            result = {
                "status": state.get("status", "partial"),
                "summary": state.get(
                    "feedback", "Stopped without verified completion."
                ),
                "claims": [],
                "remaining": ["Task was not verified complete."],
            }
        unresolved = [
            item["description"]
            for item in state.get("scope_obligations", [])
            if item["status"] == "open"
        ]
        if unresolved:
            result = result | {
                "unresolved_decisions": unresolved,
                "summary": result["summary"]
                + "\nUnresolved decisions (no assumption of resolution): "
                + "; ".join(unresolved),
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
        return {
            "result": result,
            "status": result["status"],
            "completion_feedback": None,
            "route": "done",
        }
