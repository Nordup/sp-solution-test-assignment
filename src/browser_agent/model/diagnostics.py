"""Bounded, reasoning-safe capture of provider response streams."""

import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, TypedDict

from ..protocol import response_mapping

DIAGNOSTIC_TEXT_LIMIT = 12_000
DIAGNOSTIC_ITEM_LIMIT = 16
PARTIAL_HEAD_LIMIT = 4_000
PARTIAL_TAIL_LIMIT = 4_000
MAX_EVENT_TYPES = 64


class BoundedText(TypedDict):
    value: str
    length: int
    truncated: bool


class PartialTextCapture(TypedDict):
    length: int
    head: str
    tail: str
    truncated: bool


@dataclass(frozen=True, slots=True)
class ObservedStreamEvent:
    """The small event surface needed by the response lifecycle."""

    type: str
    has_visible_delta: bool


@dataclass(slots=True)
class _PartialText:
    length: int = 0
    head: str = ""
    tail: str = ""

    def append(self, text: str) -> None:
        self.length += len(text)
        if len(self.head) < PARTIAL_HEAD_LIMIT:
            room = PARTIAL_HEAD_LIMIT - len(self.head)
            self.head += text[:room]
        self.tail = (self.tail + text)[-PARTIAL_TAIL_LIMIT:]

    def snapshot(self) -> PartialTextCapture:
        return {
            "length": self.length,
            "head": self.head,
            "tail": self.tail,
            "truncated": self.length > PARTIAL_HEAD_LIMIT,
        }


@dataclass(slots=True)
class StreamCapture:
    """Track bounded stream progress and expose diagnostic snapshots."""

    event_count: int = 0
    event_type_counts: dict[str, int] = field(default_factory=dict)
    delta_chars_by_type: dict[str, int] = field(default_factory=dict)
    partial_visible: dict[str, _PartialText] = field(default_factory=dict)
    first_sequence: int | None = None
    last_sequence: int | None = None
    last_event_type: str | None = None
    last_event_at_monotonic: float | None = None
    inter_event_gap_seconds: float | None = None
    _previous_event_at: float = field(default_factory=time.monotonic, repr=False)

    def record(self, event: Any) -> ObservedStreamEvent:
        now = time.monotonic()
        raw_type = str(getattr(event, "type", "unknown"))
        diagnostic_type = raw_type[:120]
        self.event_count += 1
        _increment_bounded(self.event_type_counts, diagnostic_type, 1)

        delta = getattr(event, "delta", None)
        if isinstance(delta, str):
            _increment_bounded(self.delta_chars_by_type, diagnostic_type, len(delta))
            partial_key = _partial_key(raw_type)
            if partial_key is not None and delta:
                self.partial_visible.setdefault(partial_key, _PartialText()).append(
                    delta
                )

        provider_sequence = getattr(event, "sequence_number", None)
        if type(provider_sequence) is not int:
            provider_sequence = self.event_count
        if self.first_sequence is None:
            self.first_sequence = provider_sequence
        self.last_sequence = provider_sequence
        self.last_event_type = diagnostic_type
        self.last_event_at_monotonic = now
        self.inter_event_gap_seconds = now - self._previous_event_at
        self._previous_event_at = now
        return ObservedStreamEvent(
            type=raw_type,
            has_visible_delta=(
                raw_type.endswith(".delta") and isinstance(delta, str) and bool(delta)
            ),
        )

    def snapshot(self) -> dict[str, Any]:
        return {
            "event_count": self.event_count,
            "event_type_counts": dict(self.event_type_counts),
            "delta_chars_by_type": dict(self.delta_chars_by_type),
            "partial_visible": {
                key: capture.snapshot() for key, capture in self.partial_visible.items()
            },
            "first_sequence": self.first_sequence,
            "last_sequence": self.last_sequence,
            "last_event_type": self.last_event_type,
            "last_event_at_monotonic": self.last_event_at_monotonic,
            "inter_event_gap_seconds": self.inter_event_gap_seconds,
        }


def bounded_visible(value: Any, limit: int = DIAGNOSTIC_TEXT_LIMIT) -> BoundedText:
    """Capture visible model data with length and truncation metadata."""

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
    """Capture function arguments and visible text while excluding reasoning."""

    data = _safe_mapping(response)
    output = data.get("output")
    if not isinstance(output, Sequence) or isinstance(output, (str, bytes)):
        output = getattr(response, "output", ()) or ()

    items = []
    for item in output[:DIAGNOSTIC_ITEM_LIMIT]:
        captured = _visible_output_item(item)
        if captured is not None:
            items.append(captured)

    output_text = data.get("output_text")
    if not isinstance(output_text, str):
        output_text = getattr(response, "output_text", None)
    capture: dict[str, Any] = {"items": items}
    if isinstance(output_text, str) and output_text:
        capture["output_text"] = bounded_visible(output_text)
    return capture


def bounded_error_fields(value: Any) -> dict[str, Any]:
    """Keep only bounded, provider-defined error fields."""

    data = _safe_mapping(value)
    nested = _safe_mapping(data.get("error"))
    if nested:
        data = {**nested, **data}
    result: dict[str, Any] = {}
    for key in ("code", "message", "param"):
        field_value = data.get(key)
        if isinstance(field_value, str):
            result[key] = field_value[:4_000]
            result[f"{key}_truncated"] = len(field_value) > 4_000
    return result


def response_details(response: Any) -> dict[str, Any]:
    """Capture bounded response identity, status, incompletion, and error data."""

    data = _safe_mapping(response)
    response_id = data.get("id", getattr(response, "id", None))
    status = data.get("status", getattr(response, "status", None))
    incomplete = data.get(
        "incomplete_details", getattr(response, "incomplete_details", None)
    )
    result: dict[str, Any] = {
        "response_id": str(response_id)[:240] if response_id is not None else None,
        "response_status": str(status)[:120] if status is not None else None,
    }
    incomplete_data = _safe_mapping(incomplete)
    if incomplete_data:
        result["incomplete_details"] = {
            key: str(incomplete_data[key])[:240]
            for key in ("reason", "type")
            if incomplete_data.get(key) is not None
        }
    elif incomplete is not None:
        result["incomplete_details"] = str(incomplete)[:240]

    error = bounded_error_fields(data.get("error", getattr(response, "error", None)))
    if error:
        result["response_error"] = error
    return result


def _visible_output_item(item: Any) -> dict[str, Any] | None:
    data = _safe_mapping(item)
    item_type = data.get("type")
    if item_type == "function_call":
        captured: dict[str, Any] = {"type": "function_call"}
        for key in ("name", "call_id"):
            value = data.get(key)
            if isinstance(value, str):
                captured[key] = value[:240]
        if "arguments" in data:
            captured["arguments"] = bounded_visible(data["arguments"])
        return captured
    if item_type not in {"message", "output_text", "text"}:
        return None

    texts = []
    content = data.get("content")
    if isinstance(content, Sequence) and not isinstance(content, (str, bytes)):
        for part in content:
            part_data = _safe_mapping(part)
            if part_data.get("type") in {"output_text", "text"} and isinstance(
                part_data.get("text"), str
            ):
                texts.append(part_data["text"])
    if isinstance(data.get("text"), str):
        texts.append(data["text"])
    if not texts:
        return None
    return {"type": str(item_type)[:80], "text": bounded_visible("".join(texts))}


def _increment_bounded(counts: dict[str, int], key: str, increment: int) -> None:
    if key in counts or len(counts) < MAX_EVENT_TYPES:
        counts[key] = counts.get(key, 0) + increment


def _safe_mapping(value: Any) -> Mapping[str, Any]:
    """Keep diagnostic extraction from changing a valid model outcome."""

    try:
        data = response_mapping(value)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, Mapping) else {}


def _partial_key(event_type: str) -> str | None:
    if "function_call_arguments.delta" in event_type:
        return "function_call_arguments"
    if "output_text.delta" in event_type:
        return "output_text"
    return None
