"""Render compact terminal turns without exposing private event payloads."""

from __future__ import annotations

import re
import unicodedata
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from rich.console import Console
from rich.padding import Padding
from rich.status import Status
from rich.text import Text

_ANSI = re.compile(
    r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\](?:[^\x07\x1b]|\x1b(?!\\))*?(?:\x07|\x1b\\))"
)
_NODE_STATUS = {
    "observe": "Checking the browser",
    "decide": "Planning the next step",
    "review": "Checking the action",
    "execute": "Applying the action",
    "recover": "Recovering from an issue",
}
_SKIPPED_STATUSES = frozenset({"skipped", "skipped_by_user", "cancelled_by_user"})
_FAILED_STATUSES = frozenset({"error", "failed", "failure"})
_MESSAGE_FIELDS = ("message", "detail", "reason", "error", "code", "text")
_MODEL_ERROR_MARKERS = ("model", "provider", "protocol", "invalid_response")
_EDIT_COMMANDS = frozenset({"fill", "type", "input", "select"})
_TARGET_COMMANDS = frozenset({"press", "click", "hover", "focus"})
_SEARCH_COMMANDS = frozenset({"find", "search"})

# Debug output is an explicit projection of host-generated lifecycle data. Event
# records also contain task text, page evidence, arguments, answers, and model
# diagnostics, so broad serialization is never safe here.
_DEBUG_NUMERIC_FIELDS = (
    "step",
    "steps",
    "attempt",
    "delay_seconds",
    "latency_seconds",
    "ttft_seconds",
    "input_tokens",
    "reserved_microusd",
    "cost_microusd",
    "cost_usd",
    "reported_cost_usd",
    "model_attempts",
)
_DEBUG_BOOLEAN_FIELDS = ("needs_approval", "approved", "usage_unknown")
_DEBUG_LABEL_FIELDS = (
    "node",
    "status",
    "response_status",
    "purpose",
    "effort",
    "error_type",
)

FailureKind = Literal["", "skip", "browser", "model"]


def _clean(value: Any) -> str:
    """Remove terminal escapes and replace unsafe control characters."""

    raw = _ANSI.sub("", str(value if value is not None else ""))
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    cleaned: list[str] = []
    for char in raw:
        if char in "\n\t" or not unicodedata.category(char).startswith("C"):
            cleaned.append(char)
        else:
            cleaned.append("�")
    return "".join(cleaned)


def _one_line(value: Any) -> str:
    return " ".join(_clean(value).split()).strip()


def _short(value: Any, limit: int = 220) -> str:
    """Return a bounded single-line representation for a normal UI row."""

    text = _one_line(value)
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"


def _quoted(value: Any) -> str:
    text = _one_line(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


def _dedupe(values: Sequence[Any], *, omit: str = "") -> list[str]:
    omitted = _one_line(omit).casefold()
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        text = _clean(value).strip()
        identity = text.casefold()
        if not text or identity == omitted or identity in seen:
            continue
        seen.add(identity)
        unique.append(text)
    return unique


def _record_arguments(record: Mapping[str, Any]) -> Mapping[str, Any]:
    for field in ("arguments", "args"):
        value = record.get(field)
        if isinstance(value, Mapping):
            return value
    return {}


def _argument_values(arguments: Mapping[str, Any]) -> list[str]:
    raw = arguments.get("args", [])
    if isinstance(raw, (list, tuple)):
        values = raw
    elif raw is None:
        values = []
    else:
        values = [raw]
    return [text for value in values if (text := _one_line(value))]


def _payload_message(payload: Any) -> str:
    """Find the first concise message in a bounded, possibly nested payload."""

    pending = deque([payload])
    visited: set[int] = set()
    inspected = 0
    while pending and inspected < 64:
        value = pending.popleft()
        inspected += 1
        if isinstance(value, Mapping):
            identity = id(value)
            if identity in visited:
                continue
            visited.add(identity)
            candidates = [value.get(field) for field in _MESSAGE_FIELDS]
            if "content" in value:
                candidates.append(value["content"])
            pending.extend(
                candidate for candidate in candidates if candidate is not None
            )
            continue
        if isinstance(value, (list, tuple)):
            identity = id(value)
            if identity in visited:
                continue
            visited.add(identity)
            pending.extend(value)
            continue
        message = _one_line(value)
        if message:
            return message
    return ""


def _is_model_failure(code: Any) -> bool:
    normalized = _one_line(code).casefold()
    return any(marker in normalized for marker in _MODEL_ERROR_MARKERS)


@dataclass(frozen=True)
class _ActionOutcome:
    kind: FailureKind = ""
    reason: str = ""

    @classmethod
    def from_result(cls, value: Any) -> _ActionOutcome:
        if not isinstance(value, Mapping):
            return cls()

        result = value
        nested = value.get("result")
        if isinstance(nested, Mapping) and any(
            marker in nested for marker in ("isError", "status", "error")
        ):
            result = nested

        status = _one_line(result.get("status", "")).casefold()
        if status in _SKIPPED_STATUSES:
            return cls("skip")

        error = result.get("error")
        failed = (
            status in _FAILED_STATUSES
            or result.get("isError") is True
            or result.get("ok") is False
            or error is not None
        )
        if not failed:
            return cls()

        code = error.get("code", "") if isinstance(error, Mapping) else ""
        reason = (
            _payload_message(error)
            or _payload_message(result.get("message"))
            or _payload_message(result.get("content"))
            or "The browser action failed."
        )
        return cls("model" if _is_model_failure(code) else "browser", reason)


@dataclass(frozen=True)
class _ToolProposal:
    name: str
    arguments: Mapping[str, Any]

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> _ToolProposal:
        name = _one_line(record.get("tool", "browser action")) or "browser action"
        return cls(name=name, arguments=_record_arguments(record))

    def render(self) -> str:
        if self.name == "playwright":
            command = _one_line(
                self.arguments.get("command") or self.arguments.get("action")
            )
            return _format_cli_action(command, _argument_values(self.arguments))
        if self.name == "read_browser_artifact":
            path = _short(self.arguments.get("path", "artifact"), 88)
            offset = self.arguments.get("offset")
            suffix = (
                f" (offset {_short(offset, 18)})"
                if offset is not None and _one_line(offset)
                else ""
            )
            return f"  → read · {path}{suffix}"
        if self.name == "search_browser_artifact":
            query = _short(self.arguments.get("query", ""), 96) or "artifact"
            return f"  → search · {query}"
        return f"  → {_short(self.name, 44)}"


def _format_cli_action(command: str, values: Sequence[str]) -> str:
    action = _short(command.removeprefix("browser_"), 44) or "browser action"
    details = [_short(value, 96) for value in values if _one_line(value)]
    if not details:
        return f"  → {action}"
    if action in _EDIT_COMMANDS and len(details) >= 2:
        detail = f"{details[0]}: {_quoted(details[1])}"
        if len(details) > 2:
            detail += f" · {details[2]}"
    elif action in _TARGET_COMMANDS and len(details) >= 2:
        detail = f"{details[0]}: {_short(details[1], 60)}"
    elif action in _SEARCH_COMMANDS:
        detail = details[0]
    else:
        detail = " · ".join(details[:2])
    return f"  → {action} · {detail}"


def _debug_fields(record: Mapping[str, Any]) -> list[str]:
    selected: list[str] = []
    for field in _DEBUG_NUMERIC_FIELDS:
        value = record.get(field)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            selected.append(f"{field}={value}")
    for field in _DEBUG_BOOLEAN_FIELDS:
        value = record.get(field)
        if isinstance(value, bool):
            selected.append(f"{field}={str(value).lower()}")
    for field in _DEBUG_LABEL_FIELDS:
        value = record.get(field)
        if isinstance(value, str) and value:
            selected.append(f"{field}={_short(value, 48)}")
    return selected


class TerminalUI:
    """Render action rows, human turns, failures, and the final report."""

    def __init__(self, console: Console | None = None, *, debug: bool = False) -> None:
        self.console = console or Console()
        self.debug = debug
        self._status: Status | None = None
        self._last_error: str | None = None
        self._last_question: tuple[Any, ...] | None = None
        self._assistant_open = False

    @property
    def interactive(self) -> bool:
        return bool(getattr(self.console, "is_terminal", False))

    def _stop_status(self) -> None:
        status, self._status = self._status, None
        if status is not None:
            status.stop()

    def _start_status(self, message: str) -> None:
        self._stop_status()
        if not self.interactive:
            return
        status = self.console.status(_clean(message), spinner="dots")
        status.start()
        self._status = status

    def _print(self, value: Any, *, style: str | None = None) -> None:
        self.console.print(Text(_clean(value), style=style))

    def _progress(self, value: Any, limit: int = 112) -> str:
        width = int(getattr(self.console, "width", 80) or 80)
        return _short(value, min(limit, max(24, width - 4)))

    def welcome(self) -> None:
        self._print(
            "Browser workspace ready. Enter a task; keep this terminal beside the visible browser."
        )
        self._print(
            "Press Esc twice within half a second to stop a task. Use /exit to close the session.",
            style="dim",
        )
        self.console.print()

    def _start_assistant(self) -> None:
        if self._assistant_open:
            return
        self.console.print()
        self._print("Agent:", style="bold cyan")
        self._assistant_open = True

    def user(self, text: Any) -> None:
        """Render one highlighted user turn and start a fresh assistant turn."""

        self._stop_status()
        self._assistant_open = False
        self._last_question = None
        lines = _clean(text).splitlines() or [""]
        rendered = Text("You: ", style="bold cyan")
        rendered.append(lines[0], style="bold")
        for line in lines[1:]:
            rendered.append(f"\n  {line}", style="bold")
        self.console.print()
        self.console.print(rendered)

    def assistant(self, text: Any) -> None:
        """Render one short actor message inside the current assistant turn."""

        message = _one_line(text)
        if not message:
            return
        self._start_assistant()
        self.console.print(Padding(Text(self._progress(message, 160)), (0, 0, 0, 2)))

    @staticmethod
    def _format_cli(command: str, values: Sequence[str]) -> str:
        return _format_cli_action(command, values)

    def _tool_row(self, record: Mapping[str, Any]) -> str:
        """Format one bounded action row while keeping result payloads private."""

        return self._progress(_ToolProposal.from_record(record).render())

    def _debug_event(self, event: str, record: Mapping[str, Any]) -> None:
        if not self.debug:
            return
        fields = _debug_fields(record)
        suffix = f" {' '.join(fields)}" if fields else ""
        self._print(f"[debug] {_short(event, 48)}{suffix}", style="dim")

    @staticmethod
    def _question_signature(question: Mapping[str, Any]) -> tuple[Any, ...]:
        return (
            question.get("kind"),
            question.get("request_id"),
            _one_line(question.get("question", "")),
        )

    def question(self, question: Mapping[str, Any] | str) -> None:
        self._stop_status()
        if not isinstance(question, Mapping):
            self._start_assistant()
            self.console.print(Padding(Text(_clean(question)), (0, 0, 0, 2)))
            return

        signature = self._question_signature(question)
        if signature == self._last_question:
            return
        self._last_question = signature
        kind = _one_line(question.get("kind", "clarification"))
        prompt = _clean(question.get("question", "")).strip()

        self._start_assistant()
        if kind == "approval":
            self.console.print(
                Padding(
                    Text(prompt or "Confirm this browser action?", style="bold yellow"),
                    (0, 0, 0, 2),
                )
            )
            return

        manual_step = kind in {"login", "challenge"}
        if (
            manual_step
            and "reply ready" not in prompt.casefold()
            and "visible browser" not in prompt.casefold()
        ):
            prompt += "\nComplete it in the visible browser, then reply ready."
        self._print(
            "  Manual step needed:" if manual_step else "  Input needed:",
            style="bold yellow",
        )
        self.console.print(
            Padding(
                Text(prompt or "Please provide the missing information."),
                (0, 0, 0, 4),
            )
        )

    def _show_failure(self, reason: Any, *, kind: str = "browser") -> None:
        message = _short(_payload_message(reason)) or "The browser action failed."
        identity = f"{kind}:{message.casefold()}"
        if identity == self._last_error:
            return
        self._last_error = identity
        self._start_assistant()
        label = "model error" if kind == "model" else "failed"
        self._print(f"  ↳ {label}: {message}", style="bold red")

    def _show_retry(self, record: Mapping[str, Any]) -> None:
        attempt = _one_line(record.get("attempt", "next")) or "next"
        purpose = _one_line(record.get("purpose", "model"))
        label = "safety check" if purpose == "security" else "model"
        self._start_assistant()
        self._print(f"  ↳ retrying {label} (attempt {attempt})", style="dim")

    def _show_tool_result(self, record: Mapping[str, Any]) -> None:
        result = record.get("result")
        outcome = _ActionOutcome.from_result(
            result if isinstance(result, Mapping) else record
        )
        if outcome.kind == "skip":
            if self._last_error == "skip:skipped_by_user":
                return
            self._last_error = "skip:skipped_by_user"
            self._start_assistant()
            self._print("  ↳ skipped by user", style="yellow")
        elif outcome.kind:
            self._show_failure(outcome.reason, kind=outcome.kind)

    def event(self, event: str, redacted_record: Mapping[str, Any] | None) -> None:
        """Render one already-secret-redacted event through explicit safe fields."""

        record = redacted_record if isinstance(redacted_record, Mapping) else {}
        if event == "run_started":
            self._last_error = None
            self._last_question = None
            self._start_assistant()
        elif event == "node_started":
            node = _one_line(record.get("node", ""))
            if message := _NODE_STATUS.get(node):
                self._start_status(message)
        elif event in {
            "node_finished",
            "waiting",
            "approval_requested",
            "run_cancelled",
        }:
            self._stop_status()
        elif event == "tool_proposed":
            if _one_line(record.get("tool", "")) in {"finish", "ask_user"}:
                return
            self._last_error = None
            self._start_assistant()
            self.assistant(record.get("message"))
            self._print("  " + self._tool_row(record).lstrip())
        elif event == "tool_result":
            self._show_tool_result(record)
        elif event == "recovery":
            code = record.get("code", "")
            self._show_failure(
                record.get("message") or code,
                kind="model" if _is_model_failure(code) else "browser",
            )
        elif event == "run_error":
            error = record.get("error")
            reason = record.get("message") or record.get("reason") or error
            if reason:
                code = error.get("code", "") if isinstance(error, Mapping) else ""
                self._show_failure(
                    reason,
                    kind="model" if _is_model_failure(code) else "browser",
                )
        elif event == "provider_retry":
            self._show_retry(record)

        # Diagnostics and successful results never acquire a broad debug dump.
        self._debug_event(event, record)

    def result(self, result: Mapping[str, Any] | None) -> None:
        self._stop_status()
        data = result if isinstance(result, Mapping) else {}
        summary = _clean(data.get("summary") or "No final summary was produced.")
        remaining = data.get("remaining", [])
        if not isinstance(remaining, (list, tuple)):
            remaining = [remaining] if remaining else []
        if items := _dedupe(remaining, omit=summary):
            summary += "\n\nRemaining:\n" + "\n".join(f"- {item}" for item in items)
        self._start_assistant()
        self.console.print(Padding(Text(summary), (0, 0, 0, 2)))
        self.console.print()
        if question := data.get("question"):
            self.question(question if isinstance(question, Mapping) else str(question))

    def close(self) -> None:
        self._stop_status()
