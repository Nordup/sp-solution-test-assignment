"""The public terminal view stays concise while browser details stay private."""

from io import StringIO
from unittest.mock import MagicMock

from rich.console import Console

from browser_agent.presentation import TerminalUI, _clean


def rendered(*, debug=False, width=78):
    stream = StringIO()
    return (
        TerminalUI(
            Console(file=stream, force_terminal=False, color_system=None, width=width),
            debug=debug,
        ),
        stream,
    )


def test_user_rejection_is_a_skipped_result_without_fake_success():
    ui, stream = rendered()
    ui.event(
        "tool_proposed",
        {
            "tool": "playwright",
            "arguments": {"command": "click", "args": ["e5"]},
        },
    )
    ui.event(
        "tool_result",
        {
            "tool": "playwright",
            "result": {"status": "skipped_by_user"},
        },
    )
    ui.event(
        "tool_result",
        {
            "tool": "playwright",
            "result": {"status": "skipped_by_user"},
        },
    )
    output = stream.getvalue()
    assert output.count("skipped by user") == 1
    assert "pending" not in output
    assert "completed" not in output


def test_failure_reason_is_visible_once_even_when_recovery_repeats_it():
    ui, stream = rendered()
    ui.event(
        "tool_proposed",
        {"tool": "playwright", "arguments": {"command": "click", "args": ["e5"]}},
    )
    ui.event(
        "tool_result",
        {
            "tool": "playwright",
            "result": {
                "status": "failed",
                "error": {"code": "not_visible", "message": "Save is not visible"},
            },
        },
    )
    ui.event("recovery", {"code": "not_visible", "message": "Save is not visible"})
    output = stream.getvalue()
    assert output.count("Save is not visible") == 1
    assert "failed" in output
    assert "not_visible" not in output


def test_playwright_cli_rows_show_arguments_and_hide_successful_results():
    ui, stream = rendered()
    ui.event(
        "tool_proposed",
        {
            "tool": "playwright",
            "arguments": {"command": "fill", "args": ["e5", "Armenia"]},
        },
    )
    ui.event(
        "tool_result",
        {
            "tool": "playwright",
            "result": {"status": "executed", "value": "Armenia"},
        },
    )
    ui.event(
        "tool_proposed",
        {
            "tool": "playwright",
            "arguments": {"command": "goto", "args": ["https://example.test"]},
        },
    )
    ui.event(
        "tool_proposed",
        {"tool": "playwright", "arguments": {"command": "find", "args": ["Search"]}},
    )
    ui.event(
        "tool_proposed",
        {"tool": "playwright", "arguments": {"command": "press", "args": ["Enter"]}},
    )
    output = stream.getvalue()
    assert 'fill · e5: "Armenia"' in output
    assert "goto · https://example.test" in output
    assert "find · Search" in output
    assert "press · Enter" in output
    assert '"args"' not in output
    assert "pending" not in output
    assert "completed" not in output
    assert "failed" not in output
    assert output.count('"Armenia"') == 1


def test_progress_message_and_action_row_are_bounded_to_compact_lines():
    ui, stream = rendered(width=60)
    ui.event(
        "tool_proposed",
        {
            "tool": "playwright",
            "arguments": {"command": "click", "args": ["e5"]},
            "message": "This is a very long progress explanation " * 20,
        },
    )
    lines = [line for line in stream.getvalue().splitlines() if line.strip()]
    assert len(lines) == 3  # Agent heading, one compact message, one action row
    assert all(len(line) <= 120 for line in lines)
    assert lines[-1].startswith("  →")
    assert "pending" not in stream.getvalue()


def test_invalid_model_response_is_distinct_from_the_previous_browser_action():
    ui, stream = rendered()
    ui.event(
        "tool_proposed",
        {"tool": "playwright", "arguments": {"command": "click", "args": ["e5"]}},
    )
    ui.event(
        "tool_result",
        {"tool": "playwright", "result": {"status": "executed"}},
    )
    ui.event(
        "recovery",
        {
            "code": "invalid_model_response",
            "message": "Incomplete native response; no action executed.",
        },
    )
    output = stream.getvalue()
    assert "model error: Incomplete native response; no action executed." in output
    assert "failed: Incomplete native response" not in output
    assert output.count("e5") == 1


def test_provider_retries_are_visible_without_raw_exception_or_browser_failure():
    ui, stream = rendered()
    ui.event(
        "tool_proposed",
        {"tool": "playwright", "arguments": {"command": "click", "args": ["e5"]}},
    )
    ui.event("tool_result", {"tool": "playwright", "result": {"status": "executed"}})
    ui.event("model_error", {"error_type": "TimeoutError", "purpose": "actor"})
    ui.event(
        "provider_retry",
        {
            "attempt": 2,
            "purpose": "actor",
            "delay_seconds": 2,
            "error_type": "TimeoutError",
        },
    )
    ui.event(
        "provider_retry",
        {
            "attempt": 3,
            "purpose": "actor",
            "delay_seconds": 4,
            "error_type": "TimeoutError",
        },
    )
    output = stream.getvalue()
    assert "retrying model (attempt 2)" in output
    assert "retrying model (attempt 3)" in output
    assert "TimeoutError" not in output
    assert "failed:" not in output
    assert output.count("e5") == 1


def test_long_cli_argument_is_bounded_without_structural_dump():
    ui, _stream = rendered(width=60)
    row = ui._tool_row(
        {
            "tool": "playwright",
            "arguments": {"command": "click", "args": ["e5", "x" * 400]},
        }
    )
    assert len(row) <= 112
    assert "{" not in row


def test_observe_metadata_stays_silent():
    ui, stream = rendered()
    ui.event(
        "observe",
        {
            "id": "observation-1",
            "url": "https://mail.example.test/inbox",
            "title": "Inbox",
            "tabs": [{"page_id": "page-1", "title": "Inbox", "active": True}],
            "workspace": [
                {
                    "browser_id": "browser-1",
                    "tabs": [{"page_id": "page-1", "title": "Inbox", "active": True}],
                }
            ],
        },
    )
    assert stream.getvalue() == ""


def test_read_browser_artifact_row_shows_path_and_offset_without_result_dump():
    ui, stream = rendered()
    ui.event(
        "tool_proposed",
        {
            "tool": "read_browser_artifact",
            "arguments": {"path": "/tmp/browser-artifact.txt", "offset": 0},
        },
    )
    ui.event(
        "tool_result",
        {
            "tool": "read_browser_artifact",
            "result": {"status": "executed", "text": "PRIVATE DOM"},
        },
    )
    output = stream.getvalue()
    assert "read · /tmp/browser-artifact.txt (offset 0)" in output
    assert "PRIVATE DOM" not in output


def test_search_browser_artifact_row_shows_query_and_hides_matches():
    ui, stream = rendered()
    ui.event(
        "tool_proposed",
        {
            "tool": "search_browser_artifact",
            "arguments": {"path": "/tmp/browser-artifact.txt", "query": "Armenia"},
        },
    )
    ui.event(
        "tool_result",
        {
            "tool": "search_browser_artifact",
            "result": {
                "status": "found",
                "matches": [{"line": 3, "text": "PRIVATE MATCH"}],
            },
        },
    )
    output = stream.getvalue()
    assert "search · Armenia" in output
    assert "PRIVATE MATCH" not in output
    assert "pending" not in output
    assert "completed" not in output


def test_approval_preserves_graph_prompt_and_hides_private_action_packet():
    ui, stream = rendered()
    prompt = "Send the message to team@example.test?"
    ui.question(
        {
            "kind": "approval",
            "request_id": "private-request-id",
            "question": prompt,
            "action": {
                "tool": "playwright",
                "args": {"command": "click", "args": ["e5"]},
            },
            "details": {"fields": [{"name": "Message", "value": "private draft"}]},
        }
    )
    output = stream.getvalue()
    assert prompt in output
    assert output.count(prompt) == 1
    assert "private-request-id" not in output
    assert "team@example.test" in output
    assert "private draft" not in output
    assert "Tool:" not in output and "Details:" not in output


def test_question_deduplicates_until_user_replies_and_keeps_one_agent_heading():
    ui, stream = rendered()
    question = {
        "kind": "clarification",
        "request_id": "clarify-1",
        "question": "Which account should I use?",
    }
    ui.user("Find the latest invoice")
    ui.question(question)
    ui.question(question)
    ui.user("The work account")
    ui.question(question)
    output = stream.getvalue()
    assert output.count("Which account should I use?") == 2
    assert output.count("Agent:") == 2
    assert output.count("You:") == 2


def test_final_report_is_plain_prose_without_status_box_or_heading():
    ui, stream = rendered()
    ui.result(
        {
            "status": "partial",
            "summary": "I inspected two messages.",
            "remaining": [
                "I inspected two messages.",
                "Review the browser state.",
                "Review the browser state.",
            ],
        }
    )
    output = stream.getvalue()
    assert output.count("Review the browser state.") == 1
    assert "I inspected two messages." in output
    assert "Completed" not in output and "Failed" not in output
    assert "╭" not in output and "╰" not in output


def test_private_page_content_and_arbitrary_result_mappings_are_not_dumped():
    ui, stream = rendered()
    ui.event(
        "observe",
        {
            "id": "obs",
            "url": "https://example.test",
            "title": "Example",
            "tabs": [],
            "workspace": [],
            "text": "PRIVATE PAGE CONTENT",
        },
    )
    ui.event(
        "tool_proposed",
        {
            "tool": "playwright",
            "arguments": {"command": "click", "args": ["e5"], "secret": "DO NOT SHOW"},
        },
    )
    ui.event(
        "tool_result",
        {
            "tool": "playwright",
            "result": {
                "status": "executed",
                "page_text": "PRIVATE PAGE CONTENT",
                "fingerprint": "private-fingerprint",
                "message": "clicked",
            },
        },
    )
    output = stream.getvalue()
    assert "PRIVATE PAGE CONTENT" not in output
    assert "DO NOT SHOW" not in output
    assert "private-fingerprint" not in output
    assert "clicked" not in output
    assert "{" not in output


def test_safe_terminal_sanitization_removes_ansi_and_control_characters():
    value = "\x1b]8;;https://example.test\x1b\\Example\x1b]8;;\x1b\\\x00"
    cleaned = _clean(value)
    assert cleaned.startswith("Example")
    assert "\x1b" not in cleaned
    assert "\x00" not in cleaned


def test_non_tty_status_never_emits_spinner_control_sequences():
    ui, stream = rendered()
    ui.event("node_started", {"node": "observe", "step": 1})
    ui.event("node_finished", {"node": "observe", "step": 1})
    ui.close()
    assert "\x1b" not in stream.getvalue()


def test_active_status_stops_for_waiting_and_result():
    class InteractiveUI(TerminalUI):
        @property
        def interactive(self):
            return True

    stream = StringIO()
    console = Console(file=stream, force_terminal=False, color_system=None, width=78)
    status = MagicMock()
    console.status = MagicMock(return_value=status)
    ui = InteractiveUI(console)
    ui.event("node_started", {"node": "decide", "step": 1})
    assert ui._status is status and status.start.called
    ui.event("waiting", {"kind": "clarification", "question": "Ready?"})
    assert ui._status is None and status.stop.called
    status.stop.reset_mock()
    ui.event("node_started", {"node": "decide", "step": 2})
    ui.event("result", {"status": "completed"})
    assert ui._status is status
    ui.result({"status": "completed", "summary": "Done", "remaining": []})
    assert ui._status is None and status.stop.called
