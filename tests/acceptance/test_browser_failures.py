"""Deterministic DOM races and lifecycle failures in real Chromium."""

import asyncio
import re

import pytest

from browser_agent.browser import BrowserError, BrowserSession


def ref_for(observation, name):
    line = next(
        line
        for line in observation["text"].splitlines()
        if f'"{name}"' in line and "[ref=" in line
    )
    return re.search(r"\[ref=([^\]]+)\]", line).group(1)


@pytest.fixture
async def browser(tmp_path):
    session = BrowserSession(tmp_path / "profile", headless=True)
    await session.start()
    yield session
    await session.close()


async def test_replaced_target_and_changed_form_rejected_before_dispatch(browser):
    await browser.page.set_content(
        '<form><input aria-label="Recipient" value="Alice"><textarea aria-label="Message">First letter</textarea><button type="button" onclick="window.effects=(window.effects||0)+1">Send</button></form>'
    )
    obs = await browser.observe()
    action = {"ref": ref_for(obs, "Send")}
    approved = await browser.action_context("click", action, obs["id"])
    await browser.page.get_by_role("textbox", name="Message").fill("Changed letter")
    with pytest.raises(BrowserError) as err:
        await browser.execute(
            "click", action, obs["id"], expected_fingerprint=approved["fingerprint"]
        )
    assert err.value.code == "stale_ref"
    assert await browser.page.evaluate("window.effects || 0") == 0
    fresh = await browser.observe()
    await browser.page.locator("button").evaluate(
        "el => el.outerHTML='<button type=button>Replacement</button>'"
    )
    with pytest.raises(BrowserError) as err:
        await browser.execute("click", {"ref": ref_for(fresh, "Send")}, fresh["id"])
    assert err.value.code == "stale_ref"


async def test_changed_amount_outside_form_and_selection_changes_fingerprint(browser):
    await browser.page.set_content(
        '<p id="amount">Total $10</p><form><input type="checkbox" aria-label="Message A" checked><button type="button">Delete selected</button></form>'
    )
    obs = await browser.observe()
    ref = ref_for(obs, "Delete selected")
    await browser.page.locator("#amount").evaluate("el=>el.textContent='Total $900'")
    with pytest.raises(BrowserError) as err:
        await browser.describe(ref, obs["id"])
    assert err.value.code == "stale_ref"
    obs = await browser.observe()
    await browser.page.get_by_role("checkbox").uncheck()
    with pytest.raises(BrowserError):
        await browser.describe(ref_for(obs, "Delete selected"), obs["id"])


async def test_disabled_and_obscured_controls_never_forced(browser):
    browser.ACTION_TIMEOUT_MS = 300
    browser.context.set_default_timeout(300)
    await browser.page.set_content(
        '<button disabled>Unavailable</button><button onclick="window.effects=1">Covered</button><div style="position:fixed;inset:0;z-index:99;background:white">Overlay</div>'
    )
    obs = await browser.observe()
    with pytest.raises(BrowserError) as err:
        await browser.execute("click", {"ref": ref_for(obs, "Unavailable")}, obs["id"])
    assert err.value.code == "disabled"
    with pytest.raises(BrowserError) as err:
        await browser.execute("click", {"ref": ref_for(obs, "Covered")}, obs["id"])
    assert not err.value.uncertain  # trial action failed before click dispatch
    assert await browser.page.evaluate("window.effects || 0") == 0


async def test_browser_closes_after_review_and_reopen_changes_generation(browser):
    await browser.page.set_content("<button>Submit</button>")
    obs = await browser.observe()
    generation = browser.generation
    await browser.context.close()
    with pytest.raises(BrowserError) as err:
        await browser.execute("click", {"ref": ref_for(obs, "Submit")}, obs["id"])
    assert err.value.code == "browser_disconnected" and not err.value.uncertain
    await browser.close()
    await browser.start()
    assert browser.generation != generation
    with pytest.raises(BrowserError):
        await browser.describe(ref_for(obs, "Submit"), obs["id"])


async def test_unexpected_dialog_dismissed_without_repeating_effect(browser):
    await browser.page.set_content(
        "<button onclick=\"window.attempts=(window.attempts||0)+1; if(confirm('Confirm transfer?')) window.effects=1\">Transfer</button>"
    )
    obs = await browser.observe()
    with pytest.raises(BrowserError) as err:
        await browser.execute("click", {"ref": ref_for(obs, "Transfer")}, obs["id"])
    assert err.value.code == "dialog_interrupted" and err.value.uncertain
    assert await browser.page.evaluate("window.effects || 0") == 0
    assert await browser.page.evaluate("window.attempts") == 1


async def test_closed_during_inflight_action_is_uncertain(browser):
    started = asyncio.Event()
    release = asyncio.Event()

    async def stalled_navigation(route):
        started.set()
        await release.wait()
        if not browser.page.is_closed():
            await route.abort()

    await browser.context.route("https://fixture.test/stall", stalled_navigation)
    obs = await browser.observe()
    pending = asyncio.create_task(
        browser.execute("navigate", {"url": "https://fixture.test/stall"}, obs["id"])
    )
    await asyncio.wait_for(started.wait(), timeout=5)
    await browser.page.close()
    release.set()
    with pytest.raises(BrowserError) as err:
        await pending
    assert err.value.uncertain


async def test_disallowed_capabilities_and_stale_continuation(browser):
    await browser.page.set_content(
        '<input aria-label="Text"><p>' + "x" * 20000 + "</p>"
    )
    obs = await browser.observe()
    for tool, args, expected in [
        ("navigate", {"url": "javascript:alert(1)"}, "invalid_url"),
        ("navigate", {"url": "file:///etc/passwd"}, "invalid_url"),
        ("press", {"ref": ref_for(obs, "Text"), "key": "Meta+L"}, "invalid_key"),
        ("evaluate", {"code": "document.body.remove()"}, "unknown_tool"),
    ]:
        with pytest.raises(BrowserError) as err:
            await browser.execute(tool, args, obs["id"])
        assert err.value.code == expected
    await browser.page.locator("p").evaluate("el=>el.textContent+='new text'")
    with pytest.raises(BrowserError) as err:
        await browser.observe(offset=obs["next_offset"])
    assert err.value.code == "stale_continuation"


async def test_navigation_retry_after_is_exposed_without_automatic_retry(browser):
    requests = []

    async def rate_limit(route):
        requests.append(route.request.url)
        await route.fulfill(
            status=429, headers={"Retry-After": "3"}, body="<h1>Rate limited</h1>"
        )

    await browser.context.route("https://fixture.test/**", rate_limit)
    obs = await browser.observe()
    await browser.execute(
        "navigate", {"url": "https://fixture.test/rate-limit"}, obs["id"]
    )
    obs = await browser.observe()
    assert obs["http"] == {"status": 429, "retry_after": "3"}
    assert len(requests) == 1


async def test_failed_durable_admission_prevents_browser_effect(browser):
    await browser.page.set_content('<button onclick="window.effects=1">Save</button>')
    obs = await browser.observe()
    action = {"ref": ref_for(obs, "Save")}
    context = await browser.action_context("click", action, obs["id"])
    calls = []

    def disk_failure():
        calls.append("admission")
        raise OSError("synthetic journal unavailable")

    with pytest.raises(OSError, match="journal unavailable"):
        await browser.execute(
            "click",
            action,
            obs["id"],
            expected_fingerprint=context["fingerprint"],
            before_dispatch=disk_failure,
        )
    assert calls == ["admission"]
    assert await browser.page.evaluate("window.effects || 0") == 0


async def test_durable_admission_runs_once_before_dispatch(browser):
    await browser.page.set_content(
        '<button onclick="window.effects=(window.effects||0)+1">Save</button>'
    )
    obs = await browser.observe()
    recorded = []

    async def admit():
        assert await browser.page.evaluate("window.effects || 0") == 0
        recorded.append("dispatching")

    await browser.execute(
        "click", {"ref": ref_for(obs, "Save")}, obs["id"], before_dispatch=admit
    )
    assert recorded == ["dispatching"]
    assert await browser.page.evaluate("window.effects") == 1


async def test_iframe_action_binds_outer_effect_context(browser):
    await browser.page.set_content(
        '<p id="amount">Total $10</p><iframe srcdoc="&lt;button&gt;Pay&lt;/button&gt;"></iframe>'
    )
    obs = await browser.observe()
    await browser.page.locator("#amount").evaluate("el=>el.textContent='Total $999'")
    with pytest.raises(BrowserError) as err:
        await browser.describe(ref_for(obs, "Pay"), obs["id"])
    assert err.value.code == "stale_ref"


async def test_concurrent_action_requests_serialize_and_cannot_duplicate(browser):
    await browser.page.set_content(
        '<button onclick="window.effects=(window.effects||0)+1">Continue</button>'
    )
    obs = await browser.observe()
    args = {"ref": ref_for(obs, "Continue")}
    admissions = []
    results = await asyncio.gather(
        browser.execute(
            "click", args, obs["id"], before_dispatch=lambda: admissions.append("first")
        ),
        browser.execute(
            "click",
            args,
            obs["id"],
            before_dispatch=lambda: admissions.append("second"),
        ),
        return_exceptions=True,
    )
    assert len(admissions) == 1
    assert (
        sum(
            isinstance(result, BrowserError) and result.code == "stale_observation"
            for result in results
        )
        == 1
    )
    assert await browser.page.evaluate("window.effects") == 1
