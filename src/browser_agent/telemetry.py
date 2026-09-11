"""Private local event sink for task runs."""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager, suppress
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from langsmith import Client, RunTree
from rich.console import Console

from .presentation import TerminalUI

DIAGNOSTIC_INTERVAL_SECONDS = 10.0


@asynccontextmanager
async def diagnostic_span(
    emit: Callable[[str, dict[str, Any]], None], phase: str, **context: Any
) -> AsyncIterator[dict[str, Any]]:
    """Record phase progress locally without imposing a deadline on the work."""
    started = time.monotonic()
    phase_id = str(uuid4())
    progress = {}

    def record(state: str, **extra: Any) -> None:
        now = time.monotonic()
        last_event = progress.get("last_event_at_monotonic")
        if isinstance(last_event, (int, float)):
            extra["elapsed_since_last_event_seconds"] = max(0.0, now - last_event)
        emit(
            "diagnostic",
            {
                **context,
                **progress,
                "phase": phase,
                "phase_id": phase_id,
                "state": state,
                "elapsed_seconds": now - started,
                **extra,
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
                    0.0, now - previous - DIAGNOSTIC_INTERVAL_SECONDS
                ),
            )
            previous = now

    record("started")
    watcher = asyncio.create_task(heartbeat(), name=f"diagnostic:{phase}:{phase_id}")
    outcome = {"outcome": "completed"}
    try:
        yield progress
    except BaseException as exc:
        outcome = {
            "outcome": "cancelled"
            if isinstance(exc, asyncio.CancelledError)
            else "error",
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


class Events:
    """Write private records, render redacted events, and export explicit metrics."""

    def __init__(
        self,
        path: Path,
        console: Console | TerminalUI | None = None,
        *,
        debug: bool = False,
        ui: TerminalUI | None = None,
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.file = path.open("a", encoding="utf-8")
        path.chmod(0o600)
        # A session can share its presenter. UI events use the redacted record.
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
        self.debug = debug
        self.root = None
        self.client = None
        self.models = {}
        self.current_span = ContextVar("telemetry_span", default=None)
        self.tracing_enabled = os.getenv("LANGSMITH_TRACING", "false").lower() == "true"
        self.secrets = [
            v
            for k, v in os.environ.items()
            if ("KEY" in k or "TOKEN" in k or "SECRET" in k) and len(v) > 12
        ]

    def __call__(self, event: str, data: dict[str, Any]) -> None:
        payload = json.dumps(
            {"time": datetime.now(UTC).isoformat(), "event": event, **data},
            ensure_ascii=False,
            default=str,
        )
        for secret in self.secrets:
            payload = payload.replace(secret, "[REDACTED]")
        self.file.write(payload + "\n")
        self.file.flush()
        # These records may contain partial model output and low-level errors.
        # Keep them strictly local, including when terminal --debug is enabled.
        if event == "diagnostic":
            return
        record = json.loads(payload)
        if event != "tracing_error":
            try:
                self._trace_event(event, record)
            except Exception as exc:  # noqa: BLE001 - export failures must not stop browser work
                self._trace_error(exc)
        if self.ui is not None:
            self.ui.event(event, record)

    def _trace_error(self, exc: Exception) -> None:
        self("tracing_error", {"error_type": type(exc).__name__})

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
            inputs={"content": "Kept in private local artifacts"},
            extra={"metadata": metadata or {}},
        )
        span.post()
        return span

    @contextmanager
    def span(self, name: str, *, step: int = 0) -> Iterator[None]:
        """Trace a graph node without serializing its browser state or arguments."""
        span = None
        started = time.monotonic()
        try:
            span = self._start_span(
                name, "tool" if name == "execute" else "chain", metadata={"step": step}
            )
        except Exception as exc:  # noqa: BLE001 - export failures must not stop browser work
            self._trace_error(exc)
        token = self.current_span.set(span)
        self("node_started", {"node": name, "step": step})
        error = None
        try:
            yield
        except BaseException as exc:
            error = type(exc).__name__
            raise
        finally:
            self.current_span.reset(token)
            data = {
                "node": name,
                "step": step,
                "latency_seconds": time.monotonic() - started,
                "error_type": error,
            }
            self("node_finished", data)
            if span is not None:
                try:
                    span.end(outputs=data, error=error)
                    span.patch()
                except Exception as exc:  # noqa: BLE001 - export failures must not stop browser work
                    self._trace_error(exc)

    def _trace_event(self, event: str, data: dict[str, Any]) -> None:
        # Only numeric metrics and host-generated status fields cross this boundary.
        # Never forward task text, page contents, tool arguments, answers or errors.
        if event == "run_started" and self.tracing_enabled:
            self.client = Client()
            self.root = RunTree(
                id=data["run_id"],
                name="browser-agent-task",
                run_type="chain",
                project_name=os.getenv(
                    "LANGSMITH_PROJECT", "sp-solution-test-assignment"
                ),
                client=self.client,
                inputs={"content": "Kept in private local artifacts"},
                extra={
                    "metadata": {
                        "run_id": data["run_id"],
                        "session_id": data["run_id"],
                        "model": data["model"],
                        "budget_usd": data["budget_usd"],
                        "content_policy": "metrics_only",
                    }
                },
                tags=["browser-agent", "metrics-only"],
            )
            self.root.post()
        if self.root is None:
            return
        if event == "model_admitted":
            span = self._start_span(
                data["purpose"],
                "llm",
                run_id=data["call_id"],
                metadata={
                    "ls_provider": "openai",
                    "ls_model_name": data["model"],
                    "ls_model_type": "chat",
                    "purpose": data["purpose"],
                    "reasoning_effort": data["effort"],
                    "attempt": data["attempt"],
                    "reserved_microusd": data["reserved_microusd"],
                    "service_tier": "default",
                },
            )
            self.models[data["call_id"]] = span
        elif event == "model_first_token":
            span = self.models.get(data["call_id"])
            if span:
                span.add_event({"name": "new_token", "time": data["first_token_time"]})
        elif event in {"model_usage", "model_error"}:
            span = self.models.pop(data["call_id"], None)
            if span:
                safe = {
                    key: data[key]
                    for key in (
                        "usage_metadata",
                        "response_id",
                        "response_status",
                        "latency_seconds",
                        "ttft_seconds",
                        "usage_unknown",
                        "error_type",
                    )
                    if key in data
                }
                error = data.get("error_type") or (
                    "ProviderResponseFailed"
                    if data.get("response_status") == "failed"
                    else None
                )
                span.end(outputs=safe, error=error)
                span.patch()
        elif event == "result":
            safe = {
                key: data[key]
                for key in (
                    "run_id",
                    "steps",
                    "status",
                    "cost_usd",
                    "reported_cost_usd",
                    "usage",
                    "model_attempts",
                    "latency_seconds",
                )
                if key in data
            }
            self.root.end(
                outputs=safe, error="TaskFailed" if data["status"] == "failed" else None
            )
            self.root.patch()
        elif event not in {"run_started", "node_started", "node_finished"}:
            safe = {
                key: data[key]
                for key in (
                    "step",
                    "tool",
                    "decision",
                    "needs_approval",
                    "approved",
                    "purpose",
                    "attempt",
                    "delay_seconds",
                    "error_type",
                )
                if key in data
            }
            self.root.add_event({"name": event, "time": data["time"], "kwargs": safe})

    def close(self) -> None:
        if self.ui is not None:
            self.ui.close()
        try:
            for span in self.models.values():
                span.end(error="RunClosedBeforeUsage")
                span.patch()
            if self.root is not None and self.root.end_time is None:
                self.root.end(error="RunClosedBeforeResult")
                self.root.patch()
            if self.client is not None:
                self.client.flush(timeout=5)
        except Exception as exc:  # noqa: BLE001 - export failures must not stop browser work
            self._trace_error(exc)
        finally:
            self.file.close()
