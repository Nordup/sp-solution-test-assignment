"""Terminal session contract; model and browser startup are mocked here."""

import asyncio

import pytest
from typer.testing import CliRunner

from browser_agent import cli
from browser_agent.config import Settings


class _Page:
    def is_closed(self):
        return False


class _Browser:
    def __init__(self, *_args, **_kwargs):
        self.page = _Page()
        self.starts = []
        self.close_calls = 0

    async def start(self, url=None):
        self.starts.append(url)

    async def close(self):
        self.close_calls += 1


@pytest.mark.asyncio
async def test_two_tasks_reuse_one_browser_until_exit(monkeypatch, tmp_path):
    browser = _Browser()
    calls = []
    tasks = iter(["first task", "second task", "/exit"])

    monkeypatch.setattr(cli, "BrowserSession", lambda *_args, **_kwargs: browser)
    monkeypatch.setattr("builtins.input", lambda _prompt: next(tasks))

    async def fake_run_task(settings, task, active_browser, **kwargs):
        calls.append((settings, task, active_browser, kwargs))
        return {"status": "completed", "summary": task, "remaining": []}

    monkeypatch.setattr(cli, "run_task", fake_run_task)
    await cli.session(Settings(artifact_dir=tmp_path, api_key="test-key"))

    assert browser.starts == [None]
    assert browser.close_calls == 1
    assert [call[1] for call in calls] == ["first task", "second task"]
    assert all(call[2] is browser for call in calls)
    assert all(call[3]["raise_on_cancel"] is True for call in calls)


def test_cli_accepts_bare_command_and_rejects_removed_options(monkeypatch):
    runner = CliRunner()
    seen = []

    monkeypatch.setattr(cli.Settings, "load", lambda: object())

    async def fake_session(settings):
        seen.append(settings)

    monkeypatch.setattr(cli, "session", fake_session)
    result = runner.invoke(cli.app, [])
    assert result.exit_code == 0 and len(seen) == 1

    for args in (
        ["run", "task"],
        ["--url", "https://example.test"],
        ["--profile", "personal"],
        ["--headless"],
    ):
        rejected = runner.invoke(cli.app, args)
        assert rejected.exit_code != 0


@pytest.mark.asyncio
async def test_session_propagates_cancellation_to_close_browser(monkeypatch, tmp_path):
    browser = _Browser()
    monkeypatch.setattr(cli, "BrowserSession", lambda *_args, **_kwargs: browser)
    monkeypatch.setattr("builtins.input", lambda _prompt: "task")

    async def cancelled(*_args, **_kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(cli, "run_task", cancelled)
    with pytest.raises(asyncio.CancelledError):
        await cli.session(Settings(artifact_dir=tmp_path, api_key="test-key"))
    assert browser.close_calls == 1
