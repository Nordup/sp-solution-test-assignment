"""A real terminal beside the visible browser."""

import asyncio
import importlib.metadata
import json
import os
import sys

import typer
from rich.console import Console
from rich.panel import Panel

from .agent import run_agent, safe_name
from .browser import BrowserSession
from .config import Settings

app = typer.Typer(
    no_args_is_help=True,
    help="Autonomous browser tasks with exact critical-action approval.",
)
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


@app.command()
def doctor():
    """Check local configuration without displaying credentials or making paid calls."""
    settings = Settings.load()
    settings.prepare()
    console.print_json(
        data={
            "python": sys.version.split()[0],
            "model": settings.model,
            "openai_key_present": bool(settings.api_key.get_secret_value()),
            "langsmith_key_present": bool(os.getenv("LANGSMITH_API_KEY")),
            "versions": {
                p: importlib.metadata.version(p)
                for p in ["playwright", "langgraph", "openai", "langsmith"]
            },
        }
    )
    if not settings.api_key.get_secret_value():
        raise typer.Exit(1)


@app.command()
def login(url: str = "about:blank", profile: str = "default"):
    """Log in manually in a dedicated persistent profile."""
    settings = Settings.load()
    settings.prepare()
    safe_name(profile)

    async def launch():
        browser = BrowserSession(settings.artifact_dir / "profiles" / profile)
        try:
            await browser.start(None if url == "about:blank" else url)
            await asyncio.to_thread(
                input, "Log in manually, then press Enter to save and close: "
            )
        finally:
            await browser.close()

    asyncio.run(launch())


@app.command()
def run(
    task: str | None = typer.Argument(None),
    url: str | None = None,
    profile: str = "default",
    budget_usd: float = 5,
    headless: bool = False,
):
    """Enter a task and watch the agent work; the browser is visible by default."""
    task = task or typer.prompt("Task")
    result = asyncio.run(
        run_agent(
            Settings.load(budget_usd=budget_usd),
            task=task,
            url=url,
            profile=profile,
            headless=headless,
            responder=human,
            console=console,
        )
    )
    console.print(
        Panel(json.dumps(result, ensure_ascii=False, indent=2), title="RESULT"),
        markup=False,
    )
    if result["status"] != "completed":
        raise typer.Exit(2)
