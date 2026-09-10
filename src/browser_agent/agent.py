"""Run one task with a small LangGraph and private JSON artifacts."""

import asyncio
import json
from uuid import uuid4

from langgraph.errors import NodeCancelledError
from langsmith import tracing_context

from .graph import AgentGraph
from .llm import Gateway
from .telemetry import Events


async def run_task(
    settings,
    task,
    browser,
    *,
    responder=None,
    console=None,
    gateway_factory=Gateway,
    raise_on_cancel=False,
):
    """Run one model task in the caller's browser; leave the browser open."""
    if not isinstance(task, str) or not task.strip():
        raise ValueError("A task is required.")
    settings.prepare()
    profile = browser.profile.name
    run_id = str(uuid4())
    directory = settings.artifact_dir / "runs" / run_id
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    events = Events(directory / "events.jsonl", console)
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
        "url": browser.page.url if browser.page else None,
        "model": settings.model,
        "profile": profile,
        "budget_usd": settings.budget_usd,
    }
    events("run_started", metadata)
    try:
        await browser.prepare_task()
        gateway = gateway_factory(settings, emit=events)
        engine = AgentGraph(browser, gateway, settings, task, events, responder)
        # Live account content must never follow LANGSMITH_TRACING=true into cloud traces.
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
        cancelled = True
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
        }
        path = directory / "result.json"
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        path.chmod(0o600)
        events("result", result)
        events.close()
        if gateway is not None and hasattr(gateway, "close"):
            await gateway.close()
    if cancelled and raise_on_cancel:
        raise asyncio.CancelledError
    return result
