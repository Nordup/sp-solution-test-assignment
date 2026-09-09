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
    offset: int = Field(
        ge=0,
        le=1000000,
        description="Start at 0; for continuation use the observation's next_offset exactly.",
    )
    scope: str | None = Field(
        description="Use null for the whole page, or an exact current element ref from the observation to read its subtree. Never use a CSS selector, role, label, or description such as 'menu'. If the wanted content is beyond a truncated excerpt, use scope=null and its next_offset.",
    )


class Scroll(Strict):
    direction: Literal["up", "down"]


class Tab(Strict):
    page_id: str


class Empty(Strict):
    pass


class Ask(Strict):
    question: str = Field(
        min_length=1,
        max_length=1500,
        description="Ask for a genuinely missing fact, necessary choice, or manual authentication/challenge. Never ask for browser-action approval here: propose that concrete action through its browser tool so the host requests exact approval before dispatch.",
    )
    kind: Literal["clarification", "login", "challenge"]


class ScopeItem(Strict):
    identity: str = Field(
        min_length=1,
        max_length=250,
        description="Exact observed name or URL appearing within the supporting quote; never invent an identifier.",
    )
    evidence_id: str
    quote: str = Field(min_length=3, max_length=700)


class CollectionScope(Strict):
    boundary: str = Field(min_length=1, max_length=1500)
    items: list[ScopeItem] = Field(min_length=1, max_length=60)


class Note(Strict):
    notes: str = Field(
        min_length=1,
        max_length=6000,
        description="Cumulative facts, completed work, pending work and evidence IDs. Preserve earlier facts as rolling history expires.",
    )
    scope: CollectionScope | None = Field(
        description="For a bounded collection defined by the original task, freeze its originally observed identities BEFORE changes shift membership/order. Use exact evidence quotes. Null if not yet observable, not a fixed-collection task, or already frozen. Never redefine an existing scope."
    )


class Claim(Strict):
    claim: str = Field(min_length=1, max_length=1500)
    evidence_id: str
    quote: str = Field(
        min_length=3,
        max_length=1500,
        description="Exact short substring of the supporting saved page content. Quote its actual words, excluding element refs or host-added recall annotations such as '[historical; not actionable]'.",
    )


class Recall(Strict):
    evidence_id: str
    offset: int = Field(ge=0, le=1000000)


class Reconcile(Strict):
    action_id: str
    evidence_id: str
    quote: str = Field(min_length=3, max_length=1500)
    claim: str = Field(min_length=1, max_length=1500)


class Finish(Strict):
    status: Literal["completed", "partial", "failed"] = Field(
        description="Completion is relative to the user's requested outcome AND explicit stopping boundary. Completed means all requested work within that boundary is verified; deliberately excluded future actions do not make the task partial."
    )
    summary: str = Field(min_length=1, max_length=4000)
    claims: list[Claim] = Field(max_length=20)
    remaining: list[str] = Field(
        max_length=20,
        description="Only unmet requested requirements belong here. Use [] when the requested outcome and explicit stopping boundary are satisfied. Never list deliberately excluded/prohibited future actions or safety reminders as unfinished work; state those boundaries in summary instead.",
    )


_PROPOSAL_GATE = " The host resolves the effect, reviews it and requests exact approval when needed BEFORE dispatch. Calling this tool never grants approval."

REGISTRY = {
    "recall": (
        Recall,
        "Recall a saved observation by an evidence ID you previously received. Historical references are not actionable; current browser observation remains authoritative.",
    ),
    "reconcile": (
        Reconcile,
        "Resolve a previously uncertain action ONLY when new observation proves its effect occurred. Never infer no effect from missing evidence; no browser mutation.",
    ),
    "click": (
        Ref,
        "Propose clicking exactly one currently observed element reference."
        + _PROPOSAL_GATE,
    ),
    "fill": (
        Fill,
        "Propose replacing editable field content. Password fields are unavailable."
        + _PROPOSAL_GATE,
    ),
    "select": (
        Fill,
        "Propose selecting a currently observed option value or label."
        + _PROPOSAL_GATE,
    ),
    "press": (
        Press,
        "Propose pressing a restricted key on the observed target." + _PROPOSAL_GATE,
    ),
    "navigate": (
        Navigate,
        "Propose navigating to a user-supplied or observed HTTP(S) destination."
        + _PROPOSAL_GATE,
    ),
    "back": (Empty, "Propose going back one page in current tab." + _PROPOSAL_GATE),
    "read": (
        Read,
        "Read a bounded page excerpt. scope is null or an exact current element ref, never a selector or descriptive label. Continue a truncated excerpt using next_offset.",
    ),
    "scroll": (
        Scroll,
        "Propose scrolling the current page a viewport, then observe." + _PROPOSAL_GATE,
    ),
    "tabs": (Empty, "List known browser tabs."),
    "switch_tab": (
        Tab,
        "Propose switching to an existing observed tab ID." + _PROPOSAL_GATE,
    ),
    "close_tab": (Tab, "Propose closing an existing tab." + _PROPOSAL_GATE),
    "screenshot": (
        Empty,
        "Inspect the current viewport visually when semantic observation is insufficient.",
    ),
    "ask_user": (
        Ask,
        "Pause for a genuinely missing fact or necessary choice, manual login or a security challenge; no polling while paused. Do not request action approval with ask_user: propose the concrete browser action through its native tool instead; the host will request exact approval before dispatch.",
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
