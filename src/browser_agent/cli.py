"""Start a visible browser and accept tasks in the terminal."""

import asyncio
import json

import typer
from rich.console import Console
from rich.panel import Panel

from .agent import run_task
from .browser import BrowserError, BrowserSession
from .config import Settings
from .llm import ProviderFailure

app = typer.Typer(add_completion=False)
console = Console()


async def human(question):
    console.print(
        Panel(
            json.dumps(
                {
                    k: v
                    for k, v in question.items()
                    if k != "effect" or "details" not in question
                },
                ensure_ascii=False,
                indent=2,
            ),
            title="YOUR INPUT",
            border_style="yellow",
        ),
        markup=False,
    )
    if question["kind"] == "approval":
        answer = await asyncio.to_thread(
            input, "Approve this exact action? Type yes; anything else denies: "
        )
        return {
            "request_id": question["request_id"],
            "approved": answer.strip().lower() == "yes",
        }
    answer = await asyncio.to_thread(input, "Reply, or /stop to end this task: ")
    return None if answer.strip() in {"/stop", "/pause"} else answer


async def session(settings):
    settings.prepare()
    if not settings.api_key.get_secret_value():
        raise ValueError("Set OPENAI_API_KEY in .env.local before starting.")
    browser = BrowserSession(settings.artifact_dir / "profiles" / "default")
    try:
        await browser.start()
        console.print(
            "Browser ready. Enter a task, or /exit to close. You can log in manually in the browser."
        )
        while True:
            try:
                task = (await asyncio.to_thread(input, "Task: ")).strip()
            except EOFError:
                break
            if task == "/exit":
                break
            if not task:
                continue
            result = await run_task(
                settings,
                task,
                browser,
                responder=human,
                console=console,
                raise_on_cancel=True,
            )
            console.print(
                Panel(json.dumps(result, ensure_ascii=False, indent=2), title="RESULT"),
                markup=False,
            )
            if browser.page is None or browser.page.is_closed():
                break
    finally:
        await browser.close()


@app.command()
def main():
    """Open the browser and enter tasks. No startup URL or task options."""
    try:
        asyncio.run(session(Settings.load()))
    except (KeyboardInterrupt, EOFError, asyncio.CancelledError):
        pass
    except (BrowserError, OSError, ProviderFailure, RuntimeError, ValueError) as exc:
        console.print(
            f"Session stopped ({type(exc).__name__}). Check configuration and close any other process using the browser profile.",
            markup=False,
        )
        raise typer.Exit(1) from None
