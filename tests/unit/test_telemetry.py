"""Private diagnostics must explain waits without affecting browser work or UI."""

import asyncio
import json
from unittest.mock import MagicMock

import pytest

from browser_agent.config import Settings
from browser_agent.model import ModelClient
from browser_agent.telemetry import Events, diagnostic_span
from tests.support.model import fake_client


@pytest.mark.parametrize("debug", [False, True])
def test_diagnostics_stay_in_private_file_even_in_debug(tmp_path, monkeypatch, debug):
    secret = "private-credential-for-test"
    monkeypatch.setenv("DIAGNOSTIC_TEST_KEY", secret)
    ui = MagicMock()
    path = tmp_path / "events.jsonl"
    events = Events(path, ui=ui, debug=debug)
    events._trace_event = MagicMock()
    events("diagnostic", {"phase": "stream", "partial_arguments": secret})
    events._trace_event.assert_not_called()
    ui.event.assert_not_called()
    events.close()

    record = json.loads(path.read_text())
    assert record["event"] == "diagnostic"
    assert record["partial_arguments"] == "[REDACTED]"
    assert path.stat().st_mode & 0o777 == 0o600


def test_events_pass_only_redacted_records_to_ui(tmp_path, monkeypatch):
    seen = []

    class SpyUI:
        def event(self, event, record):
            seen.append((event, record))

        def close(self):
            pass

    monkeypatch.setenv("PRIVATE_TOKEN", "token-value-long-enough")
    events = Events(tmp_path / "events.jsonl", ui=SpyUI())
    events("progress", {"message": "token-value-long-enough"})
    events.close()
    assert seen[0][1]["message"] == "[REDACTED]"
    assert "token-value-long-enough" not in (tmp_path / "events.jsonl").read_text()


async def test_wait_progress_is_logged_without_cancelling_work(monkeypatch):
    monkeypatch.setattr("browser_agent.telemetry.DIAGNOSTIC_INTERVAL_SECONDS", 0.005)
    progress_seen, release = asyncio.Event(), asyncio.Event()
    records = []

    def emit(event, data):
        assert event == "diagnostic"
        records.append(data)
        if data["state"] == "progress":
            progress_seen.set()

    async def work():
        async with diagnostic_span(emit, "browser_execute", step=3) as progress:
            progress["tool"] = "click"
            await release.wait()
        return "finished normally"

    task = asyncio.create_task(work())
    try:
        await asyncio.wait_for(progress_seen.wait(), 1)
        assert not task.done()
        release.set()
        assert await task == "finished normally"
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    assert records[0]["state"] == "started"
    assert records[-1]["outcome"] == "completed"
    assert len({record["phase_id"] for record in records}) == 1
    assert all(record["phase"] == "browser_execute" for record in records)
    assert all(record["step"] == 3 for record in records)
    assert records[1]["tool"] == "click"
    assert records[1]["event_loop_lag_seconds"] >= 0
    assert not any(
        task.get_name().startswith("diagnostic:") for task in asyncio.all_tasks()
    )


@pytest.mark.parametrize("cancelled", [False, True])
async def test_failed_or_cancelled_phase_records_outcome_and_cleans_up(cancelled):
    records = []
    error = (
        asyncio.CancelledError() if cancelled else RuntimeError("native call failed")
    )
    with pytest.raises(type(error)):
        async with diagnostic_span(lambda _, data: records.append(data), "stream_read"):
            raise error
    assert records[-1]["state"] == "finished"
    assert records[-1]["outcome"] == ("cancelled" if cancelled else "error")
    assert records[-1]["error_type"] == type(error).__name__
    assert records[-1]["error_message"] == str(error)
    assert not any(
        task.get_name().startswith("diagnostic:") for task in asyncio.all_tasks()
    )


async def test_langsmith_parentage_metrics_and_private_content(tmp_path, monkeypatch):
    from uuid import uuid4

    from langsmith import tracing_context

    from browser_agent.telemetry import Events

    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    client = MagicMock()
    monkeypatch.setattr("browser_agent.telemetry.Client", lambda: client)
    events = Events(tmp_path / "events.jsonl")
    run_id = str(uuid4())
    events(
        "run_started",
        {
            "run_id": run_id,
            "model": "gpt-5.6-luna",
            "budget_usd": 5,
            "task": "PRIVATE TASK",
            "url": "https://private.example",
        },
    )
    with tracing_context(enabled=False), events.span("decide", step=1):
        model = ModelClient(Settings(), client=fake_client(), emit=events)
        await model.call({"input": "PRIVATE PROMPT"})
        await model.call({"input": "PRIVATE REVIEW"}, purpose="security")
        events(
            "tool_proposed",
            {"step": 1, "tool": "click", "arguments": {"text": "PRIVATE ARGUMENT"}},
        )
    events(
        "result", {"status": "completed", "summary": "PRIVATE RESULT", "run_id": run_id}
    )
    events.close()

    assert events.root.id == events.root.trace_id
    node = events.root.child_runs[0]
    assert node.name == "decide" and len(node.child_runs) == 2
    for span in node.child_runs:
        assert span.run_type == "llm" and span.parent_run_id == node.id
        assert span.outputs["usage_metadata"]["total_tokens"] == 120
        assert len(span.events) == 1 and span.events[0]["name"] == "new_token"
        assert span.end_time is not None
    assert [span.metadata["reasoning_effort"] for span in node.child_runs] == [
        "max",
        "medium",
    ]
    sent = str(client.mock_calls)
    assert "PRIVATE" not in sent and "private.example" not in sent
    assert client.create_run.call_count == 4
    client.flush.assert_called_once_with(timeout=5)


async def test_tracing_off_and_export_failure_do_not_stop_model(tmp_path, monkeypatch):
    from uuid import uuid4

    from browser_agent.telemetry import Events

    factory = MagicMock(side_effect=RuntimeError("PRIVATE SECRET"))
    monkeypatch.setattr("browser_agent.telemetry.Client", factory)
    for enabled in ("false", "true"):
        monkeypatch.setenv("LANGSMITH_TRACING", enabled)
        path = tmp_path / (enabled + ".jsonl")
        events = Events(path)
        events(
            "run_started",
            {"run_id": str(uuid4()), "model": "gpt-5.6-luna", "budget_usd": 5},
        )
        model = ModelClient(Settings(), client=fake_client(), emit=events)
        await model.call({"input": "test"})
        events.close()
        records = path.read_text()
        assert "PRIVATE SECRET" not in records and '"model_usage"' in records
        assert ('"tracing_error"' in records) is (enabled == "true")
    assert factory.call_count == 1
