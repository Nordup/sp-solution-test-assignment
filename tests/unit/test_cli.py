"""Terminal session lifecycle and approval input contract."""

import asyncio

import pytest
from typer.testing import CliRunner

from browser_agent import cli
from browser_agent.config import Settings


class _Browser:
    def __init__(self, *_args, **_kwargs):
        self.artifact_dir = None
        self.starts = 0
        self.close_calls = 0

    async def start(self):
        self.starts += 1

    async def close(self):
        self.close_calls += 1


@pytest.mark.asyncio
async def test_human_question_and_reply_share_the_expected_turn_hooks():
    calls = []

    class UI:
        def question(self, value):
            calls.append(("question", value))

        def user(self, value):
            calls.append(("user", value))

    async def read(message, **kwargs):
        calls.append(("read", message, kwargs))
        return "The work account"

    answer = await cli.respond_to_question(
        {
            "kind": "clarification",
            "request_id": "clarify-1",
            "question": "Which account?",
        },
        read=read,
        ui=UI(),
    )
    assert answer == "The work account"
    assert calls[0][0] == "question"
    assert calls[1] == (
        "read",
        "Reply, or /stop to end this task: ",
        {"erase_when_done": True},
    )
    assert calls[2] == ("user", "The work account")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("answer", "approved"),
    [("y", True), (" YES ", True), ("n", False), ("", False), ("/stop", False)],
)
async def test_human_approval_accepts_standard_answers(answer, approved):
    seen = []

    class UI:
        def question(self, value):
            seen.append(("question", value))

        def user(self, value):
            seen.append(("user", value))

    async def read(message, **kwargs):
        seen.append(("read", message, kwargs))
        return answer

    response = await cli.respond_to_question(
        {
            "kind": "approval",
            "request_id": "approval-1",
            "question": "Send this message?",
            "action": {
                "tool": "playwright",
                "args": {"command": "click", "args": ["e5"]},
            },
        },
        read=read,
        ui=UI(),
    )
    assert response == {"request_id": "approval-1", "approved": approved}
    assert seen[1][1] == "[y/N]: "


@pytest.mark.asyncio
async def test_session_starts_one_browser_and_reuses_it_until_exit(
    monkeypatch, tmp_path
):
    browser = _Browser()
    tasks = iter(["first task", "second task", "/exit"])
    calls = []

    monkeypatch.setattr(cli, "PlaywrightCLI", lambda *_args, **_kwargs: browser)
    monkeypatch.setattr("builtins.input", lambda _prompt: next(tasks))

    async def fake_run_task(settings, task, active_browser, **kwargs):
        calls.append((settings, task, active_browser, kwargs))
        return {"status": "completed", "summary": task, "remaining": []}

    monkeypatch.setattr(cli, "run_task", fake_run_task)
    await cli.run_session(Settings(artifact_dir=tmp_path, api_key="test-key"))

    assert browser.starts == 1 and browser.close_calls == 1
    assert [call[1] for call in calls] == ["first task", "second task"]
    assert all(call[2] is browser for call in calls)


def test_cli_accepts_bare_command_and_rejects_removed_options(monkeypatch):
    runner = CliRunner()
    seen = []
    monkeypatch.setattr(cli.Settings, "load", lambda: object())

    async def fake_session(settings, *, debug=False):
        seen.append((settings, debug))

    monkeypatch.setattr(cli, "run_session", fake_session)
    result = runner.invoke(cli.app, [])
    assert result.exit_code == 0 and len(seen) == 1 and seen[0][1] is False
    debug_result = runner.invoke(cli.app, ["--debug"])
    assert debug_result.exit_code == 0 and seen[-1][1] is True
    for args in (
        ["run", "task"],
        ["--url", "https://example.test"],
        ["--profile", "personal"],
    ):
        assert runner.invoke(cli.app, args).exit_code != 0


def test_main_keeps_startup_errors_human_facing_and_debug_adds_class(monkeypatch):
    runner = CliRunner()
    monkeypatch.setattr(cli.Settings, "load", lambda: object())

    async def missing_key(_settings, *, debug=False):
        raise ValueError("Set OPENAI_API_KEY in .env.local before starting.")

    monkeypatch.setattr(cli, "run_session", missing_key)
    regular = runner.invoke(cli.app, [])
    assert regular.exit_code == 1 and "Set OPENAI_API_KEY" in regular.stdout
    assert "Session stopped" not in regular.stdout
    debug = runner.invoke(cli.app, ["--debug"])
    assert debug.exit_code == 1 and "Session stopped (ValueError)." in debug.stdout


@pytest.mark.asyncio
async def test_session_closes_browser_when_task_is_cancelled(monkeypatch, tmp_path):
    browser = _Browser()
    monkeypatch.setattr(cli, "PlaywrightCLI", lambda *_args, **_kwargs: browser)
    monkeypatch.setattr("builtins.input", lambda _prompt: "task")

    async def cancelled(*_args, **_kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(cli, "run_task", cancelled)
    with pytest.raises(asyncio.CancelledError):
        await cli.run_session(Settings(artifact_dir=tmp_path, api_key="test-key"))
    assert browser.starts == 1 and browser.close_calls == 1
