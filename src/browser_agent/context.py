"""Build actor requests and carry opaque Responses history across decisions."""

from __future__ import annotations

from typing import Any

from .prompts import ACTOR
from .tools import tool_specs


class ContextOverflow(RuntimeError):
    """The request exceeds the configured input allowance."""


def build_request(
    task: str,
    history: list[dict[str, Any]] | None = None,
    *,
    feedback: str = "",
    instructions: str = "",
    tools: list[dict[str, Any]] | None = None,
    compaction: dict[str, Any] | None = None,
    compact_threshold: int = 150_000,
) -> dict[str, Any]:
    """Pin the task; add browser observations only through native tool results."""
    messages = []
    if compaction is not None:
        messages.append(compaction)
    messages.append({"role": "user", "content": f"Task (pinned for this run):\n{task}"})
    messages.extend(history or [])
    if feedback.strip():
        messages.append(
            {"role": "user", "content": f"Host feedback:\n{feedback.strip()}"}
        )
    return {
        "instructions": ACTOR + ("\n\n" + instructions if instructions else ""),
        "input": messages,
        "tools": list(tools) if tools is not None else tool_specs(),
        "tool_choice": "required",
        "parallel_tool_calls": False,
        "truncation": "disabled",
        "context_management": [
            {"type": "compaction", "compact_threshold": compact_threshold}
        ],
    }


def extract_compaction(response: Any) -> dict[str, Any] | None:
    """Return the opaque native compaction item from a completed actor response."""
    latest = None
    output = (
        response.get("output", ())
        if isinstance(response, dict)
        else getattr(response, "output", ())
    )
    for item in output or ():
        if isinstance(item, dict):
            data = item
        else:
            dump = getattr(item, "model_dump", None)
            if dump is not None:
                data = dump(exclude_none=True)
            elif hasattr(item, "__dict__"):
                data = vars(item)
            else:
                continue
        if data.get("type") != "compaction":
            continue
        encrypted = data.get("encrypted_content")
        if not isinstance(encrypted, str) or not encrypted:
            continue
        # Responses output items may include ``created_by`` metadata.  The
        # input schema accepts only this opaque payload (and its optional id),
        # so preserve the ciphertext byte-for-byte while stripping output-only
        # fields before carrying the item into the next request.
        latest = {"type": "compaction", "encrypted_content": encrypted}
        if isinstance(data.get("id"), str) and data["id"]:
            latest["id"] = data["id"]
    return latest


def input_item_diagnostics(messages: list[dict[str, Any]]) -> dict[str, Any]:
    """Return protocol-shape counts without copying private item content."""

    item_types = {}
    encrypted_reasoning = 0
    for item in messages if isinstance(messages, list) else ():
        if not isinstance(item, dict):
            continue
        item_type = item.get("type") or item.get("role") or "unknown"
        item_type = str(item_type)[:80]
        item_types[item_type] = item_types.get(item_type, 0) + 1
        if item_type == "reasoning" and item.get("encrypted_content"):
            encrypted_reasoning += 1
    return {
        "input_item_types": item_types,
        "encrypted_reasoning_items": encrypted_reasoning,
    }


def continuation_items(
    items: list[dict[str, Any]], compaction: dict[str, Any] | None
) -> list[dict[str, Any]]:
    """Keep output emitted after the latest native compaction boundary."""

    items = list(items or [])
    if compaction is None:
        return items
    boundaries = [
        index
        for index, item in enumerate(items)
        if isinstance(item, dict) and item.get("type") == "compaction"
    ]
    return items[boundaries[-1] + 1 :] if boundaries else items
