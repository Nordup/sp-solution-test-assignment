"""Generic UI controls should not need repeated user permission to explore."""

import json

import pytest

from browser_agent.browser import _CONTEXT_JS, BrowserSession
from browser_agent.safety import (
    assess,
    security_review_complete,
    security_review_request,
)


@pytest.mark.parametrize(
    "markup,classification,review",
    [
        (
            '<form role="search" method="get"><input type="search"><button>Search</button></form>',
            "ordinary",
            False,
        ),
        ('<button aria-expanded="false">Menu</button>', "ordinary", False),
        (
            '<button aria-haspopup="menu">Delete selected messages</button>',
            "consequential",
            False,
        ),
        ("<button>Continue</button>", "review", True),
    ],
)
async def test_browser_semantics_distinguish_navigation_from_critical_buttons(
    tmp_path, markup, classification, review
):
    browser = BrowserSession(tmp_path / "profile", headless=True)
    try:
        await browser.start()
        await browser.page.set_content(markup)
        context = await browser.page.get_by_role("button").evaluate(
            # Use the production metadata reader, not invented classification data.
            _CONTEXT_JS
        )
        assessment = assess({"tool": "click", "args": {"ref": "observed"}}, context)
        assert assessment.classification == classification
        assert assessment.requires_review is review
        assert not assessment.forbidden
    finally:
        await browser.close()


def test_reload_requires_confirmation_because_it_can_resubmit_a_form():
    assessment = assess(
        {"tool": "reload", "args": {}},
        {"document_url": "https://unit.test/form", "context_complete": True},
    )
    assert assessment.requires_review and not assessment.requires_approval


@pytest.mark.parametrize(
    "context,classification,review",
    [
        ({"tag": "button", "name": "Log in", "type": "submit"}, "review", True),
        (
            {
                "tag": "button",
                "name": "Apply filter",
                "type": "submit",
                "form_action": "https://unit.test/search",
            },
            "review",
            True,
        ),
        (
            {"tag": "button", "name": "Remove from cart", "type": "button"},
            "review",
            True,
        ),
        (
            {
                "tag": "input",
                "type": "text",
                "name": "Search terms",
                "value": "Delete selected messages",
            },
            "ordinary",
            False,
        ),
        (
            {
                "tag": "button",
                "name": "Delete selected messages",
                "type": "submit",
                "context_complete": True,
            },
            "consequential",
            False,
        ),
    ],
)
def test_ambiguous_controls_use_reviewer_but_clear_commit_stays_host_gated(
    context, classification, review
):
    assessment = assess({"tool": "click", "args": {"ref": "observed"}}, context)
    assert assessment.classification == classification
    assert assessment.requires_review is review
    assert assessment.requires_approval == (classification == "consequential")


def test_action_like_anchor_href_review_cannot_be_bypassed_by_folder_label():
    action = assess(
        {"tool": "click", "args": {"ref": "observed"}},
        {
            "tag": "a",
            "name": "Details",
            "href": "https://unit.test/messages/delete?id=4",
            "context_complete": True,
        },
    )
    folder = assess(
        {"tool": "click", "args": {"ref": "observed"}},
        {
            "tag": "a",
            "name": "Trash",
            "href": "https://unit.test/mail/trash",
            "context_complete": True,
        },
    )
    assert action.requires_review and action.classification == "review"
    assert folder.classification == "review" and folder.requires_review


def test_security_packet_keeps_exact_target_and_marks_excerpt_truncation():
    target = "Apply this exact control " + "x" * 1500
    assessment = assess(
        {"tool": "click", "args": {"ref": "observed"}},
        {"tag": "button", "name": target, "context_complete": True},
    )
    request = security_review_request(
        "Find the item", {"tool": "click", "args": {"ref": "observed"}}, {
            "tag": "button",
            "name": target,
            "text": target,
            "fields": [],
            "context": "c" * 4000,
            "page_text": "p" * 4000,
            "context_complete": True,
        }, assessment
    )
    payload = json.loads(request["input"][0]["content"][0]["text"].split("\n", 1)[1])
    host = payload["host_target"]
    assert host["name"] == target and host["text"] == target
    assert host["context_excerpt_truncated"] and host["page_excerpt_truncated"]
    assert security_review_complete({"fields": []})
    assert not security_review_complete({"fields": ["x" * 13000]})
