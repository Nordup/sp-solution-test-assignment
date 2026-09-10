"""The small native tool surface exposed to the browser actor.

Browser control is intentionally one Playwright CLI invocation.  The CLI owns
the command grammar and the browser session; this module only validates the
shape of model calls and pairs the CLI's actual result with the Responses
history.  Keeping the protocol here small avoids a second, subtly different
browser API in the agent.
"""

from __future__ import annotations

import base64
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Playwright(Strict):
    command: str = Field(
        min_length=1,
        max_length=160,
        description="One supported Playwright CLI command, such as open, find, fill, click, snapshot, or screenshot.",
    )
    args: list[str] = Field(
        max_length=32,
        description="The literal positional arguments for that command; do not write a shell command or flags that run code.",
    )


class ReadBrowserArtifact(Strict):
    path: str = Field(min_length=1, max_length=4000)
    offset: int = Field(ge=0, le=1_000_000)


class AskUser(Strict):
    question: str = Field(min_length=1, max_length=1500)
    kind: Literal["clarification", "login", "challenge"]


class Finish(Strict):
    status: Literal["completed", "partial", "failed"]
    summary: str = Field(min_length=1, max_length=4000)
    remaining: list[str] = Field(max_length=20)


class SecurityReview(Strict):
    """The private reviewer answers only whether approval is needed."""

    needs_approval: bool


SECURITY_REGISTRY = {
    "security_review": (
        SecurityReview,
        "Classify the immediate effect of the proposed Playwright command. Return only needs_approval.",
    )
}


REGISTRY = {
    "playwright": (
        Playwright,
        "Run exactly one supported Playwright CLI command with literal arguments. The host handles safety approval before any consequential effect.",
    ),
    "read_browser_artifact": (
        ReadBrowserArtifact,
        "Read a bounded snapshot, text, or image artifact produced by Playwright CLI. Use this only when the command returned an artifact path.",
    ),
    "ask_user": (
        AskUser,
        "Ask only for missing information or manual login/security help that blocks the task. Approval is handled inside the intended browser action.",
    ),
    "finish": (
        Finish,
        "Report the observed outcome against the user's task and stopping boundary.",
    ),
}


class ProtocolError(ValueError):
    """A model response cannot be dispatched safely."""


def tool_specs(registry=None):
    """Return Responses function definitions for the actor or reviewer."""

    selected = REGISTRY if registry is None else registry
    return [
        {
            "type": "function",
            "name": name,
            "description": description,
            "strict": True,
            "parameters": schema.model_json_schema(),
        }
        for name, (schema, description) in selected.items()
    ]


def _response_dict(response):
    if hasattr(response, "model_dump"):
        return response.model_dump(mode="json")
    return response


def parse_call(response, registry=None):
    """Parse exactly one completed native function call.

    The error intentionally names only the tool and a field location.  It is
    useful feedback for the next model decision without echoing arbitrary
    account text or a malformed argument payload.
    """

    selected = REGISTRY if registry is None else registry
    data = _response_dict(response)
    if not isinstance(data, dict):
        raise ProtocolError("The model response was not an object; no browser call was dispatched.")
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
    calls = [item for item in data.get("output", []) if item.get("type") == "function_call"]
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
        raise ProtocolError(f"Invalid arguments for {name}: {location}: {message}") from exc
    except (TypeError, KeyError, ValueError, json.JSONDecodeError) as exc:
        raise ProtocolError(f"Invalid arguments for {name}: malformed argument payload") from exc
    arguments = model.model_dump(mode="json")
    if name == "playwright" and arguments["command"].strip().lower() in {"evaluate", "run_code"}:
        raise ProtocolError(
            "The playwright command is not allowed; use a supported literal browser command."
        )
    return {"name": name, "arguments": arguments, "call_id": call["call_id"]}


def _content_image(value):
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


def _pair_result(result):
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


def protocol_pair(call, result):
    """Pair a proposed call with the host's exact, actual result."""

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
            "output": _pair_result(result),
        },
    ]
