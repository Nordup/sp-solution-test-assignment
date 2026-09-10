"""Private diagnostics must explain waits without affecting browser work or UI."""

import asyncio
import json
from unittest.mock import MagicMock

import pytest

from browser_agent.telemetry import Events, diagnostic_span


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
    assert not any(task.get_name().startswith("diagnostic:") for task in asyncio.all_tasks())


@pytest.mark.parametrize("cancelled", [False, True])
async def test_failed_or_cancelled_phase_records_outcome_and_cleans_up(cancelled):
    records = []
    error = asyncio.CancelledError() if cancelled else RuntimeError("native call failed")
    with pytest.raises(type(error)):
        async with diagnostic_span(lambda _, data: records.append(data), "stream_read"):
            raise error
    assert records[-1]["state"] == "finished"
    assert records[-1]["outcome"] == ("cancelled" if cancelled else "error")
    assert records[-1]["error_type"] == type(error).__name__
    assert records[-1]["error_message"] == str(error)
    assert not any(task.get_name().startswith("diagnostic:") for task in asyncio.all_tasks())
