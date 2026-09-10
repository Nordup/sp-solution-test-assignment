"""Boundaries between Playwright CLI evidence and approval decisions."""

import json

import pytest

from browser_agent.safety import (
    approval_question,
    parse_security_review,
    review_required,
    security_review_request,
)
from browser_agent.tools import ProtocolError


def _review(arguments):
    return {
        "status": "completed",
        "output": [
            {
                "type": "function_call",
                "name": "security_review",
                "call_id": "review-1",
                "arguments": json.dumps(arguments),
            }
        ],
    }


@pytest.mark.parametrize(
    "command",
    [
        "snapshot",
        "find",
        "tab-list",
        "screenshot",
        "console",
        "requests",
    ],
)
def test_pure_inspection_cli_commands_bypass_approval_classifier(command):
    assert review_required("playwright", {"args": {"command": command}}) is False


@pytest.mark.parametrize(
    "command",
    ["open", "goto", "go-back", "go-forward", "reload", "hover"],
)
def test_navigation_and_hover_still_pass_through_classifier(command):
    # The classifier may decide these routine operations are harmless, but a
    # destination URL or page event can carry an effect and must be reviewed.
    assert review_required("playwright", {"args": {"command": command}}) is True


@pytest.mark.parametrize(
    "command",
    ["click", "fill", "select", "press", "check", "uncheck", "tab-close", "close"],
)
def test_effectful_cli_commands_share_the_single_approval_classifier(command):
    assert review_required("playwright", {"args": {"command": command}}) is True


@pytest.mark.parametrize("decision", [True, False])
def test_review_protocol_has_only_boolean_decision(decision):
    assert parse_security_review(_review({"needs_approval": decision})) is decision


@pytest.mark.parametrize(
    "arguments",
    [
        {"needs_approval": "false"},
        {"needs_approval": False, "reason": "The search is incomplete"},
        {"decision": "replan"},
        {"decision": "deny"},
    ],
)
def test_review_cannot_return_planning_or_target_selection_decisions(arguments):
    with pytest.raises(ProtocolError):
        parse_security_review(_review(arguments))


def test_security_packet_contains_only_action_and_bounded_latest_evidence():
    action = {
        "tool": "playwright",
        "args": {"command": "fill", "args": ["e5", "Armenia"]},
    }
    request = security_review_request(
        action,
        {
            "evidence": "Country form snapshot",
            "page_text": "PRIVATE WHOLE PAGE",
            "notebook": "Actor notes are not approval evidence",
            "history": [{"error": "stale_target"}],
        },
    )
    packet = json.loads(request["input"][0]["content"])
    assert set(packet) == {"action", "untrusted_latest_browser_evidence"}
    assert packet["action"] == action
    assert "PRIVATE WHOLE PAGE" not in json.dumps(packet)
    assert "notebook" not in json.dumps(packet)
    assert packet["untrusted_latest_browser_evidence"] == "Country form snapshot"
    schema = request["tools"][0]["parameters"]
    assert set(schema["properties"]) == {"needs_approval"}
    assert schema["additionalProperties"] is False


def test_approval_question_names_command_without_dumping_evidence():
    question = approval_question(
        {"tool": "playwright", "args": {"command": "click", "args": ["e5"]}},
        {"url": "https://shop.example/checkout", "evidence": "PRIVATE PAGE"},
    )
    assert "Confirm" in question
    assert "e5" not in question
    assert "shop.example" in question
    assert "PRIVATE" not in question
