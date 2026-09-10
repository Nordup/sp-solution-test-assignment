"""Actual Chromium adapter conformance (no paid models or external accounts)."""

import asyncio
import os
import re
import sys
from pathlib import Path

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
    session = BrowserSession(
        tmp_path / "profile", headless=True, artifact_dir=tmp_path / "evidence"
    )
    await session.start()
    yield session
    await session.close()


async def test_observed_fill_select_click_and_password_privacy(browser):
    await browser.page.set_content(
        """<h1>Form</h1><input aria-label="Name"><input type="password" aria-label="Password" value="CANARY_PRIVATE_PASSWORD"><select aria-label="Size"><option value="s">Small</option><option value="l">Large</option></select><button onclick="this.textContent='Saved'">Save</button>"""
    )
    obs = await browser.observe()
    assert "CANARY_PRIVATE_PASSWORD" not in str(obs)
    context = await browser.describe(ref_for(obs, "Name"), obs["id"])
    assert "CANARY_PRIVATE_PASSWORD" not in str(context)
    await browser.execute(
        "fill", {"ref": ref_for(obs, "Name"), "value": "Ada"}, obs["id"]
    )
    assert (
        await browser.page.get_by_role("textbox", name="Name", exact=True).input_value()
        == "Ada"
    )
    obs = await browser.observe()
    await browser.execute(
        "select", {"ref": ref_for(obs, "Size"), "value": "l"}, obs["id"]
    )
    obs = await browser.observe()
    await browser.execute("click", {"ref": ref_for(obs, "Save")}, obs["id"])
    assert await browser.page.get_by_role("button", name="Saved").count() == 1
    shot = await browser.screenshot()
    assert Path(shot["path"]).stat().st_size > 100
    assert Path(shot["path"]).stat().st_mode & 0o777 == 0o600


async def test_iframe_refs_and_duplicate_names_resolve_identity(browser):
    await browser.page.set_content(
        """<button onclick="this.textContent='First done'">Same</button><button onclick="this.textContent='Second done'">Same</button><iframe srcdoc="&lt;button onclick=&quot;this.textContent='Frame done'&quot;&gt;In frame&lt;/button&gt;"></iframe>"""
    )
    obs = await browser.observe()
    same_refs = [
        re.search(r"\[ref=([^\]]+)\]", line).group(1)
        for line in obs["text"].splitlines()
        if 'button "Same"' in line
    ]
    assert len(same_refs) == 2
    await browser.execute("click", {"ref": same_refs[1]}, obs["id"])
    assert await browser.page.get_by_role("button", name="Second done").count() == 1
    assert (
        await browser.page.get_by_role("button", name="Same", exact=True).count() == 1
    )
    obs = await browser.observe()
    frame_ref = ref_for(obs, "In frame")
    assert frame_ref.startswith("f")
    await browser.execute("click", {"ref": frame_ref}, obs["id"])
    assert (
        await browser.page.frames[1].get_by_role("button", name="Frame done").count()
        == 1
    )


async def test_bounded_observation_pagination_and_ref_membership(browser):
    await browser.page.set_content(
        "<h1>Long page</h1>"
        + "".join(
            f"<p>Visible section {i} {'x' * 200}</p><button>Action {i}</button>"
            for i in range(250)
        )
    )
    obs = await browser.observe()
    assert len(obs["text"]) <= browser.MAX_OBSERVATION_CHARS
    assert obs["truncated"] and obs["next_offset"]
    first_id = obs["id"]
    first_ref = obs["refs"][0]
    with pytest.raises(BrowserError, match="not delivered"):
        await browser.describe("e999999", obs["id"])
    offset = obs["next_offset"]
    continuation = await browser.observe(offset=offset)
    assert continuation["offset"] == offset
    with pytest.raises(BrowserError) as err:
        await browser.describe(first_ref, first_id)
    assert err.value.code == "stale_observation"


async def test_visible_late_portal_control_is_actionable_in_initial_observation(browser):
    await browser.page.set_content(
        "<main>"
        + "".join(f"<p>Restaurant listing {i} {'x' * 240}</p>" for i in range(100))
        + "</main>"
        + '<div style="position:fixed;left:12px;top:24px;z-index:1000">'
        + '<button onclick="this.textContent=\'Orders opened\'">Orders</button>'
        + "</div>"
    )
    observation = await browser.observe()
    assert observation["truncated"]
    orders_ref = ref_for(observation, "Orders")
    await browser.execute("click", {"ref": orders_ref}, observation["id"])
    assert await browser.page.get_by_role("button", name="Orders opened").count() == 1


async def test_unlabelled_late_portal_click_target_is_promoted(browser):
    listings = "".join(
        f"<p>Ресторан {i} " + "описание " * 12 + "</p>" for i in range(90)
    )
    await browser.page.set_content(
        "<main>"
        + listings
        + "</main>"
        + '<div style="position:fixed;left:12px;top:24px;z-index:1000;cursor:pointer" '
        + 'onclick="this.textContent=\'Orders opened\'">Orders</div>'
        + '<input type="password" value="LATE_PRIVATE_VALUE" '
        + 'style="position:fixed;left:12px;top:64px;z-index:1000">'
    )
    observation = await browser.observe()
    assert len(browser._snapshot_text) < 18000
    assert len(browser._snapshot_text.encode()) > 18000
    assert "LATE_PRIVATE_VALUE" not in str(observation)
    orders_ref = re.search(
        r"\[ref=([^\]]+)\]", next(line for line in observation["text"].splitlines() if "Orders" in line)
    ).group(1)
    await browser.execute("click", {"ref": orders_ref}, observation["id"])
    assert await browser.page.get_by_text("Orders opened", exact=True).count() == 1


async def test_profile_cookie_persistence_and_exclusive_lock(tmp_path):
    profile = tmp_path / "persistent"
    session = BrowserSession(profile, headless=True)
    await session.start()
    await session.context.add_cookies(
        [
            {
                "name": "fixture_session",
                "value": "synthetic",
                "domain": "example.test",
                "path": "/",
                "expires": 2000000000,
            }
        ]
    )
    competing = BrowserSession(profile, headless=True)
    with pytest.raises(BrowserError) as err:
        await competing.start()
    assert err.value.code == "profile_busy"
    await session.close()
    await competing.start()
    assert any(
        c["name"] == "fixture_session" for c in await competing.context.cookies()
    )
    await competing.close()


async def test_new_tab_switch_invalidates_old_observation(browser):
    await browser.context.route(
        "https://fixture.test/**",
        lambda route: route.fulfill(body="<h1>Destination</h1>"),
    )
    await browser.page.set_content(
        '<a href="https://fixture.test/destination" target="_blank">Open tab</a>'
    )
    obs = await browser.observe()
    async with browser.context.expect_page():
        await browser.execute("click", {"ref": ref_for(obs, "Open tab")}, obs["id"])
    obs = await browser.observe()
    assert len(obs["tabs"]) == 2
    target = next(t["page_id"] for t in obs["tabs"] if not t["active"])
    await browser.execute("switch_tab", {"page_id": target}, obs["id"])
    with pytest.raises(BrowserError) as err:
        await browser.execute("click", {"ref": ref_for(obs, "Open tab")}, obs["id"])
    assert err.value.code == "stale_observation"
    assert "Destination" in (await browser.observe())["text"]


async def test_start_blank_and_actor_chosen_navigation(browser):
    assert browser.page.url == "about:blank"
    await browser.context.route(
        "https://fixture.test/**",
        lambda route: route.fulfill(body="<h1>Chosen destination</h1>"),
    )
    observation = await browser.observe()
    await browser.execute(
        "navigate",
        {"url": "https://fixture.test/public"},
        observation["id"],
    )
    destination = await browser.observe()
    assert destination["url"] == "https://fixture.test/public"
    assert "Chosen destination" in destination["text"]


async def test_owned_window_resize_updates_page_viewport(tmp_path):
    if sys.platform.startswith("linux") and not (
        os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    ):
        pytest.skip("headed Chromium needs a Linux display")
    session = BrowserSession(tmp_path / "resize-profile", headless=False)
    try:
        await session.start()
        await session.page.set_content(
            "<style>"
            "@media (max-width: 700px) { body { background: rgb(255, 0, 0); } }"
            "@media (min-width: 701px) { body { background: rgb(0, 0, 255); } }"
            "</style><main>responsive fixture</main>"
        )
        cdp = await session.context.new_cdp_session(session.page)
        window = await cdp.send("Browser.getWindowForTarget")
        original = dict(window["bounds"])
        await cdp.send(
            "Browser.setWindowBounds",
            {
                "windowId": window["windowId"],
                "bounds": {**original, "windowState": "normal", "width": 1200, "height": 700},
            },
        )
        await session.page.wait_for_function("window.innerWidth >= 701", timeout=3000)
        wide = await session.page.evaluate(
            "() => ({width: innerWidth, narrow: matchMedia('(max-width: 700px)').matches, background: getComputedStyle(document.body).backgroundColor})"
        )
        await cdp.send(
            "Browser.setWindowBounds",
            {
                "windowId": window["windowId"],
                "bounds": {**original, "windowState": "normal", "width": 600, "height": 500},
            },
        )
        await session.page.wait_for_function("window.innerWidth <= 700", timeout=3000)
        narrow = await session.page.evaluate(
            "() => ({width: innerWidth, narrow: matchMedia('(max-width: 700px)').matches, background: getComputedStyle(document.body).backgroundColor})"
        )
        assert wide["width"] >= 701 and not wide["narrow"]
        assert narrow["width"] <= 700 and narrow["narrow"]
        assert wide["background"] != narrow["background"]
    finally:
        await session.close()


async def test_new_tab_close_last_tab_and_tab_identity(browser):
    await browser.context.route(
        "https://fixture.test/**",
        lambda route: route.fulfill(body="<h1>Second tab</h1>"),
    )
    blank = await browser.observe()
    first_page = blank["tabs"][0]["page_id"]
    opened = await browser.execute("new_tab", {}, blank["id"])
    second_page = next(tab["page_id"] for tab in opened["tabs"] if tab["active"])
    assert second_page != first_page and len(opened["tabs"]) == 2
    with pytest.raises(BrowserError, match="current"):
        await browser.execute("tabs", {}, blank["id"])

    current = await browser.observe()
    await browser.execute(
        "navigate", {"url": "https://fixture.test/second"}, current["id"]
    )
    current = await browser.observe()
    await browser.execute("switch_tab", {"page_id": first_page}, current["id"])
    switched = await browser.observe()
    assert switched["tabs"] == [
        {"page_id": first_page, "url": "about:blank", "active": True},
        {"page_id": second_page, "url": "https://fixture.test/second", "active": False},
    ]

    await browser.execute("close_tab", {"page_id": first_page}, switched["id"])
    remaining = await browser.observe()
    assert remaining["tabs"] == [
        {"page_id": second_page, "url": "https://fixture.test/second", "active": True}
    ]
    await browser.execute("close_tab", {"page_id": second_page}, remaining["id"])
    blank_again = await browser.observe()
    assert len(blank_again["tabs"]) == 1
    assert blank_again["tabs"][0]["active"]
    assert blank_again["url"] == "about:blank"


async def test_hover_and_forward_are_observed_browser_actions(browser):
    await browser.context.route(
        "https://fixture.test/one",
        lambda route: route.fulfill(body="<h1>One</h1>"),
    )
    await browser.context.route(
        "https://fixture.test/two",
        lambda route: route.fulfill(body="<h1>Two</h1>"),
    )
    observation = await browser.observe()
    await browser.execute(
        "navigate", {"url": "https://fixture.test/one"}, observation["id"]
    )
    observation = await browser.observe()
    await browser.execute(
        "navigate", {"url": "https://fixture.test/two"}, observation["id"]
    )
    observation = await browser.observe()
    await browser.execute("back", {}, observation["id"])
    observation = await browser.observe()
    assert observation["url"] == "https://fixture.test/one"
    await browser.execute("forward", {}, observation["id"])
    assert browser.page.url == "https://fixture.test/two"

    await browser.page.set_content(
        '<button onmouseenter="document.querySelector(\'#revealed\').hidden=false">Menu</button>'
        '<a id="revealed" hidden href="https://fixture.test/item">Revealed item</a>'
    )
    observation = await browser.observe()
    await browser.execute(
        "hover", {"ref": ref_for(observation, "Menu")}, observation["id"]
    )
    hovered = await browser.observe()
    assert "Revealed item" in hovered["text"]
    await browser.execute("reload", {}, hovered["id"])
    assert browser.page.url == "https://fixture.test/two"


async def test_navigation_back_press_and_delayed_modal(browser):
    await browser.context.route(
        "https://fixture.test/**",
        lambda route: route.fulfill(
            content_type="text/html",
            body="<input aria-label=\"Search\" onkeydown=\"if(event.key==='Enter') document.querySelector('output').textContent='Searched'\"><output></output><button onclick=\"setTimeout(()=>document.querySelector('dialog').showModal(),50)\">Open</button><dialog><p>Confirmation details</p><button onclick=\"this.closest('dialog').close()\">Dismiss</button></dialog>",
        ),
    )
    obs = await browser.observe()
    await browser.execute("navigate", {"url": "https://fixture.test/start"}, obs["id"])
    obs = await browser.observe()
    await browser.execute(
        "press", {"ref": ref_for(obs, "Search"), "key": "Enter"}, obs["id"]
    )
    assert await browser.page.locator("output").inner_text() == "Searched"
    obs = await browser.observe()
    await browser.execute("click", {"ref": ref_for(obs, "Open")}, obs["id"])
    await browser.page.get_by_role("button", name="Dismiss").wait_for(state="visible")
    obs = await browser.observe()
    assert "Confirmation details" in obs["text"]
    await browser.execute("click", {"ref": ref_for(obs, "Dismiss")}, obs["id"])
    assert not await browser.page.locator("dialog").is_visible()
    obs = await browser.observe()
    await browser.execute("navigate", {"url": "https://fixture.test/other"}, obs["id"])
    obs = await browser.observe()
    await browser.execute("back", {}, obs["id"])
    assert browser.page.url == "https://fixture.test/start"


async def test_utf8_budget_and_scoped_read(browser):
    await browser.page.set_content(
        '<section aria-label="Scope"><h2>Scope title</h2><button>Continue</button></section><p>'
        + "Описание раздела " * 2000
        + "</p>"
    )
    obs = await browser.observe()
    assert len(obs["text"].encode("utf-8")) <= 18000
    scoped = await browser.observe(scope=ref_for(obs, "Scope"))
    assert '"Scope title"' in scoped["text"] and '"Continue"' in scoped["text"]
    assert not scoped["truncated"]
    await browser.execute("click", {"ref": ref_for(scoped, "Continue")}, scoped["id"])


async def test_long_page_does_not_invalidate_complete_local_effect(browser):
    await browser.page.set_content(
        '<a href="https://fixture.test/details">Details</a><form action="https://fixture.test/send"><textarea aria-label="Letter">'
        + "Long letter. " * 200
        + "</textarea><button>Send</button></form><p>"
        + "Unrelated text. " * 3000
        + "</p>"
    )
    obs = await browser.observe()
    link = await browser.describe(ref_for(obs, "Details"), obs["id"])
    assert link["context_complete"] and link["page_text_truncated"]
    send = await browser.describe(ref_for(obs, "Send"), obs["id"])
    assert send["context_complete"]
    assert send["fields"][0]["value"] == "Long letter. " * 200


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


async def test_concurrent_action_requests_serialize_and_cannot_duplicate(browser):
    await browser.page.set_content(
        '<button onclick="window.effects=(window.effects||0)+1">Continue</button>'
    )
    obs = await browser.observe()
    args = {"ref": ref_for(obs, "Continue")}
    results = await asyncio.gather(
        browser.execute("click", args, obs["id"]),
        browser.execute("click", args, obs["id"]),
        return_exceptions=True,
    )
    assert (
        sum(
            isinstance(result, BrowserError) and result.code == "stale_observation"
            for result in results
        )
        == 1
    )
    assert await browser.page.evaluate("window.effects") == 1
