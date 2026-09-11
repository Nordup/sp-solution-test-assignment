"""Start a visible browser and accept tasks in the terminal."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
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


async def respond_to_question(
    question: dict[str, Any],
    *,
    read: Callable[..., Awaitable[str]],
    ui: TerminalUI | None = None,
) -> dict[str, Any] | str | None:
    """Read an answer and bind approval to the pending request."""
    if ui is not None:
        ui.question(question)
    if question["kind"] == "approval":
        prompt = "[y/N]: "
        while True:
            answer = await read(prompt, erase_when_done=True)
            normalized = answer.strip().casefold()
            if normalized in {"y", "yes", "n", "no", "", "/stop", "/pause"}:
                if ui is not None:
                    # Empty input is the default No; make that decision visible
                    # in the transcript instead of printing a blank user turn.
                    ui.user(answer if normalized else "No")
                return {
                    "request_id": question["request_id"],
                    "approved": normalized in {"y", "yes"},
                }
            prompt = "Please enter y or n [y/N]: "
    answer = await read("Reply, or /stop to end this task: ", erase_when_done=True)
    if ui is not None:
        ui.user(answer)
    return None if answer.strip() in {"/stop", "/pause"} else answer


async def run_session(settings: Settings, *, debug: bool = False) -> None:
    """Reuse one browser between tasks and release session resources on exit."""
    settings.prepare()
    if not settings.api_key.get_secret_value():
        raise ValueError("Set OPENAI_API_KEY in .env.local before starting.")
    browser = PlaywrightCLI(settings)
    ui = TerminalUI(console, debug=debug)
    terminal = Terminal()
    try:
        # The session owns browser startup and shutdown; tasks reuse its profile.
        await browser.start()
        ui.welcome()
        while True:
            try:
                task = (await terminal.read("Task: ", erase_when_done=True)).strip()
            except EOFError:
                break
            if task == "/exit":
                break
            if not task:
                continue
            ui.user(task)
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
            if result is None:
                result = {
                    "status": "partial",
                    "summary": "Task stopped. The browser remains open. An action already sent to the browser may have taken effect; inspect it before retrying.",
                    "remaining": [],
                }
            ui.result(result)
    finally:
        ui.close()
        terminal.close()
        await browser.close()


@app.command()
def main(
    debug: bool = typer.Option(
        False,
        "--debug",
        help="Show selected diagnostic events while keeping full artifacts private.",
    ),
) -> None:
    """Open the browser workspace and enter tasks. No startup browser is chosen."""
    try:
        settings = Settings.load()
        asyncio.run(run_session(settings, debug=debug))
    except (KeyboardInterrupt, EOFError, asyncio.CancelledError):
        pass
    except (BrowserError, OSError, ProviderFailure, RuntimeError, ValueError) as exc:
        if isinstance(exc, ValueError) and "OPENAI_API_KEY" in str(exc):
            console.print(
                "Set OPENAI_API_KEY in .env.local before starting.", markup=False
            )
            if debug:
                console.print(f"Session stopped ({type(exc).__name__}).", markup=False)
        elif debug:
            console.print(f"Session stopped ({type(exc).__name__}).", markup=False)
        else:
            console.print(
                "Could not start the browser agent session. Check configuration and browser availability.",
                markup=False,
            )
        raise typer.Exit(1) from None
