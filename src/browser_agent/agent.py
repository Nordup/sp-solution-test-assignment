"""Run one task with a small LangGraph and private JSON artifacts."""

import asyncio
import json
import time
from uuid import uuid4

from langgraph.errors import NodeCancelledError
from langsmith import tracing_context

from .graph import AgentGraph
from .llm import Gateway
from .telemetry import Events, diagnostic_span


async def run_task(
    settings,
    task,
    browser,
    *,
    responder=None,
    console=None,
    gateway_factory=Gateway,
    raise_on_cancel=False,
    debug=False,
    ui=None,
):
    """Run one model task in the caller's browser; leave the browser open."""
    if not isinstance(task, str) or not task.strip():
        raise ValueError("A task is required.")
    settings.prepare()
    profile = getattr(getattr(browser, "profile", None), "name", "workspace")
    run_id = str(uuid4())
    started = time.monotonic()
    directory = settings.artifact_dir / "runs" / run_id
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    events = Events(directory / "events.jsonl", console, debug=debug, ui=ui)
    previous_browser_emit = getattr(browser, "emit", None)
    if hasattr(browser, "emit"):
        browser.emit = events
    if hasattr(browser, "artifact_dir"):
        browser.artifact_dir = directory / "evidence"
    gateway = None
    engine = None
    steps = 0
    cancelled = False
    result = {
        "status": "failed",
        "summary": "Task did not start.",
        "remaining": ["Run the task."],
    }
    metadata = {
        "run_id": run_id,
        "task": task,
        "url": getattr(browser, "current_url", None)
        or (browser.page.url if getattr(browser, "page", None) else None),
        "model": settings.model,
        "profile": profile,
        "budget_usd": settings.budget_usd,
    }
    events("run_started", metadata)
    events(
        "diagnostic",
        {
            "phase": "run_configuration",
            "model": settings.model,
            "reasoning": settings.reasoning,
            "max_input_tokens": settings.max_input_tokens,
            "max_output_tokens": settings.max_output_tokens,
            "max_decisions": settings.max_decisions,
            "active_seconds": settings.active_seconds,
            "max_retries": settings.max_retries,
        },
    )
    try:
        async with diagnostic_span(events, "browser_prepare"):
            await browser.prepare_task()
        gateway = gateway_factory(settings, emit=events)
        engine = AgentGraph(browser, gateway, settings, task, events, responder)
        # Events exports explicit metrics-only spans; automatic graph tracing would
        # serialize private account content and must remain disabled.
        with tracing_context(enabled=False):
            state = await engine.compile().ainvoke(
                {
                    "steps": 0,
                    "failures": 0,
                    "history": [],
                    "pending_result": None,
                },
                {"recursion_limit": settings.max_decisions * 5 + 40},
            )
        result = state["result"]
        steps = state.get("steps", 0)
    except (asyncio.CancelledError, NodeCancelledError):
        cancelled = True
        result = {
            "status": "partial",
            "summary": (
                "I stopped this task at cancellation. A pending browser action may "
                "already have taken effect; inspect the current page before trying again."
            ),
            "remaining": [
                "Check the current browser page before retrying."
            ],
        }
        events("run_cancelled", {"pending_action_may_have_taken_effect": True})
    except Exception as exc:  # noqa: BLE001 - record a safe failure without secret-bearing request bodies
        result = {
            "status": "failed",
            "summary": (
                "I could not verify the current browser state after an unexpected "
                "failure. A pending action may need checking; inspect the browser "
                "before retrying."
            ),
            "remaining": [
                "Check the current browser page, then retry if needed."
            ],
        }
        events("run_error", {"error_type": type(exc).__name__})
    finally:
        steps = getattr(engine, "steps", steps)
        result = result | {
            "run_id": run_id,
            "steps": steps,
            "cost_usd": float(getattr(gateway, "cost_usd", 0)),
            "reported_cost_usd": float(getattr(gateway, "reported_cost_usd", 0)),
            "usage": dict(getattr(gateway, "usage", {})),
            "model_attempts": int(getattr(gateway, "attempts", 0)),
            "latency_seconds": time.monotonic() - started,
            "profile": profile,
        }
        path = directory / "result.json"
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        path.chmod(0o600)
        events("result", result)
        if hasattr(browser, "emit"):
            browser.emit = previous_browser_emit
        events.close()
        if gateway is not None and hasattr(gateway, "close"):
            await gateway.close()
    if cancelled and raise_on_cancel:
        raise asyncio.CancelledError
    return result
