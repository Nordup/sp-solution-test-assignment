"""Bounded capture of visible provider response and stream diagnostics."""

import json
import time
from collections.abc import Mapping
from typing import Any

DIAGNOSTIC_TEXT_LIMIT = 12_000
DIAGNOSTIC_ITEM_LIMIT = 16
PARTIAL_HEAD_LIMIT = 4_000
PARTIAL_TAIL_LIMIT = 4_000
MAX_EVENT_TYPES = 64


def object_mapping(value: Any) -> Mapping[str, Any]:
    """Return a shallow provider object mapping without serializing secrets."""

    if isinstance(value, dict):
        return value
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        try:
            data = dump(exclude_none=True)
        except (TypeError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}
    values = getattr(value, "__dict__", None)
    return values if isinstance(values, dict) else {}


def bounded_visible(value: Any, limit: int = DIAGNOSTIC_TEXT_LIMIT) -> dict[str, Any]:
    """Capture visible model text with its length and truncation state."""

    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = str(value)
    return {
        "value": text[:limit],
        "length": len(text),
        "truncated": len(text) > limit,
    }


def visible_response_capture(response: Any) -> dict[str, Any]:
    """Keep function arguments and visible text while excluding reasoning."""

    response_data = object_mapping(response)
    output = response_data.get("output")
    if not isinstance(output, (list, tuple)):
        output = getattr(response, "output", ()) or ()

    items: list[dict[str, Any]] = []
    for item in list(output)[:DIAGNOSTIC_ITEM_LIMIT]:
        item_data = object_mapping(item)
        item_type = item_data.get("type")
        if item_type == "function_call":
            captured: dict[str, Any] = {"type": "function_call"}
            for key in ("name", "call_id"):
                value = item_data.get(key)
                if isinstance(value, str):
                    captured[key] = value[:240]
            if "arguments" in item_data:
                captured["arguments"] = bounded_visible(item_data["arguments"])
            items.append(captured)
            continue
        if item_type not in {"message", "output_text", "text"}:
            continue

        texts: list[str] = []
        content = item_data.get("content")
        if isinstance(content, (list, tuple)):
            for part in content:
                part_data = object_mapping(part)
                if part_data.get("type") in {"output_text", "text"}:
                    text = part_data.get("text")
                    if isinstance(text, str):
                        texts.append(text)
        text = item_data.get("text")
        if isinstance(text, str):
            texts.append(text)
        if texts:
            items.append(
                {
                    "type": str(item_type)[:80],
                    "text": bounded_visible("".join(texts)),
                }
            )

    output_text = response_data.get("output_text")
    if not isinstance(output_text, str):
        output_text = getattr(response, "output_text", None)
    capture: dict[str, Any] = {"items": items}
    if isinstance(output_text, str) and output_text:
        capture["output_text"] = bounded_visible(output_text)
    return capture


def bounded_error_fields(value: Any) -> dict[str, Any]:
    """Keep bounded public provider error fields."""

    data = object_mapping(value)
    nested = object_mapping(data.get("error"))
    if nested:
        data = {**nested, **data}
    result: dict[str, Any] = {}
    for key in ("code", "message", "param"):
        field = data.get(key)
        if isinstance(field, str):
            result[key] = field[:4_000]
            result[f"{key}_truncated"] = len(field) > 4_000
    return result


def response_details(response: Any) -> dict[str, Any]:
    """Capture bounded identifiers, status, incomplete details, and errors."""

    data = object_mapping(response)
    response_id = data.get("id", getattr(response, "id", None))
    status = data.get("status", getattr(response, "status", None))
    details = data.get(
        "incomplete_details", getattr(response, "incomplete_details", None)
    )
    result: dict[str, Any] = {
        "response_id": str(response_id)[:240] if response_id is not None else None,
        "response_status": str(status)[:120] if status is not None else None,
    }
    detail_data = object_mapping(details)
    if detail_data:
        result["incomplete_details"] = {
            key: str(detail_data[key])[:240]
            for key in ("reason", "type")
            if detail_data.get(key) is not None
        }
    elif details is not None:
        result["incomplete_details"] = str(details)[:240]

    error_details = bounded_error_fields(
        data.get("error", getattr(response, "error", None))
    )
    if error_details:
        result["response_error"] = error_details
    return result


def new_stream_progress() -> dict[str, Any]:
    """Return the stable initial diagnostic fields for a response stream."""

    return {
        "event_count": 0,
        "event_type_counts": {},
        "delta_chars_by_type": {},
        "partial_visible": {},
        "first_sequence": None,
        "last_sequence": None,
        "last_event_type": None,
        "last_event_at_monotonic": None,
        "inter_event_gap_seconds": None,
    }


def record_stream_event(
    progress: dict[str, Any],
    event: Any,
    sequence: int,
    last_event_at: float,
) -> float:
    """Update bounded counters and visible deltas for one stream event."""

    now = time.monotonic()
    event_type = str(getattr(event, "type", "unknown"))[:120]
    counts = progress.setdefault("event_type_counts", {})
    if event_type in counts or len(counts) < MAX_EVENT_TYPES:
        counts[event_type] = counts.get(event_type, 0) + 1

    delta = getattr(event, "delta", None)
    delta_counts = progress.setdefault("delta_chars_by_type", {})
    if isinstance(delta, str) and (
        event_type in delta_counts or len(delta_counts) < MAX_EVENT_TYPES
    ):
        delta_counts[event_type] = delta_counts.get(event_type, 0) + len(delta)
    if "function_call_arguments.delta" in event_type:
        _append_partial_visible(progress, "function_call_arguments", delta)
    elif "output_text.delta" in event_type:
        _append_partial_visible(progress, "output_text", delta)

    provider_sequence = getattr(event, "sequence_number", None)
    if not isinstance(provider_sequence, int) or isinstance(provider_sequence, bool):
        provider_sequence = sequence
    if progress.get("first_sequence") is None:
        progress["first_sequence"] = provider_sequence
    progress.update(
        event_count=sequence,
        last_sequence=provider_sequence,
        last_event_type=event_type,
        last_event_at_monotonic=now,
        inter_event_gap_seconds=now - last_event_at,
    )
    return now


def _append_partial_visible(progress: dict[str, Any], key: str, value: Any) -> None:
    if not isinstance(value, str) or not value:
        return
    partial = progress.setdefault("partial_visible", {})
    captured = partial.setdefault(
        key,
        {"length": 0, "head": "", "tail": "", "truncated": False},
    )
    captured["length"] += len(value)
    if len(captured["head"]) < PARTIAL_HEAD_LIMIT:
        room = PARTIAL_HEAD_LIMIT - len(captured["head"])
        captured["head"] += value[:room]
    captured["tail"] = (captured["tail"] + value)[-PARTIAL_TAIL_LIMIT:]
    captured["truncated"] = captured["length"] > PARTIAL_HEAD_LIMIT
