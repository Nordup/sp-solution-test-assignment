"""Closed native function registry. No extraction of JSON from model prose."""

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Ref(Strict):
    ref: str = Field(min_length=1, max_length=80)


class Fill(Ref):
    value: str = Field(max_length=8000)


class Press(Ref):
    key: Literal["Enter", "Tab", "Escape", "ArrowDown", "ArrowUp", "Space", "Backspace"]


class Navigate(Strict):
    url: str = Field(min_length=8, max_length=3000)


class Read(Strict):
    offset: int = Field(ge=0, le=1000000)
    scope: str | None


class Scroll(Strict):
    direction: Literal["up", "down"]


class Tab(Strict):
    page_id: str


class Empty(Strict):
    pass


class Ask(Strict):
    question: str = Field(min_length=1, max_length=1500)
    kind: Literal["clarification", "login", "challenge"]


class Note(Strict):
    notes: str = Field(
        max_length=6000,
        description="Replace working notes with concise cumulative facts and evidence IDs; preserve unresolved constraints.",
    )


class Claim(Strict):
    claim: str = Field(min_length=1, max_length=1500)
    evidence_id: str
    quote: str = Field(min_length=3, max_length=1500)


class Recall(Strict):
    evidence_id: str
    offset: int = Field(ge=0, le=1000000)


class Reconcile(Strict):
    action_id: str
    evidence_id: str
    quote: str = Field(min_length=3, max_length=1500)
    claim: str = Field(min_length=1, max_length=1500)


class Finish(Strict):
    status: Literal["completed", "partial", "failed"]
    summary: str = Field(min_length=1, max_length=4000)
    claims: list[Claim] = Field(max_length=20)
    remaining: list[str] = Field(max_length=20)


REGISTRY = {
    "recall": (
        Recall,
        "Recall a saved observation by an evidence ID you previously received. Historical references are not actionable; current browser observation remains authoritative.",
    ),
    "reconcile": (
        Reconcile,
        "Resolve a previously uncertain action ONLY when new observation proves its effect occurred. Never infer no effect from missing evidence; no browser mutation.",
    ),
    "click": (Ref, "Click exactly one currently observed element reference."),
    "fill": (Fill, "Replace editable field content. Password fields are unavailable."),
    "select": (Fill, "Select a currently observed option value or label."),
    "press": (
        Press,
        "Press a restricted key on the observed target; submissions require review.",
    ),
    "navigate": (
        Navigate,
        "Navigate to a user-supplied or observed HTTP(S) destination.",
    ),
    "back": (Empty, "Go back one page in current tab."),
    "read": (
        Read,
        "Read a bounded page excerpt or observed scope. Continue with next_offset.",
    ),
    "scroll": (Scroll, "Scroll current page a viewport, then observe."),
    "tabs": (Empty, "List known browser tabs."),
    "switch_tab": (Tab, "Switch to an existing observed tab ID."),
    "close_tab": (Tab, "Close an existing tab, subject to review."),
    "screenshot": (
        Empty,
        "Inspect the current viewport visually when semantic observation is insufficient.",
    ),
    "ask_user": (
        Ask,
        "Pause for missing information, manual login or a security challenge; no polling while paused.",
    ),
    "remember": (Note, "Save bounded working notes from observations for long tasks."),
    "finish": (
        Finish,
        "Report actual result with exact quotes from saved observations. Completion requires evidence, not just a successful click.",
    ),
}


class ProtocolError(ValueError):
    pass


def tool_specs(registry=None):
    return [
        {
            "type": "function",
            "name": name,
            "description": desc,
            "strict": True,
            "parameters": cls.model_json_schema(),
        }
        for name, (cls, desc) in (registry or REGISTRY).items()
    ]


def parse_call(response, registry=None):
    registry = registry or REGISTRY
    data = (
        response.model_dump(mode="json")
        if hasattr(response, "model_dump")
        else response
    )
    if data.get("status") != "completed":
        raise ProtocolError(
            "Native response is incomplete or failed; no action dispatched."
        )
    output = data.get("output", [])
    if any(
        c.get("type") == "refusal" for item in output for c in item.get("content", [])
    ):
        raise ProtocolError("Model refused; no action dispatched.")
    calls = [item for item in output if item.get("type") == "function_call"]
    if len(calls) != 1:
        raise ProtocolError(
            "Exactly one native function call is required; no partial dispatch."
        )
    call = calls[0]
    if call.get("name") not in registry or not call.get("call_id"):
        raise ProtocolError("Unknown function or missing native call ID.")
    try:
        payload = registry[call["name"]][0].model_validate_json(call["arguments"])
    except (ValueError, TypeError, KeyError) as exc:
        raise ProtocolError("Tool arguments failed strict schema validation.") from exc
    return {
        "name": call["name"],
        "arguments": payload.model_dump(),
        "call_id": call["call_id"],
    }


def protocol_pair(call, result):
    return [
        {
            "type": "function_call",
            "name": call["name"],
            "call_id": call["call_id"],
            "arguments": json.dumps(call["arguments"], ensure_ascii=False),
        },
        {
            "type": "function_call_output",
            "call_id": call["call_id"],
            "output": json.dumps(result, ensure_ascii=False),
        },
    ]
