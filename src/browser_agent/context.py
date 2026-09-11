"""Build pinned actor requests and retain native history across compaction."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any

from .prompts import ACTOR
from .protocol import ResponseItem, response_mapping
from .tools import tool_specs


class ContextOverflow(RuntimeError):
    """The request exceeds the configured input allowance."""


def build_request(
    task: str,
    history: list[ResponseItem] | None = None,
    *,
    feedback: str = "",
    instructions: str = "",
    tools: list[ResponseItem] | None = None,
    compaction: ResponseItem | None = None,
    compact_threshold: int = 150_000,
    local_now: datetime | None = None,
) -> dict[str, Any]:
    """Pin the task beside opaque history; browser observations stay in tool results."""
    messages = [compaction] if compaction is not None else []
    messages.extend(
        [
            {"role": "user", "content": f"Task (pinned for this run):\n{task}"},
            *(history or []),
        ]
    )
    if feedback := feedback.strip():
        messages.append({"role": "user", "content": f"Host feedback:\n{feedback}"})
    local_now = local_now or datetime.now().astimezone()
    date_context = (
        f"Current host-local date: {local_now:%Y-%m-%d} (UTC{local_now:%z}). "
        "Resolve relative dates against this date. Verify that time-bound requests "
        "match the observed dates; request clarification when they do not."
    )
    return {
        "instructions": "\n\n".join(
            part for part in (ACTOR, date_context, instructions) if part
        ),
        "input": messages,
        "tools": tool_specs() if tools is None else list(tools),
        "tool_choice": "required",
        "parallel_tool_calls": False,
        "truncation": "disabled",
        "context_management": [
            {"type": "compaction", "compact_threshold": compact_threshold}
        ],
    }


def extract_compaction(response: Any) -> ResponseItem | None:
    """Select the latest valid compaction item and retain only input-safe fields."""
    output = response_mapping(response).get("output") or []
    for item in reversed(output):
        data = response_mapping(item)
        encrypted = data.get("encrypted_content")
        if (
            data.get("type") != "compaction"
            or not isinstance(encrypted, str)
            or not encrypted
        ):
            continue
        compacted = {"type": "compaction", "encrypted_content": encrypted}
        if isinstance(identifier := data.get("id"), str) and identifier:
            compacted["id"] = identifier
        return compacted
    return None


def continuation_items(
    items: list[ResponseItem], compaction: ResponseItem | None
) -> list[ResponseItem]:
    """Retain output after the last compaction boundary, in provider order."""
    if compaction is not None:
        for index in range(len(items) - 1, -1, -1):
            if items[index].get("type") == "compaction":
                return items[index + 1 :]
    return list(items)


def input_item_diagnostics(messages: list[ResponseItem]) -> dict[str, Any]:
    """Count protocol shapes without copying private content."""
    counts: Counter[str] = Counter()
    encrypted_reasoning = 0
    for item in messages:
        kind = str(item.get("type") or item.get("role") or "unknown")[:80]
        counts[kind] += 1
        encrypted_reasoning += kind == "reasoning" and bool(
            item.get("encrypted_content")
        )
    return {
        "input_item_types": dict(counts),
        "encrypted_reasoning_items": encrypted_reasoning,
    }
