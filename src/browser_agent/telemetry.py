"""Private local events with optional metrics-only LangSmith export."""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import AsyncIterator, Callable, Iterator, Mapping
from contextlib import asynccontextmanager, contextmanager, suppress
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO
from uuid import uuid4

from langsmith import Client, RunTree
from rich.console import Console

from .presentation import TerminalUI

DIAGNOSTIC_INTERVAL_SECONDS = 10.0

_PRIVATE_DIRECTORY_MODE = 0o700
_PRIVATE_FILE_MODE = 0o600
_SECRET_NAME_MARKERS = ("key", "token", "secret", "password", "credential")
_MINIMUM_REDACTED_SECRET_LENGTH = 8
_MODEL_METADATA_FIELDS = (
    "purpose",
    "effort",
    "attempt",
    "reserved_microusd",
)
_MODEL_OUTPUT_LABELS = ("response_status", "error_type")
_MODEL_OUTPUT_NUMBERS = ("latency_seconds", "ttft_seconds")
_RESULT_NUMBERS = (
    "steps",
    "cost_usd",
    "reported_cost_usd",
    "model_attempts",
    "latency_seconds",
)
_USAGE_NUMBERS = (
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "calls",
    "input_cost",
    "output_cost",
    "total_cost",
)
_USAGE_DETAIL_FIELDS = ("input_token_details", "output_token_details")
_USAGE_DETAIL_NUMBERS = frozenset({"cache_read", "cache_creation", "reasoning"})
_ROOT_EVENT_NUMBERS = ("step", "attempt", "delay_seconds")
_ROOT_EVENT_FLAGS = ("needs_approval", "approved", "usage_unknown")
_ROOT_EVENT_LABELS = ("status", "purpose", "error_type")
_UNTRACED_EVENTS = frozenset(
    {"run_started", "node_started", "node_finished", "tracing_error", "diagnostic"}
)


def _emit_diagnostic(
    emit: Callable[[str, dict[str, Any]], None], record: dict[str, Any]
) -> None:
    """Keep optional diagnostics from changing the operation they observe."""

    try:
        emit("diagnostic", record)
    except Exception:  # noqa: BLE001 - diagnostics are never task control flow
        return


@asynccontextmanager
async def diagnostic_span(
    emit: Callable[[str, dict[str, Any]], None], phase: str, **context: Any
) -> AsyncIterator[dict[str, Any]]:
    """Record local phase progress without adding a timeout to the operation."""

    started = time.monotonic()
    phase_id = str(uuid4())
    progress: dict[str, Any] = {}

    def record(state: str, **extra: Any) -> None:
        now = time.monotonic()
        fields = dict(extra)
        previous_event = progress.get("last_event_at_monotonic")
        if isinstance(previous_event, (int, float)):
            fields["elapsed_since_last_event_seconds"] = max(0.0, now - previous_event)
        _emit_diagnostic(
            emit,
            {
                **context,
                **progress,
                "phase": phase,
                "phase_id": phase_id,
                "state": state,
                "elapsed_seconds": now - started,
                **fields,
            },
        )

    async def heartbeat() -> None:
        previous = time.monotonic()
        while True:
            await asyncio.sleep(DIAGNOSTIC_INTERVAL_SECONDS)
            now = time.monotonic()
            record(
                "progress",
                event_loop_lag_seconds=max(
                    0.0,
                    now - previous - DIAGNOSTIC_INTERVAL_SECONDS,
                ),
            )
            previous = now

    record("started")
    watcher = asyncio.create_task(heartbeat(), name=f"diagnostic:{phase}:{phase_id}")
    outcome: dict[str, Any] = {"outcome": "completed"}
    try:
        yield progress
    except BaseException as exc:
        outcome = {
            "outcome": (
                "cancelled" if isinstance(exc, asyncio.CancelledError) else "error"
            ),
            "error_type": type(exc).__name__,
            "error_message": str(exc)[:4000],
            "error_code": getattr(exc, "code", None),
        }
        raise
    finally:
        watcher.cancel()
        with suppress(asyncio.CancelledError):
            await watcher
        record("finished", **outcome)


def _environment_secrets(environment: Mapping[str, str]) -> tuple[str, ...]:
    values = {
        value
        for name, value in environment.items()
        if len(value) >= _MINIMUM_REDACTED_SECRET_LENGTH
        and any(marker in name.casefold() for marker in _SECRET_NAME_MARKERS)
    }
    return tuple(sorted(values, key=len, reverse=True))


class _Redactor:
    """Copy arbitrary event values into JSON-compatible, secret-free data."""

    def __init__(self, secrets: tuple[str, ...]) -> None:
        self.secrets = secrets

    def _text(self, value: Any) -> str:
        text = str(value)
        for secret in self.secrets:
            text = text.replace(secret, "[REDACTED]")
        return text

    def value(
        self,
        value: Any,
        *,
        depth: int = 0,
        ancestors: frozenset[int] = frozenset(),
    ) -> Any:
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, str):
            return self._text(value)
        if depth >= 32:
            return "[TRUNCATED]"

        identity = id(value)
        if identity in ancestors:
            return "[CYCLE]"
        nested_ancestors = ancestors | {identity}
        if isinstance(value, Mapping):
            return {
                self._text(key): self.value(
                    item,
                    depth=depth + 1,
                    ancestors=nested_ancestors,
                )
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple, set, frozenset)):
            return [
                self.value(
                    item,
                    depth=depth + 1,
                    ancestors=nested_ancestors,
                )
                for item in value
            ]
        return self._text(value)


def _open_private_log(path: Path) -> TextIO:
    path.parent.mkdir(mode=_PRIVATE_DIRECTORY_MODE, parents=True, exist_ok=True)
    path.parent.chmod(_PRIVATE_DIRECTORY_MODE)
    descriptor = os.open(
        path,
        os.O_APPEND | os.O_CREAT | os.O_WRONLY | getattr(os, "O_CLOEXEC", 0),
        _PRIVATE_FILE_MODE,
    )
    try:
        os.chmod(path, _PRIVATE_FILE_MODE)
        return os.fdopen(descriptor, "a", encoding="utf-8")
    except BaseException:
        os.close(descriptor)
        raise


def _number_fields(data: Mapping[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    return {
        field: value
        for field in fields
        if isinstance((value := data.get(field)), (int, float))
        and not isinstance(value, bool)
    }


def _flag_fields(data: Mapping[str, Any], fields: tuple[str, ...]) -> dict[str, bool]:
    return {
        field: value for field in fields if isinstance((value := data.get(field)), bool)
    }


def _label_fields(data: Mapping[str, Any], fields: tuple[str, ...]) -> dict[str, str]:
    return {
        field: value[:128]
        for field in fields
        if isinstance((value := data.get(field)), str) and value
    }


def _usage_metrics(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    metrics = _number_fields(value, _USAGE_NUMBERS)
    for field in _USAGE_DETAIL_FIELDS:
        details = value.get(field)
        if isinstance(details, Mapping):
            metrics[field] = {
                str(key): number
                for key, number in details.items()
                if key in _USAGE_DETAIL_NUMBERS
                and isinstance(number, (int, float))
                and not isinstance(number, bool)
            }
    return metrics


class Events:
    """Persist private records, render selected fields, and export safe metrics."""

    def __init__(
        self,
        path: Path,
        console: Console | TerminalUI | None = None,
        *,
        debug: bool = False,
        ui: TerminalUI | None = None,
    ) -> None:
        self.file = _open_private_log(path)
        self.debug = debug
        self.root: RunTree | None = None
        self.client: Client | None = None
        self.models: dict[str, RunTree | None] = {}
        self.current_span: ContextVar[RunTree | None] = ContextVar(
            f"telemetry_span_{id(self)}",
            default=None,
        )
        self.tracing_enabled = os.getenv(
            "LANGSMITH_TRACING", "false"
        ).strip().casefold() in {
            "1",
            "true",
            "yes",
            "on",
        }
        self.secrets = list(_environment_secrets(os.environ))
        self._redactor = _Redactor(tuple(self.secrets))
        self._closed = False

        if ui is not None:
            self.ui = ui
            self.console = getattr(ui, "console", console)
        elif isinstance(console, TerminalUI):
            self.ui = console
            self.console = console.console
        elif console is not None:
            self.ui = TerminalUI(console, debug=debug)
            self.console = console
        else:
            self.ui = None
            self.console = None

    def _write(self, event: str, data: Mapping[str, Any]) -> dict[str, Any]:
        raw = {
            **data,
            "time": datetime.now(UTC).isoformat(),
            "event": event,
        }
        record = self._redactor.value(raw)
        self.file.write(json.dumps(record, ensure_ascii=False) + "\n")
        self.file.flush()
        return record

    def __call__(self, event: str, data: dict[str, Any]) -> None:
        if self._closed:
            raise RuntimeError("Events sink is closed.")
        record = self._write(event, data)
        if event == "diagnostic":
            return
        if event != "tracing_error":
            try:
                self._trace_event(event, record)
            except Exception as exc:  # noqa: BLE001 - exporting cannot stop browser work
                self._trace_error(exc)
        if self.ui is not None:
            self.ui.event(event, record)

    def _trace_error(self, exc: Exception) -> None:
        """Record only the local exception class, without provider error text."""

        try:
            self("tracing_error", {"error_type": type(exc).__name__})
        except Exception:  # noqa: BLE001 - even local logging may be unavailable
            return

    def _start_span(
        self,
        name: str,
        run_type: str = "chain",
        *,
        run_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> RunTree | None:
        parent = self.current_span.get() or self.root
        if parent is None:
            return None
        span = parent.create_child(
            name,
            run_type=run_type,
            run_id=run_id,
            inputs={},
            extra={"metadata": metadata or {}},
        )
        span.post()
        return span

    @contextmanager
    def span(self, name: str, *, step: int = 0) -> Iterator[None]:
        """Trace one graph node without serializing browser state or arguments."""

        started = time.monotonic()
        span: RunTree | None = None
        try:
            span = self._start_span(
                name,
                "tool" if name == "execute" else "chain",
                metadata={"step": step},
            )
        except Exception as exc:  # noqa: BLE001 - exporting cannot stop browser work
            self._trace_error(exc)

        token = self.current_span.set(span)
        self("node_started", {"node": name, "step": step})
        error_type: str | None = None
        try:
            yield
        except BaseException as exc:
            error_type = type(exc).__name__
            raise
        finally:
            self.current_span.reset(token)
            result = {
                "node": name,
                "step": step,
                "latency_seconds": time.monotonic() - started,
                "error_type": error_type,
            }
            self("node_finished", result)
            if span is not None:
                try:
                    span.end(outputs=result, error=error_type)
                    span.patch()
                except Exception as exc:  # noqa: BLE001 - exporting cannot stop browser work
                    self._trace_error(exc)

    def _start_trace(self, data: Mapping[str, Any]) -> None:
        if not self.tracing_enabled:
            return
        run_id = str(data["run_id"])
        self.client = Client()
        self.root = RunTree(
            id=run_id,
            name="browser-agent-task",
            run_type="chain",
            project_name=os.getenv(
                "LANGSMITH_PROJECT",
                "sp-solution-test-assignment",
            ),
            client=self.client,
            inputs={},
            extra={
                "metadata": {
                    "run_id": run_id,
                    "session_id": run_id,
                    "model": data.get("model", ""),
                    "budget_usd": data.get("budget_usd", 0),
                    "content_policy": "metrics_only",
                }
            },
            tags=["browser-agent", "metrics-only"],
        )
        self.root.post()

    def _start_model_trace(self, data: Mapping[str, Any]) -> None:
        call_id = str(data["call_id"])
        purpose = str(data.get("purpose", "model"))
        metadata = {
            "ls_provider": "openai",
            "ls_model_name": str(data.get("model", "")),
            "ls_model_type": "chat",
            **{key: data[key] for key in _MODEL_METADATA_FIELDS if key in data},
            "reasoning_effort": data.get("effort", ""),
            "service_tier": "default",
        }
        self.models[call_id] = self._start_span(
            purpose,
            "llm",
            run_id=call_id,
            metadata=metadata,
        )

    def _record_first_token(self, data: Mapping[str, Any]) -> None:
        span = self.models.get(str(data.get("call_id", "")))
        timestamp = data.get("first_token_time")
        if span is not None and isinstance(timestamp, str):
            span.add_event({"name": "new_token", "time": timestamp})

    def _finish_model_trace(self, data: Mapping[str, Any]) -> None:
        span = self.models.pop(str(data.get("call_id", "")), None)
        if span is None:
            return
        output = {
            **_label_fields(data, _MODEL_OUTPUT_LABELS),
            **_number_fields(data, _MODEL_OUTPUT_NUMBERS),
            **_flag_fields(data, ("usage_unknown",)),
        }
        usage = _usage_metrics(data.get("usage_metadata"))
        if usage:
            output["usage_metadata"] = usage
        error = data.get("error_type")
        if not error and data.get("response_status") == "failed":
            error = "ProviderResponseFailed"
        span.end(outputs=output, error=error)
        span.patch()

    def _finish_trace(self, data: Mapping[str, Any]) -> None:
        if self.root is None:
            return
        output = {
            **_number_fields(data, _RESULT_NUMBERS),
            **_label_fields(data, ("run_id", "status")),
        }
        usage = _usage_metrics(data.get("usage"))
        if usage:
            output["usage"] = usage
        status = data.get("status")
        self.root.end(
            outputs=output,
            error="TaskFailed" if status == "failed" else None,
        )
        self.root.patch()

    def _add_trace_event(self, event: str, data: Mapping[str, Any]) -> None:
        if self.root is None:
            return
        fields = {
            **_number_fields(data, _ROOT_EVENT_NUMBERS),
            **_flag_fields(data, _ROOT_EVENT_FLAGS),
            **_label_fields(data, _ROOT_EVENT_LABELS),
        }
        self.root.add_event(
            {
                "name": event,
                "time": data["time"],
                "kwargs": fields,
            }
        )

    def _trace_event(self, event: str, data: dict[str, Any]) -> None:
        """Project private records onto explicit numeric and host-status fields."""

        if event == "run_started":
            self._start_trace(data)
            return
        if self.root is None:
            return
        if event == "model_admitted":
            self._start_model_trace(data)
        elif event == "model_first_token":
            self._record_first_token(data)
        elif event in {"model_usage", "model_error"}:
            self._finish_model_trace(data)
        elif event == "result":
            self._finish_trace(data)
        elif event not in _UNTRACED_EVENTS:
            self._add_trace_event(event, data)

    def _close_export(self) -> None:
        for span in tuple(self.models.values()):
            if span is None:
                continue
            try:
                span.end(error="RunClosedBeforeUsage")
                span.patch()
            except Exception as exc:  # noqa: BLE001 - exporting cannot stop cleanup
                self._trace_error(exc)
        self.models.clear()

        root = self.root
        if root is not None and root.end_time is None:
            try:
                root.end(error="RunClosedBeforeResult")
                root.patch()
            except Exception as exc:  # noqa: BLE001 - exporting cannot stop cleanup
                self._trace_error(exc)
        if self.client is not None:
            try:
                self.client.flush(timeout=5)
            except Exception as exc:  # noqa: BLE001 - exporting cannot stop cleanup
                self._trace_error(exc)

    def close(self) -> None:
        """Finish open trace spans and close the private log exactly once."""

        if self._closed:
            return
        try:
            if self.ui is not None:
                self.ui.close()
            self._close_export()
        finally:
            self._closed = True
            self.file.close()
