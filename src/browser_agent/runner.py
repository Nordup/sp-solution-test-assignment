"""Own resource lifetimes and checkpoint resume; no hidden approval responder."""

import asyncio
import json
import uuid

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command
from langsmith import tracing_context

from .browser import BrowserSession
from .graph import AgentGraph
from .llm import Gateway
from .storage import Store
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
    task=None,
    url=None,
    profile="default",
    run_id=None,
    headless=False,
    responder=None,
    console=None,
    release_session=None,
    gateway_factory=Gateway,
    browser_factory=BrowserSession,
    synthetic=False,
    new_run_id=None,
):
    settings.prepare()
    resuming = run_id is not None
    run_id = safe_name(run_id or new_run_id or str(uuid.uuid4()))
    run_dir = settings.artifact_dir / "runs" / run_id
    run_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    config_path = run_dir / "run.json"
    store = Store(settings.artifact_dir / "state" / "operations.sqlite")
    if resuming:
        saved = json.loads(config_path.read_text())
        task, profile = saved["task"], saved["profile"]
        if saved["model"] != settings.model:
            raise ValueError("Resume must use the saved model.")
        settings = settings.model_copy(update={"budget_usd": saved["budget_usd"]})
        release_session = saved.get("release_session")
        synthetic = saved.get("synthetic", False)
    else:
        if not task:
            raise ValueError("A task is required.")
        safe_name(profile)
        config_path.write_text(
            json.dumps(
                {
                    "task": task,
                    "profile": profile,
                    "model": settings.model,
                    "budget_usd": settings.budget_usd,
                    "release_session": release_session,
                    "synthetic": synthetic,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        config_path.chmod(0o600)
    store.create_budget(run_id, round(settings.budget_usd * 1_000_000))
    aggregates = ()
    if release_session:
        scope = "release:" + safe_name(release_session)
        store.budget(scope)  # Must exist; never silently create/reset an aggregate.
        aggregates = (scope,)
    events = Events(run_dir / "events.jsonl", console)
    browser = browser_factory(
        settings.artifact_dir / "profiles" / safe_name(profile),
        headless=headless,
        artifact_dir=run_dir / "evidence",
    )
    events(
        "run_started",
        {
            "run_id": run_id,
            "task": task,
            "model": settings.model,
            "profile": profile,
            "budget": store.budget(run_id),
            "resuming": resuming,
        },
    )
    try:
        await browser.start(None if resuming else url)
        gateway = gateway_factory(
            settings, store, run_id, aggregates=aggregates, emit=events
        )
        async with AsyncSqliteSaver.from_conn_string(
            str(settings.artifact_dir / "state" / "checkpoints.sqlite")
        ) as saver:
            engine = AgentGraph(browser, gateway, store, settings, run_dir, events)
            graph = engine.compile(saver)
            config = {
                "configurable": {"thread_id": run_id},
                "recursion_limit": settings.max_decisions * 12 + 100,
            }
            # Env LANGSMITH_TRACING=true must NEVER leak live browser/graph state.
            # Synthetic eval runner owns explicit remote traces independently.
            with tracing_context(enabled=False):
                value = {
                    "run_id": run_id,
                    "task": task,
                    "status": "running",
                    "steps": 0,
                    "history": [],
                    "notes": "",
                    "evidence_ids": [],
                }
                if resuming:
                    snapshot = await graph.aget_state(config)
                    if not snapshot.next:
                        return snapshot.values.get(
                            "result", {"status": "failed", "run_id": run_id}
                        )
                    if snapshot.interrupts:
                        question = snapshot.interrupts[0].value
                        # Browser restart invalidates old approval; never ask for obsolete effect.
                        if question.get("kind") == "approval":
                            await graph.aupdate_state(
                                config,
                                {
                                    "feedback": "Browser restarted; previous approval invalidated. Reobserve.",
                                    "route": "observe",
                                    "approval_id": None,
                                    "human": {},
                                },
                                as_node="recover",
                            )
                            value = None
                        elif responder:
                            answer = await responder(question)
                            if answer is None:
                                return {
                                    "status": "needs_user",
                                    "run_id": run_id,
                                    "question": question,
                                }
                            value = Command(resume=answer)
                        else:
                            return {
                                "status": "needs_user",
                                "run_id": run_id,
                                "question": question,
                            }
                    else:
                        # Any replayed executor still checks authoritative journal first.
                        value = None
                while True:
                    state = await graph.ainvoke(value, config, durability="sync")
                    pauses = state.get("__interrupt__", ())
                    if not pauses:
                        return state["result"]
                    question = pauses[0].value
                    events("waiting", {"run_id": run_id, "question": question})
                    (run_dir / "waiting.json").write_text(
                        json.dumps(question, ensure_ascii=False, indent=2)
                    )
                    if responder is None:
                        return {
                            "status": "needs_user",
                            "run_id": run_id,
                            "question": question,
                            "budget": store.budget(run_id),
                        }
                    answer = await responder(question)
                    if answer is None:
                        return {
                            "status": "needs_user",
                            "run_id": run_id,
                            "question": question,
                            "budget": store.budget(run_id),
                        }
                    value = Command(resume=answer)
    except (KeyboardInterrupt, asyncio.CancelledError):
        events(
            "cancelled",
            {"run_id": run_id, "uncertain_actions": store.unresolved_actions(run_id)},
        )
        return {"status": "cancelled", "run_id": run_id}
    finally:
        await browser.close()
        events.close()
