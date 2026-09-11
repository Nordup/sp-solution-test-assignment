"""Interactive entry point for a reusable visible-browser session."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from functools import partial
from typing import Any

import typer
from rich.console import Console

from .agent import run_task
from .browser import BrowserError, PlaywrightCLI
from .config import Settings
from .model import ProviderFailure
from .presentation import TerminalUI
from .terminal import Terminal

app = typer.Typer(add_completion=False)
console = Console()

_APPROVAL_DECISIONS = {
    "y": True,
    "yes": True,
    "n": False,
    "no": False,
    "": False,
    "/stop": False,
    "/pause": False,
}
_TASK_STOP_COMMANDS = frozenset({"/stop", "/pause"})
_MISSING_KEY_MESSAGE = "Set OPENAI_API_KEY in .env.local before starting."
_CANCELLED_RESULT = {
    "status": "partial",
    "summary": (
        "Task stopped. The browser remains open. An action already sent to the "
        "browser may have taken effect; inspect it before retrying."
    ),
    "remaining": [],
}


async def respond_to_question(
    question: dict[str, Any],
    *,
    read: Callable[..., Awaitable[str]],
    ui: TerminalUI | None = None,
) -> dict[str, Any] | str | None:
    """Collect one human response, binding approvals to their request ID."""

    if ui is not None:
        ui.question(question)

    if question["kind"] == "approval":
        prompt = "[y/N]: "
        while True:
            answer = await read(prompt, erase_when_done=True)
            normalized = answer.strip().casefold()
            approved = _APPROVAL_DECISIONS.get(normalized)
            if approved is None:
                prompt = "Please enter y or n [y/N]: "
                continue
            if ui is not None:
                ui.user(answer if normalized else "No")
            return {
                "request_id": question["request_id"],
                "approved": approved,
            }

    answer = await read("Reply, or /stop to end this task: ", erase_when_done=True)
    if ui is not None:
        ui.user(answer)
    return None if answer.strip() in _TASK_STOP_COMMANDS else answer


async def _read_task(terminal: Terminal) -> str | None:
    try:
        return (await terminal.read("Task: ", erase_when_done=True)).strip()
    except EOFError:
        return None


async def _run_one_task(
    settings: Settings,
    task: str,
    browser: PlaywrightCLI,
    terminal: Terminal,
    ui: TerminalUI,
    *,
    debug: bool,
) -> Mapping[str, Any]:
    result = await terminal.run(
        run_task(
            settings,
            task,
            browser,
            responder=partial(respond_to_question, read=terminal.read, ui=ui),
            console=console,
            raise_on_cancel=True,
            debug=debug,
            ui=ui,
        )
    )
    return _CANCELLED_RESULT if result is None else result


async def run_session(settings: Settings, *, debug: bool = False) -> None:
    """Reuse one browser across tasks and always release session-owned resources."""

    if not settings.api_key.get_secret_value():
        raise ValueError(_MISSING_KEY_MESSAGE)
    settings.prepare()

    browser = PlaywrightCLI(settings)
    terminal = Terminal()
    ui = TerminalUI(console, debug=debug)
    try:
        await browser.start()
        ui.welcome()
        while (task := await _read_task(terminal)) is not None:
            if task == "/exit":
                break
            if not task:
                continue
            ui.user(task)
            ui.result(
                await _run_one_task(
                    settings,
                    task,
                    browser,
                    terminal,
                    ui,
                    debug=debug,
                )
            )
    finally:
        try:
            ui.close()
        finally:
            try:
                terminal.close()
            finally:
                await browser.close()


def _print_startup_error(exc: Exception, *, debug: bool) -> None:
    missing_key = isinstance(exc, ValueError) and "OPENAI_API_KEY" in str(exc)
    if missing_key:
        console.print(_MISSING_KEY_MESSAGE, markup=False)
    elif not debug:
        console.print(
            "Could not start the browser agent session. Check configuration and browser availability.",
            markup=False,
        )
    if debug:
        console.print(f"Session stopped ({type(exc).__name__}).", markup=False)


@app.command()
def main(
    debug: bool = typer.Option(
        False,
        "--debug",
        help="Show safe lifecycle diagnostics; detailed artifacts remain private.",
    ),
) -> None:
    """Open the browser workspace and accept terminal tasks until session exit."""

    try:
        asyncio.run(run_session(Settings.load(), debug=debug))
    except (KeyboardInterrupt, EOFError, asyncio.CancelledError):
        return
    except (BrowserError, OSError, ProviderFailure, RuntimeError, ValueError) as exc:
        _print_startup_error(exc, debug=debug)
        raise typer.Exit(1) from None
