"""Strict native tools for the single browser actor."""

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Action(Strict):
    notebook: str = Field(
        min_length=1,
        max_length=6000,
        description="Cumulative factual task notebook: original constraints, item identities, observed facts, completed actions and remaining checklist. Preserve prior useful facts. No reasoning or invented progress.",
    )


class Empty(Action):
    pass


class Ref(Action):
    ref: str = Field(min_length=1, max_length=80)


class Fill(Ref):
    value: str = Field(max_length=8000)


class Press(Ref):
    key: Literal["Enter", "Tab", "Escape", "ArrowDown", "ArrowUp", "Space", "Backspace"]


class Navigate(Action):
    url: str = Field(min_length=8, max_length=3000)


class Read(Action):
    offset: int = Field(ge=0, le=1000000)
    scope: str | None = Field(
        description="Null for whole page, or an exact current element ref. Never a selector. Use next_offset for continuation."
    )


class Scroll(Action):
    direction: Literal["up", "down", "left", "right"]


class Tab(Action):
    page_id: str


class Ask(Action):
    question: str = Field(min_length=1, max_length=1500)
    kind: Literal["clarification", "login", "challenge"]


class Finish(Action):
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
    "navigate": (
        Navigate,
        "Navigate to an HTTP(S) URL you choose, including a public homepage or search engine. Inspect the result; discover site-specific routes from the page.",
    ),
    "new_tab": (Empty, "Open and switch to a new blank browser tab."),
    "back": (Empty, "Go back in the current tab."),
    "forward": (Empty, "Go forward in the current tab."),
    "reload": (Empty, "Reload the current page; may resubmit a previous form."),
    "hover": (
        Ref,
        "Move the pointer over a current observed element to reveal hover controls.",
    ),
    "read": (
        Read,
        "Read a bounded current-page excerpt or subtree. Continue with next_offset.",
    ),
    "scroll": (Scroll, "Scroll one viewport and observe."),
    "tabs": (Empty, "List browser tabs."),
    "switch_tab": (Tab, "Switch to an observed tab ID."),
    "close_tab": (Tab, "Close an observed tab ID."),
    "screenshot": (Empty, "Inspect the actual current viewport visually."),
    "ask_user": (
        Ask,
        "Ask for missing information or manual login/challenge. Action approval is handled by the host; propose the action instead.",
    ),
    "finish": (
        Finish,
        "Report the observed outcome against the user's task and stopping boundary. Include relevant counts and identities. Remaining lists unmet requested work.",
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
    payload = arguments.model_dump()
    parsed = {"name": call["name"], "arguments": payload, "call_id": call["call_id"]}
    if isinstance(arguments, Action):
        parsed["notebook"] = payload.pop("notebook")
    return parsed


def protocol_pair(call, result):
    arguments = dict(call["arguments"])
    if "notebook" in call:
        arguments["notebook"] = call["notebook"]
    return [
        {
            "type": "function_call",
            "name": call["name"],
            "call_id": call["call_id"],
            "arguments": json.dumps(arguments, ensure_ascii=False),
        },
        {
            "type": "function_call_output",
            "call_id": call["call_id"],
            "output": json.dumps(result, ensure_ascii=False),
        },
    ]
