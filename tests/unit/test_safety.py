"""The reviewer has one boolean contract and a frozen CLI action boundary."""

import json

import pytest

from browser_agent.protocol import ProtocolError
from browser_agent.safety import (
    approval_question,
    parse_security_review,
    review_required,
    security_review_request,
)


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


@pytest.mark.parametrize("decision", [True, False])
def test_review_protocol_has_only_boolean_output(decision):
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
def test_reviewer_cannot_return_planning_or_target_selection_decisions(arguments):
    with pytest.raises(ProtocolError):
        parse_security_review(_review(arguments))


def test_security_packet_contains_exact_action_and_bounded_untrusted_evidence():
    action = {
        "tool": "playwright",
        "args": {"command": "eval", "args": ["(el) => el.innerText", "e250"]},
    }
    evidence = "prefix " * 1800 + '{"ref":"e250","name":"Delete selected messages"}'
    request = security_review_request(
        action,
        {
            "evidence": evidence,
            "page_text": "PRIVATE WHOLE PAGE",
            "private_notes": "Actor notes are not approval evidence",
            "history": [{"error": "stale_target"}],
        },
    )
    packet = json.loads(request["input"][0]["content"])
    selected = packet["untrusted_latest_browser_evidence"]
    assert set(packet) == {"action", "untrusted_latest_browser_evidence"}
    assert packet["action"] == action
    assert len(selected) <= 6000
    assert "e250" in selected and "Delete selected messages" in selected
    assert "PRIVATE WHOLE PAGE" not in json.dumps(packet)
    assert "private_notes" not in json.dumps(packet)
    schema = request["tools"][0]["parameters"]
    assert set(schema["properties"]) == {"needs_approval"}
    assert schema["additionalProperties"] is False


def test_reviewer_packet_keeps_late_referenced_frame_target_context():
    action = {"tool": "playwright", "args": {"command": "click", "args": ["f7e219"]}}
    evidence = "prefix " * 1800 + '{"ref":"f7e219","name":"Delete permanently"}'
    request = security_review_request(action, {"evidence": evidence})
    selected = json.loads(request["input"][0]["content"])[
        "untrusted_latest_browser_evidence"
    ]
    assert len(selected) <= 6000
    assert "f7e219" in selected and "Delete permanently" in selected
    assert (
        approval_question(action, {"evidence": evidence})
        == "Confirm “Delete permanently”?"
    )


@pytest.mark.parametrize(
    ("command", "needs_classifier"),
    [
        ("snapshot", False),
        ("find", False),
        ("tab-list", False),
        ("screenshot", False),
        ("console", False),
        ("requests", False),
        ("open", True),
        ("goto", True),
        ("go-back", True),
        ("go-forward", True),
        ("reload", True),
        ("hover", True),
        ("click", True),
        ("fill", True),
        ("select", True),
        ("press", True),
        ("check", True),
        ("uncheck", True),
        ("upload", True),
        ("tab-close", True),
        ("close", True),
        ("eval", True),
    ],
)
def test_cli_command_review_boundary(command, needs_classifier):
    assert (
        review_required("playwright", {"args": {"command": command}})
        is needs_classifier
    )


def test_artifact_reads_bypass_action_classifier():
    assert review_required("read_browser_artifact", {}) is False


def test_approval_question_names_current_target_without_dumping_page():
    question = approval_question(
        {"tool": "playwright", "args": {"command": "click", "args": ["e5"]}},
        {
            "url": "https://shop.example/checkout",
            "target": "Send",
            "evidence": "PRIVATE PAGE",
        },
    )
    assert question == "Confirm “Send” on shop.example?"
    assert "e5" not in question
    assert "PRIVATE" not in question
