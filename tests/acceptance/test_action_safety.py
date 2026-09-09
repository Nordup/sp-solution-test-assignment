"""Exercise the real SQLite approval gate, including injected failed writes."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from browser_agent.safety import Policy, resolved_effect
from browser_agent.storage import ApprovalError, DuplicateAction, Store


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "state.db")


@pytest.fixture
def context():
    return {
        "name": "Send message",
        "tag": "button",
        "type": "submit",
        "form_action": "https://example.invalid/messages/recipient-1",
        "form_method": "post",
        "fields": [{"name": "body", "value": "Hello"}],
        "context": "Recipient: Alice",
        "context_complete": True,
        "attached": True,
        "visible": True,
    }


def approved(store, context, action=None, action_id="action-1"):
    action = action or {"tool": "click", "args": {"ref": "e1"}}
    effect = resolved_effect(action, context)
    request = store.request_approval("run", action_id, action, effect, "browser-1")
    store.decide_approval(request, True)
    return action, effect, request


def test_p08_approved_exact_action_executes_once_then_denial_blocks(store, context):
    action, effect, request = approved(store, context)
    effects = []
    store.dispatch("run", "action-1", action, effect, "browser-1", request)
    effects.append("sent")
    store.finish_action("action-1", "verified", {"receipt": "synthetic-receipt"})
    with pytest.raises(DuplicateAction):
        store.dispatch("run", "action-1", action, effect, "browser-1", request)
        effects.append("duplicate")
    assert effects == ["sent"]
    second_context = context | {
        "form_action": "https://example.invalid/messages/recipient-2"
    }
    second_effect = resolved_effect(action, second_context)
    denied = store.request_approval(
        "run", "action-2", action, second_effect, "browser-1"
    )
    store.decide_approval(denied, False)
    with pytest.raises(ApprovalError):
        store.dispatch("run", "action-2", action, second_effect, "browser-1", denied)
    assert store.action("action-2") is None


@pytest.mark.parametrize(
    "change",
    [
        {"form_action": "https://example.invalid/messages/mallory"},
        {"fields": [{"name": "amount", "value": "1000"}]},
        {"fields": [{"name": "body", "value": "Changed letter"}]},
        {"fields": [{"name": "selected", "value": "important", "checked": True}]},
        {"name": "Different action"},
    ],
)
def test_p09_changed_concrete_effect_invalidates_approval(store, context, change):
    action, _, request = approved(store, context)
    changed_effect = resolved_effect(action, context | change)
    with pytest.raises(ApprovalError, match="changed"):
        store.dispatch("run", "action-1", action, changed_effect, "browser-1", request)
    assert store.action("action-1") is None
    assert store.approval(request)["status"] == "approved"


@pytest.mark.parametrize(
    "alternative",
    [
        {"tool": "press", "args": {"ref": "e99", "key": "Enter"}},
        {"tool": "click", "args": {"ref": "e100"}},
        {"tool": "fill", "args": {"ref": "e4", "value": "Different content"}},
    ],
)
def test_p10_denial_cannot_be_bypassed_by_tool_ref_or_payload(
    store, context, alternative
):
    action = {"tool": "click", "args": {"ref": "e1"}}
    effect = resolved_effect(action, context)
    request = store.request_approval("run", "action-1", action, effect, "browser-1")
    store.decide_approval(request, False)
    alternate_context = context | {
        "name": "Message body",
        "fields": [{"name": "body", "value": "Different"}],
    }
    alternate_effect = resolved_effect(alternative, alternate_context)
    with pytest.raises(ApprovalError):
        store.dispatch(
            "run",
            "action-2",
            alternative,
            alternate_effect,
            "browser-1",
            requires_approval=False,
        )
    assert (
        Policy(store)
        .assess(alternative, alternate_context, {"classification": "ordinary"}, "run")
        .forbidden
    )


def test_p11_actor_safe_flag_and_reviewer_cannot_override_target(store, context):
    action = {"tool": "click", "args": {"ref": "e1", "safe": True, "approved": True}}
    assessment = Policy(store).assess(
        action, context, {"classification": "ordinary", "reason": "safe"}, "run"
    )
    assert assessment.requires_approval
    with pytest.raises(ApprovalError):
        store.dispatch("run", "action-1", action, assessment.effect, "browser-1")


def test_p12_restart_and_old_checkpoint_cannot_reuse_consumed_approval(store, context):
    action, effect, request = approved(store, context)
    store.dispatch("run", "action-1", action, effect, "browser-1", request)
    restored = Store(store.path)
    assert restored.approval(request)["status"] == "consumed"
    assert restored.unresolved_actions("run")[0]["id"] == "action-1"
    with pytest.raises(DuplicateAction):
        restored.dispatch("run", "action-1", action, effect, "browser-1", request)
    request2 = restored.request_approval("run", "action-2", action, effect, "browser-1")
    restored.decide_approval(request2, True)
    with pytest.raises(DuplicateAction):
        restored.dispatch("run", "action-2", action, effect, "browser-1", request2)


def test_browser_restart_invalidates_approval(store, context):
    action, effect, request = approved(store, context)
    with pytest.raises(ApprovalError, match="generation changed"):
        store.dispatch("run", "action-1", action, effect, "browser-2", request)
    assert store.action("action-1") is None


def test_expiry_and_ambiguous_input_never_approve(store, context, monkeypatch):
    action = {"tool": "click", "args": {"ref": "e1"}}
    effect = resolved_effect(action, context)
    request = store.request_approval("run", "action-1", action, effect, "browser-1", 1)
    for value in ("yes", "", None, 1):
        with pytest.raises(ApprovalError):
            store.decide_approval(request, value)
    expires = store.approval(request)["expires"]
    monkeypatch.setattr("browser_agent.storage.time.time", lambda: expires + 1)
    with pytest.raises(ApprovalError, match="expired"):
        store.decide_approval(request, True)


def test_failed_journal_write_rolls_back_approval_and_stops_effect(store, context):
    action, effect, request = approved(store, context)
    with sqlite3.connect(store.path) as db:
        db.execute(
            "CREATE TRIGGER fail_action BEFORE INSERT ON actions BEGIN SELECT RAISE(FAIL, 'disk failure'); END"
        )
    effects = []
    with pytest.raises(sqlite3.DatabaseError):
        store.dispatch("run", "action-1", action, effect, "browser-1", request)
        effects.append("sent")
    assert effects == []
    assert store.approval(request)["status"] == "approved"
    assert store.action("action-1") is None


def test_concurrent_dispatch_has_one_winner(store, context):
    action, effect, request = approved(store, context)

    def dispatch(_):
        try:
            store.dispatch("run", "action-1", action, effect, "browser-1", request)
            return True
        except DuplicateAction:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(dispatch, range(2))) == 1


def test_incomplete_context_cannot_be_approved_as_unknown(store, context):
    assessment = Policy(store).assess(
        {"tool": "click", "args": {"ref": "e1"}},
        context | {"context_complete": False},
        {"classification": "ordinary"},
    )
    assert assessment.requires_approval
    assert not assessment.details_complete


@pytest.mark.parametrize("tool", ["back", "scroll", "switch_tab"])
@pytest.mark.parametrize("classification", ["forbidden", "consequential", "uncertain"])
def test_viewing_transition_cannot_ignore_independent_risk(store, tool, classification):
    action = {"tool": tool, "args": {}}
    context = {"url": "https://example.invalid", "context_complete": True}
    assessment = Policy(store).assess(
        action,
        context,
        {
            "classification": classification,
            "reason": "Synthetic unsafe page transition",
        },
        "run",
    )
    assert assessment.classification == classification
    effects = []
    if not assessment.forbidden:
        with pytest.raises(ApprovalError):
            store.dispatch(
                "run",
                "transition",
                action,
                assessment.effect,
                "browser-1",
                requires_approval=assessment.requires_approval,
            )
            effects.append("transition")
    assert not effects
    assert store.action("transition") is None


def test_get_search_submit_type_is_not_itself_a_critical_effect(store):
    action = {"tool": "click", "args": {"ref": "e1"}}
    context = {
        "type": "submit",
        "name": "Search",
        "form_method": "get",
        "form_action": "https://example.invalid/search",
        "fields": [{"name": "q", "value": "music"}],
    }
    assessment = Policy(store).assess(
        action,
        context,
        {"classification": "ordinary", "reason": "Task-scoped GET search"},
        "run",
    )
    assert assessment.classification == "ordinary"
    store.dispatch(
        "run",
        "search",
        action,
        assessment.effect,
        "browser-1",
        requires_approval=assessment.requires_approval,
    )
    assert store.action("search")["status"] == "dispatched"


@pytest.mark.parametrize(
    "action",
    [
        {"tool": "click", "args": {"ref": "e1"}},
        {"tool": "press", "args": {"ref": "e1", "key": "Enter"}},
        {"tool": "press", "args": {"ref": "e1", "key": "Space"}},
    ],
)
def test_post_submit_activation_remains_conservative(store, action):
    context = {
        "type": "submit",
        "name": "Continue",
        "form_method": "post",
        "form_action": "https://example.invalid/submit",
    }
    assessment = Policy(store).assess(
        action,
        context,
        {"classification": "ordinary", "reason": "Spoofed safe claim"},
        "run",
    )
    assert assessment.requires_approval


def test_proven_not_applied_action_can_be_reproposed_with_new_approval(store, context):
    action, effect, request = approved(store, context)
    store.dispatch("run", "action-1", action, effect, "browser-1", request)
    store.finish_action(
        "action-1",
        "failed",
        {"observed_evidence": "Synthetic server receipt establishes no submission"},
    )
    # Reuse of the original dispatch ID or approval remains forbidden.
    with pytest.raises(DuplicateAction):
        store.dispatch("run", "action-1", action, effect, "browser-1", request)
    request2 = store.request_approval("run", "action-2", action, effect, "browser-1")
    store.decide_approval(request2, True)
    store.dispatch("run", "action-2", action, effect, "browser-1", request2)
    assert store.action("action-2")["status"] == "dispatched"


@pytest.mark.parametrize("status", ["uncertain", "observed", "verified", "interrupted"])
def test_not_failed_effect_cannot_be_replayed_with_new_approval(store, context, status):
    action, effect, request = approved(store, context)
    store.dispatch("run", "action-1", action, effect, "browser-1", request)
    store.finish_action("action-1", status)
    request2 = store.request_approval("run", "action-2", action, effect, "browser-1")
    store.decide_approval(request2, True)
    with pytest.raises(DuplicateAction):
        store.dispatch("run", "action-2", action, effect, "browser-1", request2)


@pytest.mark.parametrize(
    "label",
    [
        "Trash",
        "Spam",
        "Security alert",
        "Password reset request",
        "Корзина",
        "Пароль изменён",
    ],
)
def test_ordinary_document_link_category_is_not_a_destructive_action(label):
    context = {
        "tag": "a",
        "role": "link",
        "name": label,
        "href": "https://example.invalid/folder/42",
        "context_complete": True,
    }
    assessment = Policy().assess(
        {"tool": "click", "args": {"ref": "e1"}},
        context,
        {"classification": "ordinary"},
    )
    assert assessment.classification == "ordinary"


@pytest.mark.parametrize(
    "label,href,extra",
    [
        ("Delete item", "https://example.invalid/item/1", {}),
        ("Empty Trash", "https://example.invalid/folder/1", {}),
        ("Move to Spam", "https://example.invalid/folder/1", {}),
        ("Trash this item", "https://example.invalid/item/1", {}),
        ("Reset password", "https://example.invalid/account", {}),
        ("Overview", "https://example.invalid/item/delete?id=1", {}),
        ("Overview", "https://example.invalid/item/%64elete?id=1", {}),
        ("Overview", "https://example.invalid/item?action=trash", {}),
        ("Overview", "https://example.invalid/item?delete=1", {}),
        ("Overview", "https://example.invalid/reset-password", {}),
        ("Trash", "https://example.invalid/folder/1", {"role": "button"}),
        (
            "Trash",
            "https://example.invalid/folder/1",
            {"form_action": "https://example.invalid/write", "form_method": "post"},
        ),
        ("Trash", "javascript:erase()", {}),
    ],
)
def test_navigation_exception_preserves_action_and_destination_risk(label, href, extra):
    context = {
        "tag": "a",
        "role": "link",
        "name": label,
        "href": href,
        "context_complete": True,
    } | extra
    assessment = Policy().assess(
        {"tool": "click", "args": {"ref": "e1"}},
        context,
        {"classification": "ordinary"},
    )
    assert assessment.requires_approval


@pytest.mark.parametrize("classification", ["consequential", "uncertain", "forbidden"])
def test_navigation_noun_cannot_downgrade_independent_reviewer(classification):
    context = {
        "tag": "a",
        "role": "link",
        "name": "Trash",
        "href": "https://example.invalid/folder/42",
    }
    assessment = Policy().assess(
        {"tool": "click", "args": {"ref": "e1"}},
        context,
        {"classification": classification},
    )
    assert assessment.classification == classification


async def test_repeated_folder_and_document_links_navigate_without_approval(tmp_path):
    from test_graph_resume import click, graph_case

    html = '<nav><a href="https://example.invalid/folder/1">Trash</a><a href="https://example.invalid/document/2">Security alert</a><a href="https://example.invalid/document/3">Password reset request</a></nav>'
    script = [
        click("Trash"),
        click("Security alert"),
        click("Trash"),
        click("Password reset request"),
    ]
    async with graph_case(
        tmp_path, html=html, script=script, classification="ordinary"
    ) as (browser, gateway, store, _runtime, graph, config, initial, _events):
        visited = []

        async def page(route):
            visited.append(route.request.url)
            await route.fulfill(
                body=html + "<h1>Observed document</h1>", content_type="text/html"
            )

        await browser.context.route("https://example.invalid/**", page)
        done = await graph.ainvoke(initial, config)
        assert "__interrupt__" not in done
        assert done["result"]["status"] == "partial"  # deterministic actor stops here
        assert gateway.reviews == 4
        assert visited.count("https://example.invalid/folder/1") == 2
        with sqlite3.connect(store.path) as db:
            assert (
                db.execute(
                    "SELECT count(*) FROM actions WHERE run_id=?", (initial["run_id"],)
                ).fetchone()[0]
                == 4
            )
            assert (
                db.execute(
                    "SELECT count(*) FROM approvals WHERE run_id=?",
                    (initial["run_id"],),
                ).fetchone()[0]
                == 0
            )


@pytest.mark.parametrize(
    "html,label",
    [
        ('<a href="https://example.invalid/delete?item=1">Overview</a>', "Overview"),
        ('<a href="https://example.invalid/item/1">Delete item</a>', "Delete item"),
        ('<button onclick="window.effects=1">Trash</button>', "Trash"),
        ('<a role="button" href="https://example.invalid/item/1">Spam</a>', "Spam"),
    ],
)
async def test_deceptive_action_link_or_button_still_pauses_before_effect(
    tmp_path, html, label
):
    from test_graph_resume import click, graph_case, pending

    async with graph_case(
        tmp_path, html=html, script=[click(label)], classification="ordinary"
    ) as (browser, _gateway, _store, _runtime, graph, config, initial, _events):
        visited = []

        async def page(route):
            visited.append(route.request.url)
            await route.fulfill(
                body="<h1>Effect endpoint</h1>", content_type="text/html"
            )

        await browser.context.route("https://example.invalid/**", page)
        result = await graph.ainvoke(initial, config)
        assert pending(result)["kind"] == "approval"
        assert not visited
        assert await browser.page.evaluate("window.effects || 0") == 0
