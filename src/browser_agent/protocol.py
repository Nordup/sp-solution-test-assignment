"""Validate Responses tool calls and preserve their native conversation items."""

from __future__ import annotations

import base64
import json
from typing import Any, TypedDict

from pydantic import ValidationError

from .tools import REGISTRY, ToolRegistry


class ToolCall(TypedDict):
    name: str
    arguments: dict[str, Any]
    call_id: str
    response_items: list[dict[str, Any]]


class ProtocolError(ValueError):
    """A model response cannot be dispatched safely."""


def _response_dict(response: Any) -> Any:
    if hasattr(response, "model_dump"):
        # Output-only nullable fields are invalid when carried back as API input.
        return response.model_dump(mode="json", exclude_none=True)
    return response


def _response_item_for_input(item: Any) -> Any:
    """Strip output-only metadata while preserving opaque reasoning and item order."""

    if not isinstance(item, dict):
        return item
    clean = {key: value for key, value in item.items() if value is not None}
    if clean.get("type") in {"reasoning", "message", "function_call"}:
        clean.pop("status", None)
    return clean


def parse_tool_call(response: Any, registry: ToolRegistry | None = None) -> ToolCall:
    """Validate one completed call; report errors without echoing private arguments."""

    selected = REGISTRY if registry is None else registry
    data = _response_dict(response)
    if not isinstance(data, dict):
        raise ProtocolError(
            "The model response was not an object; no browser call was dispatched."
        )
    status = data.get("status")
    if status != "completed":
        if status == "incomplete":
            raise ProtocolError(
                "The next model decision was incomplete; no new browser call was dispatched. "
                "Prior browser results remain valid; choose a new action."
            )
        raise ProtocolError(
            "The next model decision was not completed; no new browser call was dispatched. "
            "Prior browser results remain valid."
        )
    output = data.get("output", [])
    if not isinstance(output, list):
        raise ProtocolError(
            "The completed model response had invalid output items; no browser call was dispatched."
        )
    calls = [
        item
        for item in output
        if isinstance(item, dict) and item.get("type") == "function_call"
    ]
    if len(calls) != 1:
        raise ProtocolError(
            "The next model decision did not contain exactly one action; no browser call was dispatched."
        )
    call = calls[0]
    name = call.get("name")
    if name not in selected or not call.get("call_id"):
        raise ProtocolError(
            "The next model decision contained an unknown action; no browser call was dispatched."
        )
    try:
        model = selected[name][0].model_validate_json(call.get("arguments", ""))
    except ValidationError as exc:
        error = exc.errors()[0] if exc.errors() else {}
        location = ".".join(str(part) for part in error.get("loc", ())) or "arguments"
        message = str(error.get("msg") or "invalid value").replace("\n", " ")[:240]
        raise ProtocolError(
            f"Invalid arguments for {name}: {location}: {message}"
        ) from exc
    except (TypeError, KeyError, ValueError, json.JSONDecodeError) as exc:
        raise ProtocolError(
            f"Invalid arguments for {name}: malformed argument payload"
        ) from exc
    arguments = model.model_dump(mode="json")
    if name == "playwright" and arguments["command"].strip().lower() in {
        "evaluate",
        "run_code",
    }:
        raise ProtocolError(
            "The playwright command is not allowed; use a supported literal browser command."
        )
    return {
        "name": name,
        "arguments": arguments,
        "call_id": call["call_id"],
        # Preserve reasoning and assistant items without exposing them in UI events.
        "response_items": [_response_item_for_input(item) for item in output],
    }


def _content_image(value: Any) -> str | None:
    if not isinstance(value, dict) or value.get("type") != "image":
        return None
    data = value.get("data")
    mime = value.get("mimeType") or value.get("mime_type") or "image/png"
    if not isinstance(data, str) or not data:
        return None
    # Transports commonly return base64, but accept an already formed data URL
    # so fake transports and future Playwright versions can pass it through.
    if data.startswith("data:"):
        return data
    try:
        base64.b64decode(data, validate=True)
    except (ValueError, TypeError):
        return None
    return f"data:{mime};base64,{data}"


def _pair_result(result: Any) -> str | list[dict[str, Any]]:
    """Return text output plus image blocks without embedding binary in JSON."""

    if not isinstance(result, dict):
        result = {"status": "executed", "output": result}
    images = []
    clean = dict(result)
    content = clean.pop("content", None)
    if isinstance(content, list):
        text_content = []
        for block in content:
            image = _content_image(block)
            if image is not None:
                images.append(image)
            elif isinstance(block, dict) and block.get("type") == "text":
                text_content.append(block.get("text", ""))
        if text_content and "output" not in clean:
            clean["output"] = "\n".join(str(item) for item in text_content)
    blocks = [{"type": "input_text", "text": json.dumps(clean, ensure_ascii=False)}]
    blocks.extend({"type": "input_image", "image_url": image} for image in images)
    return blocks if images else blocks[0]["text"]


def tool_result_items(
    call: ToolCall, result: Any, response_items: list[dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    """Append the matching result after the response's original, opaque output items."""

    original = response_items
    if original is None:
        original = call.get("response_items")
    if isinstance(original, list) and original:
        assistant_items = [_response_item_for_input(item) for item in original]
        if not any(
            isinstance(item, dict) and item.get("type") == "function_call"
            for item in assistant_items
        ):
            assistant_items.append(
                {
                    "type": "function_call",
                    "name": call["name"],
                    "call_id": call["call_id"],
                    "arguments": json.dumps(call["arguments"], ensure_ascii=False),
                }
            )
    else:
        assistant_items = [
            {
                "type": "function_call",
                "name": call["name"],
                "call_id": call["call_id"],
                "arguments": json.dumps(call["arguments"], ensure_ascii=False),
            }
        ]
    return assistant_items + [
        {
            "type": "function_call_output",
            "call_id": call["call_id"],
            "output": _pair_result(result),
        }
    ]
