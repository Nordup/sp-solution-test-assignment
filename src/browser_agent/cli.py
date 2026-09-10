"""Start a visible browser and accept tasks in the terminal."""

import asyncio
from functools import partial

import typer
from rich.console import Console

from .agent import run_task
from .browser_cli import BrowserError, PlaywrightCLI
from .config import Settings
from .llm import ProviderFailure
from .presentation import TerminalUI
from .terminal import Terminal

app = typer.Typer(add_completion=False)
console = Console()


async def human(question, *, read, ui=None):
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


async def session(settings, *, debug=False):
    settings.prepare()
    if not settings.api_key.get_secret_value():
        raise ValueError("Set OPENAI_API_KEY in .env.local before starting.")
    browser = PlaywrightCLI(settings)
    ui = TerminalUI(console, debug=debug)
    terminal = Terminal()
    try:
        # The official CLI keeps an interactive session in its own process.
        # Start and close it in this same owning task; individual model runs
        # only reset the private evidence cache and reuse the visible profile.
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
                    responder=partial(human, read=terminal.read, ui=ui),
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
):
    """Open the browser workspace and enter tasks. No startup browser is chosen."""
    try:
        settings = Settings.load()
        asyncio.run(session(settings, debug=debug))
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
