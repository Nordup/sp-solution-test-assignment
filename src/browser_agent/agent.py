"""Own one task's resources, execution, and private result record."""

from __future__ import annotations

import asyncio
import json
import math
import time
from collections.abc import Callable
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any
from uuid import uuid4

from langgraph.errors import NodeCancelledError
from langsmith import tracing_context
from rich.console import Console

from .browser import PlaywrightCLI
from .config import Settings
from .graph import AgentGraph, HumanResponder
from .model import ModelClient
from .presentation import TerminalUI
from .telemetry import Events, diagnostic_span


async def run_task(
    settings: Settings,
    task: str,
    browser: PlaywrightCLI,
    *,
    responder: HumanResponder | None = None,
    console: Console | None = None,
    model_factory: Callable[..., ModelClient] = ModelClient,
    raise_on_cancel: bool = False,
    debug: bool = False,
    ui: TerminalUI | None = None,
) -> dict[str, Any]:
    """Run a task in the caller's browser and release only task-owned resources."""
    if not isinstance(task, str) or not task.strip():
        raise ValueError("A task is required.")
    settings.prepare()
    run_id = str(uuid4())
    started = time.monotonic()
    directory = settings.artifact_dir / "runs" / run_id
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    profile = browser.profile.name
    model: ModelClient | None = None
    engine: AgentGraph | None = None
    cancelled = False
    outcome = {
        "status": "failed",
        "summary": "Task did not start.",
        "remaining": ["Run the task."],
    }

    async with AsyncExitStack() as cleanup:
        events = Events(directory / "events.jsonl", console, debug=debug, ui=ui)
        cleanup.callback(events.close)
        cleanup.callback(setattr, browser, "emit", browser.emit)
        browser.emit = events
        browser.artifact_dir = directory / "evidence"
        events(
            "run_started",
            {
                "run_id": run_id,
                "task": task,
                "url": browser.current_url,
                "model": settings.model,
                "profile": profile,
                "budget_usd": settings.budget_usd,
            },
        )
        events(
            "diagnostic",
            {
                "phase": "run_configuration",
                "model": settings.model,
                "reasoning": settings.reasoning,
                "max_input_tokens": settings.max_input_tokens,
                "max_output_tokens": settings.max_output_tokens,
                "active_seconds": settings.active_seconds,
                "max_retries": settings.max_retries,
            },
        )
        try:
            async with diagnostic_span(events, "browser_prepare"):
                await browser.prepare_task()
            model = model_factory(settings, emit=events)
            cleanup.push_async_callback(model.close)
            engine = AgentGraph(browser, model, settings, task, events, responder)
            # Only explicit metrics may leave the process; graph state is private.
            with tracing_context(enabled=False):
                state = await engine.compile().ainvoke(
                    {"steps": 0, "failures": 0, "history": []},
                    # Bound work by active time and cost, never by decision count.
                    {"recursion_limit": math.inf},
                )
            outcome = state["result"]
        except (asyncio.CancelledError, NodeCancelledError):
            cancelled = True
            outcome = {
                "status": "partial",
                "summary": "I stopped this task at cancellation. A pending browser action may already have taken effect; inspect the current page before trying again.",
                "remaining": ["Check the current browser page before retrying."],
            }
            events("run_cancelled", {"pending_action_may_have_taken_effect": True})
        except Exception as error:  # noqa: BLE001 - retain a safe result without secret-bearing exception text
            outcome = {
                "status": "failed",
                "summary": "I could not verify the current browser state after an unexpected failure. A pending action may need checking; inspect the browser before retrying.",
                "remaining": ["Check the current browser page, then retry if needed."],
            }
            events("run_error", {"error_type": type(error).__name__})
        finally:
            report = {
                **outcome,
                "run_id": run_id,
                "profile": profile,
                "steps": engine.steps if engine else 0,
                "latency_seconds": time.monotonic() - started,
                **_model_metrics(model),
            }
            _write_result(directory / "result.json", report)
            events("result", report)

    if cancelled and raise_on_cancel:
        raise asyncio.CancelledError
    return report


def _model_metrics(model: ModelClient | None) -> dict[str, Any]:
    return {
        "cost_usd": model.cost_usd if model else 0.0,
        "reported_cost_usd": model.reported_cost_usd if model else 0.0,
        "usage": dict(model.usage) if model else {},
        "model_attempts": model.attempts if model else 0,
    }


def _write_result(path: Path, result: dict[str, Any]) -> None:
    # Open exclusively with private permissions before writing any account data.
    with path.open("x", encoding="utf-8") as stream:
        path.chmod(0o600)
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
