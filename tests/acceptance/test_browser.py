"""Actual Chromium adapter conformance (no paid models or external accounts)."""

import re
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


async def test_headed_adapter_performs_visible_observed_action(tmp_path):
    """Required visible-browser conformance gate; run on the desktop host."""
    session = BrowserSession(tmp_path / "headed", headless=False)
    await session.start()
    try:
        await session.page.set_content(
            "<h1>Headed browser acceptance</h1><button onclick=\"this.textContent='Verified'\">Verify</button>"
        )
        obs = await session.observe()
        await session.execute("click", {"ref": ref_for(obs, "Verify")}, obs["id"])
        assert '"Verified"' in (await session.observe())["text"]
    finally:
        await session.close()


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
