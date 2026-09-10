"""Run one task with a small LangGraph and private JSON artifacts."""

import asyncio
import json
from uuid import uuid4

from langgraph.errors import NodeCancelledError
from langsmith import tracing_context

from .browser import BrowserSession
from .graph import AgentGraph
from .llm import Gateway
from .telemetry import Events


def safe_name(value):
    if (
        not value
        or len(value) > 100
        or any(
            c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
            for c in value
        )
    ):
        raise ValueError("Use a simple alphanumeric name with hyphens/underscores.")
    return value


async def run_agent(
    settings,
    task,
    url=None,
    profile="default",
    headless=False,
    responder=None,
    console=None,
    synthetic=False,
    browser_factory=BrowserSession,
    gateway_factory=Gateway,
    new_run_id=None,
):
    if not isinstance(task, str) or not task.strip():
        raise ValueError("A task is required.")
    settings.prepare()
    profile = safe_name(profile)
    run_id = safe_name(new_run_id or str(uuid4()))
    directory = settings.artifact_dir / "runs" / run_id
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    events = Events(directory / "events.jsonl", console)
    browser = browser_factory(
        settings.artifact_dir / "profiles" / profile,
        headless=headless,
        artifact_dir=directory / "evidence",
    )
    gateway = None
    engine = None
    steps = 0
    result = {
        "status": "failed",
        "summary": "Task did not start.",
        "remaining": ["Run the task."],
    }
    metadata = {
        "run_id": run_id,
        "task": task,
        "url": url,
        "model": settings.model,
        "profile": profile,
        "synthetic": synthetic,
        "budget_usd": settings.budget_usd,
    }
    events("run_started", metadata)
    try:
        await browser.start(url)
        gateway = gateway_factory(settings, emit=events)
        engine = AgentGraph(browser, gateway, settings, task, events, responder)
        # Live account content must never follow LANGSMITH_TRACING=true into cloud traces.
        # Synthetic evaluation exports are explicit and owned by the evaluator.
        with tracing_context(enabled=False):
            state = await engine.compile().ainvoke(
                {
                    "steps": 0,
                    "failures": 0,
                    "history": [],
                    "notebook": "",
                    "read_args": {},
                    "pending_result": None,
                },
                {"recursion_limit": settings.max_decisions * 5 + 30},
            )
        result = state["result"]
        steps = state.get("steps", 0)
    except (asyncio.CancelledError, NodeCancelledError):
        result = {
            "status": "partial",
            "summary": "Task stopped by cancellation. A pending action may already have taken effect; it was not replayed.",
            "remaining": [
                "Inspect the browser state and private event log before starting another task."
            ],
        }
        events("run_cancelled", {"pending_action_may_have_taken_effect": True})
    except Exception as exc:  # noqa: BLE001 - record a safe failure without secret-bearing request bodies
        result = {
            "status": "failed",
            "summary": f"Task stopped: {type(exc).__name__}.",
            "remaining": [
                "Inspect the private event log and browser state before retrying."
            ],
        }
        events("run_error", {"error_type": type(exc).__name__})
    finally:
        steps = getattr(engine, "steps", steps)
        result = result | {
            "run_id": run_id,
            "steps": steps,
            "cost_usd": float(getattr(gateway, "cost_usd", 0)),
            "profile": profile,
            "synthetic": synthetic,
        }
        path = directory / "result.json"
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        path.chmod(0o600)
        events("result", result)
        try:
            await browser.close()
        finally:
            events.close()
            if gateway is not None and hasattr(gateway, "close"):
                await gateway.close()
    return result
