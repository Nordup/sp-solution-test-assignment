"""Translate between validated tool calls and native Responses conversation items."""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from typing import Any, TypedDict

from pydantic import ValidationError

from .tools import REGISTRY, ToolRegistry

type ResponseItem = dict[str, Any]


class ToolCall(TypedDict):
    name: str
    arguments: dict[str, Any]
    call_id: str
    response_items: list[ResponseItem]


class ProtocolError(ValueError):
    """The provider response cannot be dispatched as a tool call."""


def response_mapping(value: Any) -> Mapping[str, Any]:
    """Read a native SDK object or a decoded response without changing its data."""
    if isinstance(value, Mapping):
        return value
    if callable(dump := getattr(value, "model_dump", None)):
        return dump(mode="json", exclude_none=True)
    return vars(value) if hasattr(value, "__dict__") else {}


def input_item(item: Any) -> ResponseItem:
    """Keep opaque reasoning intact and omit metadata the input API rejects."""
    clean = {
        key: value for key, value in response_mapping(item).items() if value is not None
    }
    if clean.get("type") in {"reasoning", "message", "function_call"}:
        clean.pop("status", None)
    return clean


def parse_tool_call(response: Any, registry: ToolRegistry | None = None) -> ToolCall:
    """Accept exactly one completed call with strict, declared arguments."""
    definitions = REGISTRY if registry is None else registry
    data = response_mapping(response)
    if not data:
        raise ProtocolError(
            "The model response was not an object; no browser call was dispatched."
        )
    if data.get("status") != "completed":
        status = "incomplete" if data.get("status") == "incomplete" else "not completed"
        raise ProtocolError(
            f"The next model decision was {status}; no new browser call was dispatched. "
            "Prior browser results remain valid; choose a new action."
        )

    output = data.get("output", [])
    if not isinstance(output, list):
        raise ProtocolError(
            "The completed model response had invalid output items; no browser call was dispatched."
        )
    items = [input_item(item) for item in output]
    calls = [item for item in items if item.get("type") == "function_call"]
    if len(calls) != 1:
        raise ProtocolError(
            "The next model decision did not contain exactly one action; no browser call was dispatched."
        )

    call = calls[0]
    name, call_id = call.get("name"), call.get("call_id")
    if (
        not isinstance(name, str)
        or name not in definitions
        or not isinstance(call_id, str)
        or not call_id
    ):
        raise ProtocolError(
            "The next model decision contained an unknown action; no browser call was dispatched."
        )
    try:
        arguments = (
            definitions[name]
            .arguments.model_validate_json(call.get("arguments", ""))
            .model_dump(mode="json")
        )
    except ValidationError as error:
        issue = error.errors(include_input=False)[0]
        field = ".".join(map(str, issue["loc"])) or "arguments"
        message = issue["msg"].replace("\n", " ")[:240]
        raise ProtocolError(
            f"Invalid arguments for {name}: {field}: {message}"
        ) from error
    except (TypeError, ValueError) as error:
        raise ProtocolError(
            f"Invalid arguments for {name}: malformed argument payload"
        ) from error

    if name == "playwright" and arguments["command"].strip().lower() in {
        "evaluate",
        "run_code",
    }:
        raise ProtocolError(
            "The playwright command is not allowed; use a supported literal browser command."
        )
    return ToolCall(
        name=name, arguments=arguments, call_id=call_id, response_items=items
    )


def tool_result_items(
    call: ToolCall, result: Any, response_items: list[ResponseItem] | None = None
) -> list[ResponseItem]:
    """Carry original assistant items, then append the matching tool result once."""
    original = (
        call.get("response_items", []) if response_items is None else response_items
    )
    items = [input_item(item) for item in original]
    if not any(item.get("type") == "function_call" for item in items):
        items.append(
            {
                "type": "function_call",
                "name": call["name"],
                "call_id": call["call_id"],
                "arguments": json.dumps(call["arguments"], ensure_ascii=False),
            }
        )
    items.append(
        {
            "type": "function_call_output",
            "call_id": call["call_id"],
            "output": _result_content(result),
        }
    )
    return items


def _result_content(result: Any) -> str | list[ResponseItem]:
    payload = (
        dict(result)
        if isinstance(result, dict)
        else {"status": "executed", "output": result}
    )
    content = payload.pop("content", [])
    images, text = [], []
    for block in content if isinstance(content, list) else ():
        if image_url := _image_url(block):
            images.append({"type": "input_image", "image_url": image_url})
        elif isinstance(block, dict) and block.get("type") == "text":
            text.append(str(block.get("text", "")))
    if text:
        payload.setdefault("output", "\n".join(text))
    encoded = json.dumps(payload, ensure_ascii=False)
    return [{"type": "input_text", "text": encoded}, *images] if images else encoded


def _image_url(block: Any) -> str | None:
    if not isinstance(block, dict) or block.get("type") != "image":
        return None
    data = block.get("data")
    if not isinstance(data, str) or not data:
        return None
    if data.startswith("data:"):
        return data
    try:
        base64.b64decode(data, validate=True)
    except ValueError:
        return None
    mime = block.get("mimeType") or block.get("mime_type") or "image/png"
    return f"data:{mime};base64,{data}"
