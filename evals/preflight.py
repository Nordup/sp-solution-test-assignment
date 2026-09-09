"""One tiny budgeted native tool call and verified synthetic LangSmith trace."""

import asyncio
import os
import uuid
from datetime import UTC, datetime
from typing import Literal

from langsmith import Client
from langsmith.run_trees import RunTree

from browser_agent.llm import Gateway
from browser_agent.runner import safe_name
from browser_agent.tools import Strict, parse_call, tool_specs
from evals.release import release_store
from evals.report import (
    preflight_fingerprint,
    require_deterministic,
    runtime_fingerprint,
    write_json,
)


class PreflightResponse(Strict):
    ok: Literal[True]


async def preflight(settings, release_session=None):
    require_deterministic(settings)
    if not release_session:
        raise ValueError("Online preflight requires an initialized --release-session")
    store = release_store(settings)
    run_id = str(uuid.uuid4())
    scope = "release:" + safe_name(release_session)
    store.reserve_case(run_id, round(settings.budget_usd * 1_000_000), [scope])
    report = {
        "run_id": run_id,
        "stage": "provider_langsmith_preflight",
        "release_session": release_session,
        "runtime_fingerprint": runtime_fingerprint(),
        "protocol_fingerprint": preflight_fingerprint(),
        "model": settings.model,
        "started_at": datetime.now(UTC).isoformat(),
        "passed": False,
        "synthetic": True,
    }
    events = []
    trace = None
    try:
        client = Client()
        project = client.read_project(
            project_name=os.getenv("LANGSMITH_PROJECT", "sp-solution-test-assignment")
        )
        trace = RunTree(
            id=uuid.UUID(run_id),
            name="provider-langsmith-preflight",
            project_name=project.name,
            project_id=project.id,
            ls_client=client,
            inputs={"purpose": "Synthetic structured-call connectivity check"},
        )
        trace.post()
        gateway = Gateway(
            settings.model_copy(update={"max_output_tokens": 128}),
            store,
            run_id,
            emit=lambda event, data: events.append({"event": event, **data}),
        )
        child = trace.create_child(
            name="OpenAI native structured preflight",
            run_type="llm",
            inputs={
                "model": settings.model,
                "prompt": "Call preflight_ok with ok true.",
            },
        )
        child.post()
        registry = {
            "preflight_ok": (
                PreflightResponse,
                "Confirm the synthetic preflight is working.",
            )
        }
        response = await gateway.call(
            {
                "input": "Call preflight_ok with ok true.",
                "tools": tool_specs(registry),
                "tool_choice": "required",
                "parallel_tool_calls": False,
                "truncation": "disabled",
            },
            purpose="preflight",
        )
        result = parse_call(response, registry)
        child.end(
            outputs={
                "validated_arguments": result["arguments"],
                "accounted_events": events,
            }
        )
        child.patch()
        trace.end(
            outputs={
                "structured_call_validated": True,
                "input_count_and_usage_reconciled": bool(events),
                "agent_evaluated": False,
            }
        )
        trace.patch()
        for attempt in range(4):
            try:
                await client.runs.retrieve(run_id, project_id=str(project.id))
                nested = await client.runs.retrieve(
                    str(child.id),
                    project_id=str(project.id),
                    selects=["ID", "PARENT_RUN_IDS", "TRACE_ID", "PROJECT_ID"],
                )
                if run_id not in [
                    str(value) for value in (nested.parent_run_ids or [])
                ]:
                    raise ValueError("Provider trace is not nested under preflight")
                break
            except Exception:
                if attempt == 3:
                    raise
                await asyncio.sleep(0.5 * (attempt + 1))
        report.update(
            {
                "passed": True,
                "langsmith_url": trace.get_url(),
                "events": events,
                "checks": {
                    "strict_call": result["arguments"]["ok"],
                    "count_and_usage": bool(gateway.usage["calls"]),
                    "nested_langsmith_trace": True,
                },
            }
        )
    except Exception as exc:  # noqa: BLE001 - preserve evidence for every service failure without secrets
        report["error"] = type(exc).__name__
        if trace:
            try:
                trace.end(error=type(exc).__name__)
                trace.patch()
            except Exception:  # noqa: BLE001 - remote telemetry failure must preserve the local report
                report["trace_finalization_failed"] = True
    finally:
        store.finish_case(run_id)
        report["budget"] = store.budget(run_id)
        report["ended_at"] = datetime.now(UTC).isoformat()
        path = settings.artifact_dir / "final" / "sessions" / release_session
        write_json(path / f"preflight-{run_id}.json", report)
        write_json(path / "preflight.json", report)
    return report
