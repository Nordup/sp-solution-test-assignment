"""Render concise terminal turns from redacted task events.

Show actions, questions, failures, and the final report. Keep successful browser
payloads in the private event log.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any

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

_SKIPPED_STATUSES = {"skipped", "skipped_by_user", "cancelled_by_user"}
_FAILED_STATUSES = {"error", "failed", "failure"}


def _clean(value: Any) -> str:
    """Make untrusted text safe for Rich and terminal output."""

    text = _ANSI.sub("", str(value if value is not None else ""))
    cleaned: list[str] = []
    for char in text:
        category = unicodedata.category(char)
        if char in "\n\t" or category[0] != "C":
            cleaned.append(char)
        elif char == "\r":
            cleaned.append("\n")
        else:
            cleaned.append("�")
    return "".join(cleaned)


def _one_line(value: Any) -> str:
    return " ".join(_clean(value).splitlines()).strip()


def _quoted(value: Any) -> str:
    text = _one_line(value)
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _short(value: Any, limit: int = 220) -> str:
    """Return a bounded single-line value for a normal terminal row."""

    text = _one_line(value)
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"


def _dedupe(values: Sequence[Any], omit: str = "") -> list[str]:
    seen: set[str] = set()
    omitted = _one_line(omit).casefold()
    result: list[str] = []
    for value in values:
        text = _clean(value).strip()
        identity = text.casefold()
        if not text or identity == omitted or identity in seen:
            continue
        seen.add(identity)
        result.append(text)
    return result


def _arguments(record: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return the structured argument object used by a proposal record."""

    value = record.get("arguments")
    if isinstance(value, Mapping):
        return value
    value = record.get("args")
    return value if isinstance(value, Mapping) else {}


def _cli_values(arguments: Mapping[str, Any]) -> list[str]:
    """Extract CLI positional arguments without rendering a JSON dump."""

    values = arguments.get("args", [])
    if isinstance(values, (list, tuple)):
        return [_one_line(value) for value in values if _one_line(value)]
    if values is None:
        return []
    value = _one_line(values)
    return [value] if value else []


def _mapping_message(value: Any) -> str:
    """Find a concise safe message in a host/MCP error payload."""

    if isinstance(value, Mapping):
        for key in ("message", "detail", "reason", "error", "code", "text"):
            candidate = value.get(key)
            if candidate is None or candidate is value:
                continue
            message = _mapping_message(candidate)
            if message:
                return message
        content = value.get("content")
        if content is not None:
            return _mapping_message(content)
        return ""
    if isinstance(value, (list, tuple)):
        for item in value:
            message = _mapping_message(item)
            if message:
                return message
        return ""
    return _one_line(value)


def _error_payload(result: Any) -> tuple[str, str]:
    """Return ``(kind, message)`` for a failed host result, if any."""

    if not isinstance(result, Mapping):
        return "", ""
    # Some transports wrap the native result once more.  Unwrap only this
    # conventional field; arbitrary successful result mappings stay private.
    nested = result.get("result")
    if isinstance(nested, Mapping) and (
        "isError" in nested or "status" in nested or "error" in nested
    ):
        result = nested
    status = _one_line(result.get("status", "")).casefold()
    skipped = status in _SKIPPED_STATUSES
    if skipped:
        return "skip", ""
    explicit_error = result.get("isError") is True or result.get("ok") is False
    error = result.get("error")
    failed = status in _FAILED_STATUSES or explicit_error or error is not None
    if not failed:
        return "", ""
    code = ""
    if isinstance(error, Mapping):
        code = _one_line(error.get("code", ""))
    reason = _mapping_message(error)
    if not reason:
        reason = _mapping_message(result.get("message"))
    if not reason and explicit_error:
        # Native MCP may return only ``isError`` with an empty content list.
        reason = _mapping_message(result.get("content"))
    if not reason:
        reason = "The browser action failed."
    return ("model" if _model_failure(code) else "browser"), reason


def _model_failure(code: Any) -> bool:
    value = _one_line(code).casefold()
    return any(
        marker in value
        for marker in ("model", "provider", "protocol", "invalid_response")
    )


class TerminalUI:
    """Render compact progress, action outcomes, questions, and reports."""

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
        # Captured/piped output must not receive spinner control sequences.
        if not self.interactive:
            return
        status = self.console.status(_clean(message), spinner="dots")
        status.start()
        self._status = status

    def _print(self, value: str, *, style: str | None = None) -> None:
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
        """Print one submitted user turn and close the preceding assistant turn."""

        self._stop_status()
        self._assistant_open = False
        self._last_question = None
        value = _clean(text)
        lines = value.splitlines() or [""]
        rendered = Text("You: ", style="bold cyan")
        rendered.append(lines[0], style="bold")
        for line in lines[1:]:
            rendered.append("\n  " + line, style="bold")
        self.console.print()
        self.console.print(rendered)

    def assistant(self, text: Any) -> None:
        """Print an optional short actor message in the current turn."""

        text = _one_line(text)
        if not text:
            return
        self._start_assistant()
        self.console.print(Padding(Text(self._progress(text, 160)), (0, 0, 0, 2)))

    @staticmethod
    def _format_cli(command: str, values: Sequence[str]) -> str:
        action = _short(command.removeprefix("browser_"), 44) or "browser action"
        values = [_short(value, 96) for value in values if _one_line(value)]
        if not values:
            return f"  → {action}"
        if action in {"fill", "type", "input", "select"} and len(values) >= 2:
            detail = f"{values[0]}: {_quoted(values[1])}"
            if len(values) > 2:
                detail += " · " + " · ".join(values[2:3])
        elif action in {"press", "click", "hover", "focus"} and len(values) >= 2:
            detail = f"{values[0]}: {_short(values[1], 60)}"
        elif action in {"find", "search"}:
            detail = values[0]
        else:
            detail = " · ".join(values[:2])
        return f"  → {action} · {detail}"

    def _tool_row(self, record: Mapping[str, Any]) -> str:
        """Format a single bounded action line; successful results stay silent."""

        tool = _one_line(record.get("tool", "browser action")) or "browser action"
        args = _arguments(record)
        if tool == "playwright":
            command = _one_line(args.get("command") or args.get("action"))
            return self._progress(self._format_cli(command, _cli_values(args)))
        if tool == "read_browser_artifact":
            path = _short(args.get("path", "artifact"), 88)
            offset = args.get("offset")
            detail = path
            if offset is not None and _one_line(offset):
                detail += f" (offset {_short(offset, 18)})"
            return self._progress(f"  → read · {detail}")
        if tool == "search_browser_artifact":
            query = _short(args.get("query", ""), 96) or "artifact"
            return self._progress(f"  → search · {query}")

        # Unknown proposals get a short label without their private arguments.
        return self._progress(f"  → {_short(tool, 44)}")

    def _debug_event(self, event: str, record: Mapping[str, Any]) -> None:
        if not self.debug:
            return
        selected = {
            key: value
            for key, value in record.items()
            if key not in {"task", "notebook", "text", "page_text", "observed_excerpt"}
        }
        self._print(
            "[debug] "
            + event
            + " "
            + _clean(json.dumps(selected, ensure_ascii=False, default=str)),
            style="dim",
        )

    @staticmethod
    def _question_signature(question: Mapping[str, Any]) -> tuple[Any, ...]:
        return (
            question.get("kind"),
            question.get("request_id"),
            _one_line(question.get("question", "")),
        )

    def _approval_prompt(self, question: Mapping[str, Any]) -> str:
        return (
            _clean(question.get("question", "")).strip()
            or "Confirm this browser action?"
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
        kind = str(question.get("kind", "clarification"))
        if kind == "approval":
            self._start_assistant()
            self.console.print(
                Padding(
                    Text(self._approval_prompt(question), style="bold yellow"),
                    (0, 0, 0, 2),
                )
            )
            return
        prompt = question.get("question") or "Please provide the missing information."
        if kind in {"login", "challenge"}:
            prompt = _clean(prompt)
            lowered = prompt.casefold()
            if "reply ready" not in lowered and "visible browser" not in lowered:
                prompt += "\nComplete it in the visible browser, then reply ready."
        self._start_assistant()
        self._print(
            "  Manual step needed:"
            if kind in {"login", "challenge"}
            else "  Input needed:",
            style="bold yellow",
        )
        self.console.print(Padding(Text(_clean(prompt)), (0, 0, 0, 4)))

    def _show_failure(self, reason: Any, *, kind: str = "browser") -> None:
        reason = _short(_mapping_message(reason)) or "The browser action failed."
        identity = f"{kind}:{reason.casefold()}"
        if identity == self._last_error:
            return
        self._last_error = identity
        self._start_assistant()
        prefix = "model error" if kind == "model" else "failed"
        self._print(f"  ↳ {prefix}: {reason}", style="bold red")

    def _show_retry(self, record: Mapping[str, Any]) -> None:
        attempt = (
            _one_line(record.get("attempt"))
            if record.get("attempt") is not None
            else "next"
        )
        purpose = _one_line(record.get("purpose", "model"))
        label = "safety check" if purpose == "security" else "model"
        self._start_assistant()
        self._print(f"  ↳ retrying {label} (attempt {attempt})", style="dim")

    def _show_tool_result(self, record: Mapping[str, Any]) -> None:
        result = record.get("result")
        if not isinstance(result, Mapping):
            result = record
        kind, reason = _error_payload(result)
        if kind == "skip":
            if self._last_error == "skip:skipped_by_user":
                return
            self._last_error = "skip:skipped_by_user"
            self._start_assistant()
            self._print("  ↳ skipped by user", style="yellow")
            return
        if kind:
            self._show_failure(reason, kind=kind)

    def event(self, event: str, redacted_record: Mapping[str, Any] | None) -> None:
        """Render one redacted event; emit optional debug details once."""
        record = redacted_record if isinstance(redacted_record, Mapping) else {}
        match event:
            case "run_started":
                self._last_error = None
                self._last_question = None
                self._start_assistant()
            case "node_started":
                node = _one_line(record.get("node", ""))
                if node in _NODE_STATUS:
                    self._start_status(_NODE_STATUS[node])
            case "node_finished" | "waiting" | "approval_requested" | "run_cancelled":
                self._stop_status()
            case "tool_proposed":
                if _one_line(record.get("tool", "")) in {"finish", "ask_user"}:
                    return
                self._last_error = None
                self._start_assistant()
                self.assistant(record.get("message"))
                self._print("  " + self._tool_row(record).lstrip())
            case "tool_result":
                self._show_tool_result(record)
            case "recovery":
                code = record.get("code", "")
                self._show_failure(
                    record.get("message") or code,
                    kind="model" if _model_failure(code) else "browser",
                )
            case "run_error":
                error = record.get("error")
                reason = record.get("message") or record.get("reason") or error
                if reason:
                    code = error.get("code", "") if isinstance(error, Mapping) else ""
                    self._show_failure(
                        reason, kind="model" if _model_failure(code) else "browser"
                    )
            case "provider_retry":
                self._show_retry(record)
        # Provider diagnostics and successful payloads have no normal UI row.
        self._debug_event(event, record)

    def result(self, result: Mapping[str, Any] | None) -> None:
        self._stop_status()
        data = result if isinstance(result, Mapping) else {}
        summary = _clean(data.get("summary") or "No final summary was produced.")
        remaining = data.get("remaining", [])
        if not isinstance(remaining, (list, tuple)):
            remaining = [remaining] if remaining else []
        items = _dedupe(remaining, omit=summary)
        if items:
            summary += "\n\nRemaining:\n" + "\n".join(f"- {item}" for item in items)
        self._start_assistant()
        self.console.print(Padding(Text(summary), (0, 0, 0, 2)))
        self.console.print()
        if data.get("question"):
            question = data["question"]
            self.question(question if isinstance(question, Mapping) else str(question))

    def close(self) -> None:
        self._stop_status()
