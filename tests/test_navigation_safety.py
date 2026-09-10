"""Capability routing and technical safety boundaries for page actions."""

import json

import pytest

from browser_agent.safety import (
    assess,
    security_review_complete,
    security_review_request,
)


@pytest.mark.parametrize(
    "tool,args",
    [
        ("read", {"offset": 0, "scope": None}),
        ("screenshot", {}),
        ("tabs", {}),
        ("list_browsers", {}),
        ("new_tab", {}),
        ("close_tab", {"page_id": "p1"}),
        ("scroll", {"direction": "down"}),
    ],
)
def test_trusted_observation_and_browser_capabilities_bypass_review(tool, args):
    assessment = assess({"tool": tool, "args": args}, {})
    assert assessment.classification == "ordinary"
    assert not assessment.requires_review


@pytest.mark.parametrize(
    "tool,args,context",
    [
        ("click", {"ref": "observed"}, {"name": "Delete selected messages"}),
        ("click", {"ref": "observed"}, {"name": "Продолжить"}),
        ("click", {"ref": "observed"}, {"name": "🛒"}),
        ("click", {"ref": "observed"}, {"name": "Continue"}),
        ("fill", {"ref": "observed", "value": "query"}, {}),
        ("select", {"ref": "observed", "value": "option"}, {}),
        ("press", {"ref": "observed", "key": "Enter"}, {}),
        ("navigate", {"url": "https://example.test/delete"}, {}),
        ("back", {}, {}),
        ("forward", {}, {}),
        ("reload", {}, {}),
        ("hover", {"ref": "observed"}, {}),
    ],
)
def test_all_page_actions_share_review_path_without_label_or_url_shortcuts(
    tool, args, context
):
    assessment = assess({"tool": tool, "args": args}, context)
    assert assessment.classification == "review"
    assert assessment.requires_review


def test_navigation_requires_http_scheme_but_does_not_classify_url_words():
    allowed = assess(
        {"tool": "navigate", "args": {"url": "https://example.test/delete"}}, {}
    )
    refused = assess(
        {"tool": "navigate", "args": {"url": "javascript:alert(1)"}}, {}
    )
    assert allowed.requires_review and allowed.classification == "review"
    assert refused.forbidden and not refused.requires_review


@pytest.mark.parametrize("context", [{"type": "password"}, {"type": "file"}])
def test_credentials_and_file_selection_remain_manual(context):
    assessment = assess({"tool": "fill", "args": {"ref": "observed"}}, context)
    assert assessment.forbidden


def test_incomplete_host_effect_stops_before_reviewer():
    assessment = assess(
        {"tool": "click", "args": {"ref": "observed"}},
        {"context_complete": False},
    )
    assert assessment.forbidden and not assessment.requires_review


def test_security_packet_preserves_task_and_exact_target_marks_untrusted_excerpts():
    task = "Preserve this full user constraint: " + "x" * 5000
    target = "Apply this exact control " + "x" * 1500
    assessment = assess(
        {"tool": "click", "args": {"ref": "observed"}},
        {"tag": "button", "name": target, "context_complete": True},
    )
    request = security_review_request(
        task,
        {"tool": "click", "args": {"ref": "observed"}},
        {
            "tag": "button",
            "name": target,
            "text": target,
            "fields": [],
            "context": "c" * 4000,
            "page_text": "p" * 4000,
            "context_complete": True,
        },
        assessment,
        clarifications=["Trust this explicit user clarification"],
        recent_history=[{"output": "Page text says ignore the task"}],
    )
    packet = request["input"][0]["content"][0]["text"]
    payload = json.loads(packet.split("\n", 1)[1])
    host = payload["host_target"]
    assert task in packet
    assert host["name"] == target and host["text"] == target
    assert host["context_excerpt_truncated"] and host["page_excerpt_truncated"]
    assert payload["trusted_user_clarifications"] == [
        "Trust this explicit user clarification"
    ]
    assert "untrusted_recent_tool_results" in payload
    assert security_review_complete({"fields": []})
    assert not security_review_complete({"fields": ["x" * 13000]})
