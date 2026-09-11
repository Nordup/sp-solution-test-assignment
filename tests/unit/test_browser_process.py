"""Playwright CLI command and subprocess behavior without a real browser."""

from __future__ import annotations

import asyncio
import json

import pytest

from browser_agent.browser import BrowserError, PlaywrightCLI
from browser_agent.browser.commands import build_argv, validate_invocation
from browser_agent.config import Settings


class _FakeProcess:
    def __init__(self, stdout=b"{}", stderr=b"", *, wait_for_release=False):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = 0
        self.wait_for_release = wait_for_release
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.signaled = False
        self.waited = False

    async def communicate(self):
        self.started.set()
        if self.wait_for_release:
            await self.release.wait()
        return self.stdout, self.stderr

    def send_signal(self, _signal):
        self.signaled = True
        self.release.set()

    def kill(self):
        self.signaled = True
        self.release.set()

    async def wait(self):
        self.waited = True
        self.release.set()
        return self.returncode


def _settings(tmp_path):
    return Settings(artifact_dir=tmp_path, browser_headed=False)


@pytest.mark.asyncio
async def test_cli_start_only_prepares_and_child_task_can_execute_and_close(
    monkeypatch, tmp_path
):
    calls = []
    process = _FakeProcess(stdout=json.dumps({"result": "ok"}).encode())

    async def create(*argv, **kwargs):
        calls.append((argv, kwargs))
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    browser = PlaywrightCLI(_settings(tmp_path), session_name="fake-session")
    await browser.start()
    assert calls == []
    result = await asyncio.create_task(
        browser.execute("playwright", {"command": "open", "args": ["about:blank"]})
    )
    assert result["status"] == "executed"
    await asyncio.create_task(browser.close())
    assert [argv[4] for argv, _ in calls] == ["list", "open", "close"]


def test_cli_help_argv_and_host_flag_boundaries():
    argv = build_argv("npx", "@playwright/cli@0.1.19", "argv", "help", [])
    assert argv[-2:] == ["--help", "--json"]
    validate_invocation("mousewheel", ["0", "-500"])
    validate_invocation("attach", ["--cdp", "http://127.0.0.1:9222"])
    validate_invocation("attach", ["--extension=chrome"])
    with pytest.raises(BrowserError):
        validate_invocation("open", ["--profile=/tmp/other"])
    with pytest.raises(BrowserError):
        validate_invocation("open", ["--config=/tmp/config.json"])


@pytest.mark.asyncio
@pytest.mark.parametrize("stdout", [b"", b"not json"])
async def test_cli_malformed_json_is_an_uncertain_transport_error(
    monkeypatch, tmp_path, stdout
):
    process = _FakeProcess(stdout=stdout)

    async def create(*_argv, **_kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    browser = PlaywrightCLI(_settings(tmp_path), session_name="bad-json")
    await browser.start()
    result = await browser.execute("playwright", {"command": "tab-list", "args": []})
    assert result["status"] == "error"
    assert result["error"]["code"] == "cli_parse_error"
    assert result["error"]["uncertain"] is True


@pytest.mark.asyncio
async def test_cli_startup_failure_reports_stderr_instead_of_json_error(
    monkeypatch, tmp_path
):
    process = _FakeProcess(
        stdout=b"",
        stderr=b"/private/cli/session.js:170\nthrow new Error(...)\n"
        b"Error: Daemon process exited with code 1\n"
        b"Error: Browser executable is missing\n    at launch (session.js:170)",
    )
    process.returncode = 1

    async def create(*argv, **_kwargs):
        if argv[4] == "list":
            return _FakeProcess(stdout=b'{"browsers": []}')
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    browser = PlaywrightCLI(_settings(tmp_path))
    await browser.start()
    result = await browser.execute("playwright", {"command": "open", "args": []})
    assert result["status"] == "error"
    assert result["error"]["code"] == "cli_failed"
    assert result["error"]["message"] == "Error: Browser executable is missing"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mismatch",
    [
        {"userDataDir": "/some/other/profile"},
        {"compatible": False},
        {"status": "closed"},
        {"attached": True},
        {"persistent": False},
        {"headed": True},
        {"browserType": "firefox"},
    ],
)
async def test_cli_open_never_reuses_a_mismatched_session(
    monkeypatch, tmp_path, mismatch
):
    browser = PlaywrightCLI(_settings(tmp_path))
    candidate = {
        "name": "existing",
        "userDataDir": str(browser.profile),
        "compatible": True,
        "status": "open",
        "attached": False,
        "persistent": True,
        "headed": False,
        "browserType": "chrome",
        **mismatch,
    }
    calls = []

    async def invoke(command, args):
        calls.append(command)
        return {"browsers": [candidate]} if command == "list" else {}

    monkeypatch.setattr(browser, "_invoke_cli", invoke)
    await browser.start()
    result = await browser.execute(
        "playwright", {"command": "open", "args": ["--browser", "chrome"]}
    )
    assert result["status"] == "executed"
    assert browser.ownership == "owned"
    await browser.close()
    assert calls == ["list", "open", "close"]


@pytest.mark.asyncio
async def test_cli_reuse_navigation_failure_keeps_attachment_for_cleanup(
    monkeypatch, tmp_path
):
    browser = PlaywrightCLI(_settings(tmp_path))
    calls = []

    async def invoke(command, args):
        calls.append(command)
        if command == "list":
            return {
                "browsers": [
                    {
                        "name": "existing",
                        "userDataDir": str(browser.profile),
                        "compatible": True,
                        "status": "open",
                        "attached": False,
                        "persistent": True,
                        "headed": False,
                    }
                ]
            }
        if command == "goto":
            raise BrowserError("cli_error", "Navigation failed", uncertain=True)
        return {}

    monkeypatch.setattr(browser, "_invoke_cli", invoke)
    await browser.start()
    result = await browser.execute(
        "playwright", {"command": "open", "args": ["https://example.invalid"]}
    )
    assert result["status"] == "error"
    assert browser.attached
    await browser.close()
    assert calls == ["list", "attach", "goto", "detach"]


@pytest.mark.asyncio
async def test_cli_profile_lock_race_returns_actionable_error(monkeypatch, tmp_path):
    browser = PlaywrightCLI(_settings(tmp_path))

    async def create(*argv, **_kwargs):
        if argv[4] == "list":
            return _FakeProcess(stdout=b'{"browsers": []}')
        process = _FakeProcess(
            stdout=b"",
            stderr=b"Error: Browser is already in use for /private/profile, use --isolated",
        )
        process.returncode = 1
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    await browser.start()
    result = await browser.execute("playwright", {"command": "open", "args": []})
    assert result["error"]["code"] == "browser_profile_in_use"
    assert "list and attach" in result["error"]["message"]
    assert result["error"]["uncertain"] is False


@pytest.mark.asyncio
async def test_cli_nested_error_envelope_is_not_reported_as_success(
    monkeypatch, tmp_path
):
    process = _FakeProcess(
        stdout=json.dumps(
            {"session": "s", "result": {"isError": True, "error": "Target closed"}}
        ).encode()
    )

    async def create(*_argv, **_kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    browser = PlaywrightCLI(_settings(tmp_path), session_name="nested-error")
    await browser.start()
    result = await browser.execute("playwright", {"command": "open", "args": []})
    assert result["status"] == "error"
    assert result["error"]["code"] == "cli_error"
    assert "Target closed" in result["error"]["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("command", "args", "returncode", "is_error", "expected_uncertain"),
    [
        ("click", ["e1"], 0, True, True),
        ("tab-list", [], 0, True, False),
        ("click", ["e1"], 1, False, True),
        ("tab-list", [], 1, False, False),
    ],
)
async def test_cli_dispatch_failures_report_possible_effects_honestly(
    monkeypatch,
    tmp_path,
    command,
    args,
    returncode,
    is_error,
    expected_uncertain,
):
    payload = (
        {"isError": True, "error": "Clicking the checkbox did not change its state"}
        if is_error
        else {"result": "native process failed"}
    )
    process = _FakeProcess(stdout=json.dumps(payload).encode())
    process.returncode = returncode

    async def create(*_argv, **_kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    browser = PlaywrightCLI(
        _settings(tmp_path), session_name=f"uncertain-{command}-{returncode}"
    )
    await browser.start()
    result = await browser.execute("playwright", {"command": command, "args": args})
    assert result["status"] == "error"
    assert result["error"]["uncertain"] is expected_uncertain
    if is_error:
        assert "did not change its state" in result["error"]["message"]


@pytest.mark.asyncio
async def test_cli_missing_screenshot_artifact_is_error(monkeypatch, tmp_path):
    browser = PlaywrightCLI(_settings(tmp_path), session_name="no-shot")
    await browser.start()

    async def no_file(_command, _args):
        return {"result": "- [Screenshot of viewport](missing.png)"}

    monkeypatch.setattr(browser, "_invoke_cli", no_file)
    result = await browser.execute("playwright", {"command": "screenshot", "args": []})
    assert result["status"] == "error"
    assert result["error"]["code"] == "screenshot_missing"


@pytest.mark.asyncio
async def test_cli_cancellation_reaps_subprocess_and_keeps_private_logs(
    monkeypatch, tmp_path
):
    process = _FakeProcess(
        stdout=b"partial", stderr=b"diagnostic", wait_for_release=True
    )

    async def create(*_argv, **_kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    browser = PlaywrightCLI(_settings(tmp_path), session_name="cancel")
    await browser.start()
    task = asyncio.create_task(
        browser.execute("playwright", {"command": "tab-list", "args": []})
    )
    await process.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert process.signaled and process.waited
    assert list(browser.browser_session_dir.glob("tab-list-*-stdout.log"))
    assert list(browser.browser_session_dir.glob("tab-list-*-stderr.log"))
