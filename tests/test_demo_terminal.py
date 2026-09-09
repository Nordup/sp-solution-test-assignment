"""Terminal forwarding and fixture lifetime; no model or real-account calls."""

import asyncio
import io
from unittest.mock import AsyncMock
from urllib.error import URLError
from urllib.request import urlopen

import pytest
from playwright.async_api import Error
from rich.console import Console

from browser_agent import cli
from browser_agent.browser import BrowserSession
from browser_agent.config import Settings
from browser_agent.storage import Store
from evals.fixtures import FixtureServer
from evals.run import fixture_browser_factory
from scripts import demo_terminal


def prepared(tmp_path):
    settings = Settings(artifact_dir=tmp_path)
    settings.prepare()
    store = Store(tmp_path / "state" / "operations.sqlite")
    store.create_budget("release:existing-release", 45_000_000, "release")
    return settings, store


def read_page(url):
    with urlopen(url, timeout=2) as response:
        return response.read().decode()


@pytest.mark.parametrize(
    "reply,approved", [("yes", True), ("no", False), ("yes to everything", False)]
)
async def test_terminal_uses_exact_cli_approval_and_keeps_fixture_alive(
    tmp_path, monkeypatch, reply, approved
):
    settings, store = prepared(tmp_path)
    output = io.StringIO()
    terminal = Console(file=output, color_system=None, width=100)
    monkeypatch.setattr(cli, "console", terminal)
    monkeypatch.setattr("builtins.input", lambda prompt: reply)
    captured = []
    task = "Inspect this page; literal text $(not-a-command) stays unchanged."

    async def runner(received_settings, **kwargs):
        captured.append(kwargs)
        assert received_settings is settings
        assert kwargs["responder"] is cli.human and kwargs["console"] is terminal
        assert "SYNTHETIC EVALUATION" in await asyncio.to_thread(
            read_page, kwargs["url"]
        )
        answer = await kwargs["responder"](
            {
                "kind": "approval",
                "request_id": "exact-request",
                "details": {
                    "destination": kwargs["url"],
                    "submitted": {"value": "Exact proposed content"},
                },
            }
        )
        assert answer == {"request_id": "exact-request", "approved": approved}
        assert "SYNTHETIC EVALUATION" in await asyncio.to_thread(
            read_page, kwargs["url"]
        )
        kwargs["console"].print("Actual runner output")
        return {"status": "partial", "run_id": "synthetic-test"}

    before = store.budget("release:existing-release")
    result = await demo_terminal.run_demo(
        settings,
        task,
        fixture_name="food_previous_order",
        release_session="existing-release",
        runner=runner,
    )
    assert result == {"status": "partial", "run_id": "synthetic-test"}
    args = captured[0]
    assert args["task"] == task and args["headless"] is False
    assert args["synthetic"] is True and args["profile"] == "demo-synthetic"
    assert args["release_session"] == "existing-release"
    assert set(args) == {
        "task",
        "url",
        "profile",
        "headless",
        "responder",
        "console",
        "release_session",
        "synthetic",
        "browser_factory",
    }
    assert issubclass(args["browser_factory"], BrowserSession)
    assert store.budget("release:existing-release") == before
    assert (
        "Exact proposed content" in output.getvalue()
        and "Actual runner output" in output.getvalue()
    )
    with pytest.raises(URLError):
        await asyncio.to_thread(read_page, args["url"])


@pytest.mark.parametrize(
    "reply,expected",
    [("Real missing fact", {"answer": "Real missing fact"}), ("/pause", None)],
)
async def test_terminal_clarification_and_pause_use_existing_cli(
    tmp_path, monkeypatch, reply, expected
):
    settings, _ = prepared(tmp_path)
    monkeypatch.setattr("builtins.input", lambda prompt: reply)

    async def runner(_settings, **kwargs):
        assert (
            await kwargs["responder"](
                {"kind": "clarification", "question": "Which option?"}
            )
            == expected
        )
        return {"status": "needs_user"}

    assert (
        await demo_terminal.run_demo(
            settings,
            "Inspect",
            fixture_name="mail_latest_10",
            release_session="existing-release",
            runner=runner,
        )
    )["status"] == "needs_user"


async def test_terminal_cancellation_waits_for_runner_cleanup_then_closes_fixture(
    tmp_path,
):
    settings, _ = prepared(tmp_path)
    started, cleaned = asyncio.Event(), asyncio.Event()
    urls = []

    async def runner(_settings, **kwargs):
        urls.append(kwargs["url"])
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            assert "SYNTHETIC EVALUATION" in await asyncio.to_thread(
                read_page, kwargs["url"]
            )
            cleaned.set()

    pending = asyncio.create_task(
        demo_terminal.run_demo(
            settings,
            "Inspect",
            fixture_name="food_previous_order",
            release_session="existing-release",
            runner=runner,
        )
    )
    await asyncio.wait_for(started.wait(), 3)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert cleaned.is_set()
    with pytest.raises(URLError):
        await asyncio.to_thread(read_page, urls[0])


@pytest.mark.parametrize(
    "status,code",
    [
        ("completed", 0),
        ("partial", 2),
        ("needs_user", 2),
        ("failed", 2),
        ("cancelled", 2),
    ],
)
def test_main_prompts_native_task_and_prints_actual_result(
    tmp_path, monkeypatch, status, code
):
    settings, _ = prepared(tmp_path)
    monkeypatch.setattr(demo_terminal.Settings, "load", lambda **kwargs: settings)
    prompts = []

    def prompt(text):
        prompts.append(text)
        return "My exact typed task"

    monkeypatch.setattr(demo_terminal.typer, "prompt", prompt)
    runner = AsyncMock(
        return_value={"status": status, "summary": "Actual returned outcome"}
    )
    monkeypatch.setattr(demo_terminal, "run_demo", runner)
    output = io.StringIO()
    monkeypatch.setattr(
        cli, "console", Console(file=output, color_system=None, width=100)
    )
    assert (
        demo_terminal.main(
            [
                "--fixture",
                "food_previous_order",
                "--release-session",
                "existing-release",
            ]
        )
        == code
    )
    assert prompts == ["Task"]
    assert runner.call_args.args == (settings, "My exact typed task")
    assert "Synthetic evaluation — local fixture" in output.getvalue()
    assert "Actual returned outcome" in output.getvalue()


@pytest.mark.parametrize(
    "profile,release",
    [
        ("demo", "existing-release"),
        ("../private", "existing-release"),
        ("demo-synthetic", "missing-release"),
    ],
)
async def test_terminal_rejects_live_profile_and_missing_release_without_runner(
    tmp_path, profile, release
):
    settings, store = prepared(tmp_path)
    runner = AsyncMock()
    with pytest.raises((ValueError, RuntimeError)):
        await demo_terminal.run_demo(
            settings,
            "Inspect",
            fixture_name="food_previous_order",
            profile=profile,
            release_session=release,
            runner=runner,
        )
    runner.assert_not_called()
    assert store.budget("release:existing-release")["cap"] == 45_000_000


@pytest.mark.parametrize(
    "error", [KeyboardInterrupt, EOFError, demo_terminal.typer.Abort]
)
def test_initial_prompt_interrupt_never_starts_fixture_or_runner(
    tmp_path, monkeypatch, error
):
    settings, _ = prepared(tmp_path)
    monkeypatch.setattr(demo_terminal.Settings, "load", lambda **kwargs: settings)

    def interrupted(_prompt):
        raise error()

    monkeypatch.setattr(demo_terminal.typer, "prompt", interrupted)
    runner = AsyncMock()
    monkeypatch.setattr(demo_terminal, "run_demo", runner)
    assert (
        demo_terminal.main(
            [
                "--fixture",
                "food_previous_order",
                "--release-session",
                "existing-release",
            ]
        )
        == 130
    )
    runner.assert_not_called()


@pytest.mark.parametrize(
    "width,height",
    [(640, None), (None, 620), (319, 620), (3841, 620), (640, 239), (640, 2161)],
)
def test_recording_viewport_rejects_incomplete_or_unbounded_size(width, height):
    with pytest.raises(ValueError, match="both width"):
        demo_terminal.recording_browser_factory(BrowserSession, width, height)


def test_recording_viewport_default_preserves_factory():
    assert demo_terminal.recording_browser_factory(BrowserSession) is BrowserSession


async def test_actual_browser_viewport_retains_fixture_network_isolation(tmp_path):
    with FixtureServer("food_previous_order", 102) as fixture:
        factory = demo_terminal.recording_browser_factory(
            fixture_browser_factory(fixture), 640, 620
        )
        browser = factory(tmp_path / "profile", headless=True)
        try:
            await browser.start(fixture.url)
            assert browser.page.viewport_size == {"width": 640, "height": 620}
            assert await browser.page.evaluate("[innerWidth, innerHeight]") == [
                640,
                620,
            ]
            assert (await browser.observe())["text"]
            await browser.page.reload(wait_until="domcontentloaded")
            assert browser.page.viewport_size == {"width": 640, "height": 620}
            with pytest.raises(Error):
                await browser.page.goto("https://recording-isolation.invalid/")
            assert fixture.state["blocked_external_requests"] == [
                "https://recording-isolation.invalid/"
            ]
        finally:
            await browser.close()
