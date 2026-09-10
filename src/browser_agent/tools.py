"""Strict native tools for the single browser actor."""

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Empty(Strict):
    pass


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
    scope: str | None = Field(
        description="Null for whole page, or an exact current element ref. Never a selector. Use next_offset for continuation."
    )


class Scroll(Strict):
    direction: Literal["up", "down"]


class Tab(Strict):
    page_id: str


class Ask(Strict):
    question: str = Field(min_length=1, max_length=1500)
    kind: Literal["clarification", "login", "challenge"]


class Note(Strict):
    notes: str = Field(min_length=1, max_length=6000)


class Finish(Strict):
    status: Literal["completed", "partial", "failed"]
    summary: str = Field(min_length=1, max_length=4000)
    remaining: list[str] = Field(max_length=20)


REGISTRY = {
    "click": (
        Ref,
        "Click a current observed ref. The host asks for exact approval when necessary.",
    ),
    "fill": (Fill, "Replace editable content. Password entry is manual."),
    "select": (Fill, "Select an observed option by value or label."),
    "press": (Press, "Press a permitted key on a current ref."),
    "navigate": (Navigate, "Open a user-supplied or observed HTTP(S) URL."),
    "back": (Empty, "Go back in the current tab."),
    "read": (
        Read,
        "Read a bounded current-page excerpt or subtree. Continue with next_offset.",
    ),
    "scroll": (Scroll, "Scroll one viewport and observe."),
    "tabs": (Empty, "List browser tabs."),
    "switch_tab": (Tab, "Switch to an observed tab ID."),
    "close_tab": (Tab, "Close an observed tab ID."),
    "screenshot": (Empty, "Inspect the actual current viewport visually."),
    "remember": (
        Note,
        "Replace your cumulative notebook with observed facts, selected original items, completed actions and remaining work. Older conversation expires.",
    ),
    "ask_user": (
        Ask,
        "Ask for missing information or manual login/challenge. Action approval is handled by the host; propose the action instead.",
    ),
    "finish": (
        Finish,
        "Report the observed outcome honestly. Completed means the requested work and stopping boundary are reached; remaining lists only unmet requested work.",
    ),
}


class ProtocolError(ValueError):
    pass


def tool_specs(registry=None):
    return [
        {
            "type": "function",
            "name": name,
            "description": description,
            "strict": True,
            "parameters": schema.model_json_schema(),
        }
        for name, (schema, description) in (
            REGISTRY if registry is None else registry
        ).items()
    ]


def parse_call(response, registry=None):
    registry = REGISTRY if registry is None else registry
    data = (
        response.model_dump(mode="json")
        if hasattr(response, "model_dump")
        else response
    )
    if data.get("status") != "completed":
        raise ProtocolError("Incomplete native response; no action executed.")
    calls = [
        item for item in data.get("output", []) if item.get("type") == "function_call"
    ]
    if (
        len(calls) != 1
        or calls[0].get("name") not in registry
        or not calls[0].get("call_id")
    ):
        raise ProtocolError(
            "Exactly one known native tool call with a call ID is required."
        )
    call = calls[0]
    try:
        arguments = registry[call["name"]][0].model_validate_json(call["arguments"])
    except (ValueError, TypeError, KeyError) as exc:
        raise ProtocolError("Native arguments failed strict validation.") from exc
    return {
        "name": call["name"],
        "arguments": arguments.model_dump(),
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
