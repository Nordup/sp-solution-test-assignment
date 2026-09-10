"""The reviewer has one boolean contract and a frozen CLI action boundary."""

import json

import pytest

from browser_agent.safety import (
    approval_question,
    parse_security_review,
    review_required,
    security_review_request,
)
from browser_agent.tools import ProtocolError


def review(arguments):
    return {
        "status": "completed",
        "output": [{
            "type": "function_call",
            "name": "security_review",
            "call_id": "review-1",
            "arguments": json.dumps(arguments),
        }],
    }


@pytest.mark.parametrize("decision", [True, False])
def test_review_has_one_boolean_output(decision):
    assert parse_security_review(review({"needs_approval": decision})) is decision


@pytest.mark.parametrize("arguments", [
    {"needs_approval": "false"},
    {"needs_approval": False, "reason": "extra"},
    {"decision": "replan"},
])
def test_reviewer_cannot_return_planning_decisions(arguments):
    with pytest.raises(ProtocolError):
        parse_security_review(review(arguments))


def test_packet_contains_exact_cli_action_and_bounded_untrusted_evidence():
    action = {"tool": "playwright", "args": {"command": "click", "args": ["e5"]}}
    request = security_review_request(action, {"evidence": "snapshot: e5 [Send]"})
    packet = json.loads(request["input"][0]["content"])
    assert packet["action"] == action
    assert packet["untrusted_latest_browser_evidence"] == "snapshot: e5 [Send]"
    schema = request["tools"][0]["parameters"]
    assert set(schema["properties"]) == {"needs_approval"}


def test_reviewer_packet_keeps_late_referenced_target_context():
    action = {"tool": "playwright", "args": {"command": "click", "args": ["e250"]}}
    evidence = "prefix " * 1800 + '{"ref":"e250","name":"Delete selected messages"}'
    request = security_review_request(action, {"evidence": evidence})
    packet = json.loads(request["input"][0]["content"])
    selected = packet["untrusted_latest_browser_evidence"]
    assert len(selected) <= 6000
    assert "e250" in selected and "Delete selected messages" in selected


def test_approval_question_mentions_command_target_without_whole_page_dump():
    question = approval_question(
        {"tool": "playwright", "args": {"command": "click", "args": ["e5"]}},
        {"target": "Send"},
    )
    assert question == "Confirm “Send”?"
    assert "PRIVATE PAGE" not in question


def test_read_only_cli_commands_bypass_classifier():
    for command in ("snapshot", "find", "screenshot", "tab-list"):
        assert review_required("playwright", {"args": {"command": command}}) is False
    for command in ("click", "fill", "press", "select", "upload", "tab-close"):
        assert review_required("playwright", {"args": {"command": command}}) is True
    assert review_required("read_browser_artifact", {}) is False
