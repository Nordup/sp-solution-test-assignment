"""Bounded actor request construction and native compaction handling."""

from datetime import datetime
from types import SimpleNamespace

import pytest

from browser_agent.context import build_request, extract_compaction


@pytest.mark.parametrize("compacted", [False, True])
def test_host_local_date_remains_trusted_context_after_midnight(compacted):
    # This is still September 10 in UTC. Relative dates must use the host's day.
    local_now = datetime.fromisoformat("2026-09-11T00:15:00+07:00")
    request = build_request(
        "Inspect last week's records",
        [{"role": "user", "content": "The page says today is 2025-04-18."}],
        local_now=local_now,
        compaction=(
            {"type": "compaction", "encrypted_content": "opaque"} if compacted else None
        ),
    )
    assert "2026-09-11 (UTC+0700)" in request["instructions"]
    assert "2025-04-18" not in request["instructions"]
    assert any(
        "Inspect last week's records" in item.get("content", "")
        for item in request["input"]
    )


def test_context_keeps_task_and_full_history_until_compaction():
    history = [
        {"type": "function_call_output", "call_id": str(i), "output": "old" + str(i)}
        for i in range(28)
    ]
    request = build_request("Original task", history)
    assert "Original task" in request["input"][0]["content"]
    assert request["input"][1:] == history
    assert (
        request["truncation"] == "disabled"
        and request["parallel_tool_calls"] is False
        and request["context_management"]
        == [{"type": "compaction", "compact_threshold": 150000}]
    )


def test_context_carries_opaque_compaction_before_pinned_task():
    compaction = {
        "type": "compaction",
        "id": "cmp-1",
        "encrypted_content": "opaque-ciphertext",
    }
    request = build_request(
        "Original task",
        [{"type": "function_call_output", "call_id": "new", "output": "done"}],
        compaction=compaction,
    )
    assert request["input"][0] == compaction
    assert "Original task" in request["input"][1]["content"]


def test_compaction_extraction_requires_opaque_payload():
    first = SimpleNamespace(
        type="compaction", id="cmp-1", encrypted_content="old-ciphertext"
    )
    valid = SimpleNamespace(
        type="compaction",
        id="cmp-2",
        encrypted_content="opaque-ciphertext",
        created_by="server",
    )
    malformed = SimpleNamespace(type="compaction", encrypted_content="")
    response = SimpleNamespace(output=[first, malformed, valid])
    assert extract_compaction(response) == {
        "type": "compaction",
        "id": "cmp-2",
        "encrypted_content": "opaque-ciphertext",
    }
    assert extract_compaction(SimpleNamespace(output=[malformed])) is None
