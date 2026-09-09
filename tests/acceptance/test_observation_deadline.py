"""Real-browser observation bounds; no provider calls or live accounts."""

import asyncio
import time

import pytest

from browser_agent.browser import BrowserError, BrowserSession


@pytest.fixture
async def browser(tmp_path):
    session = BrowserSession(tmp_path / "profile", headless=True)
    await session.start()
    yield session
    await session.close()


async def test_detached_snapshot_refs_are_discarded_before_fresh_read(
    browser, monkeypatch
):
    await browser.page.set_content(
        '<input type="password" value="PRIVATE_CANARY">'
        + "".join(f"<button>Button {i}</button>" for i in range(60))
    )
    browser.context.set_default_timeout(30)
    original = browser.page.aria_snapshot

    async def detach_after_snapshot(**kwargs):
        snapshot = await original(**kwargs)
        await browser.page.locator("button,input").evaluate_all(
            "els => els.forEach(el => el.remove())"
        )
        return snapshot

    monkeypatch.setattr(browser.page, "aria_snapshot", detach_after_snapshot)
    started = time.monotonic()
    observed = await asyncio.wait_for(browser.observe(), timeout=0.8)
    assert time.monotonic() - started < 0.8
    assert "PRIVATE_CANARY" not in str(observed)
    assert not any(
        data["tag"] in {"button", "input"} for data in browser._registry.values()
    )


async def test_busy_renderer_whole_observation_has_deadline(browser):
    await browser.page.set_content(
        '<button onclick="window.effectCount=(window.effectCount||0)+1">Prepare</button>'
    )
    before = await browser.observe()
    ref = next(
        ref for ref, data in browser._registry.items() if data["tag"] == "button"
    )
    await browser.execute("click", {"ref": ref}, before["id"])
    browser.OBSERVATION_TIMEOUT_SECONDS = 0.15
    await browser.page.evaluate(
        "() => setTimeout(() => { const started = performance.now(); "
        "while (performance.now() - started < 1500) {} }, 30)"
    )
    await asyncio.sleep(0.06)
    started = time.monotonic()
    with pytest.raises(BrowserError) as failure:
        await asyncio.wait_for(browser.observe(), timeout=0.6)
    assert failure.value.code == "observation_timeout"
    assert time.monotonic() - started < 0.6
    assert browser._observation is None and browser._registry == {}
    assert not browser.lock.locked()
    # Recovering the read must never replay the preceding browser effect.
    assert await browser.page.evaluate("window.effectCount") == 1
    browser.OBSERVATION_TIMEOUT_SECONDS = 10
    after = await browser.observe()
    assert after["id"] != before["id"] and after["refs"]
    with pytest.raises(BrowserError, match="no longer current"):
        await browser.execute("click", {"ref": ref}, before["id"])
    assert await browser.page.evaluate("window.effectCount") == 1


async def test_deadline_covers_metadata_and_cancels_batched_reads(browser, monkeypatch):
    await browser.page.set_content("<button>One</button><button>Two</button>")
    await browser.observe()
    browser.OBSERVATION_TIMEOUT_SECONDS = 0.15
    pending, cancelled = set(), set()

    async def blocked_metadata(ref):
        pending.add(ref)
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.add(ref)

    monkeypatch.setattr(browser, "_metadata", blocked_metadata)
    with pytest.raises(BrowserError) as failure:
        await asyncio.wait_for(browser.observe(), timeout=0.6)
    assert failure.value.code == "observation_timeout"
    assert pending and cancelled == pending
    assert browser._registry == {} and browser._observation is None
    assert not browser.lock.locked()


async def test_transient_dom_churn_retries_fresh_snapshot_without_password_leak(
    browser, monkeypatch
):
    await browser.page.set_content(
        '<input type="password" value="TRANSIENT_PRIVATE_CANARY"><button>Loading</button>'
    )
    original = browser.page.aria_snapshot
    calls = 0

    async def replace_after_first_snapshot(**kwargs):
        nonlocal calls
        calls += 1
        snapshot = await original(**kwargs)
        if calls == 1:
            await browser.page.get_by_role("button", name="Loading").evaluate(
                "el => { const next = document.createElement('button'); "
                "next.textContent = 'Ready'; next.onclick = () => next.textContent = 'Done'; "
                "el.replaceWith(next); }"
            )
        return snapshot

    monkeypatch.setattr(browser.page, "aria_snapshot", replace_after_first_snapshot)
    observed = await asyncio.wait_for(browser.observe(), timeout=1)
    assert calls == 2
    assert "TRANSIENT_PRIVATE_CANARY" not in str(observed)
    assert 'button "Ready"' in observed["text"]
    ref = next(
        ref for ref, data in browser._registry.items() if data["tag"] == "button"
    )
    await browser.execute("click", {"ref": ref}, observed["id"])
    assert await browser.page.get_by_role("button", name="Done").count() == 1


@pytest.mark.parametrize("deadline", [10, 0.4])
async def test_persistent_dom_churn_has_attempt_and_shared_time_bounds(
    browser, monkeypatch, deadline
):
    await browser.page.set_content(
        '<input type="password" value="CHURN_PRIVATE_CANARY"><button>Changing</button>'
    )
    browser.OBSERVATION_TIMEOUT_SECONDS = deadline
    original = browser.page.aria_snapshot
    calls = 0

    async def churn_after_every_snapshot(**kwargs):
        nonlocal calls
        calls += 1
        snapshot = await original(**kwargs)
        await browser.page.get_by_role("button", name="Changing").evaluate(
            "el => el.replaceWith(el.cloneNode(true))"
        )
        return snapshot

    monkeypatch.setattr(browser.page, "aria_snapshot", churn_after_every_snapshot)
    started = time.monotonic()
    with pytest.raises(BrowserError) as failure:
        await asyncio.wait_for(browser.observe(), timeout=1.5)
    if deadline == 10:
        assert calls == 3 and failure.value.code == "stale_observation"
    else:
        assert calls == 2 and failure.value.code == "observation_timeout"
        assert time.monotonic() - started < 0.8
    assert browser._observation is None and browser._registry == {}
    assert not browser.lock.locked()


@pytest.mark.parametrize("offset,scope", [(10, None), (0, "observed-ref")])
async def test_stale_scoped_or_continuation_read_never_restarts_as_whole_page(
    browser, monkeypatch, offset, scope
):
    calls = []

    async def stale_read(requested_offset, requested_scope):
        calls.append((requested_offset, requested_scope))
        raise BrowserError("stale_observation", "DOM changed during requested read")

    monkeypatch.setattr(browser, "_observe", stale_read)
    with pytest.raises(BrowserError) as failure:
        await browser.observe(offset=offset, scope=scope)
    assert failure.value.code == "stale_observation"
    assert calls == [(offset, scope)]
