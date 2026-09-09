"""Visible browser + terminal interface."""

import asyncio
import importlib.metadata
import json
import os
import sys

import typer
from rich.console import Console
from rich.panel import Panel

from .browser import BrowserSession
from .config import Settings
from .runner import run_agent, safe_name

app = typer.Typer(
    no_args_is_help=True,
    help="Autonomous browser tasks with exact approvals and a persistent $5 maximum.",
)
console = Console()


async def human(question):
    console.print(
        Panel(
            json.dumps(question, ensure_ascii=False, indent=2),
            title="WAITING FOR YOU",
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
    answer = await asyncio.to_thread(input, "Reply, or type /pause to save and exit: ")
    return None if answer.strip() == "/pause" else {"answer": answer}


@app.command()
def doctor(
    online: bool = False, budget_usd: float = 5, release_session: str | None = None
):
    """Check local setup without displaying secrets. Online checks require an explicit budget."""
    settings = Settings.load(budget_usd=budget_usd)
    settings.prepare()
    report = {
        "python": sys.version.split()[0],
        "model": settings.model,
        "openai_key_present": bool(settings.api_key.get_secret_value()),
        "langsmith_key_present": bool(os.getenv("LANGSMITH_API_KEY")),
        "artifact_dir": str(settings.artifact_dir.resolve()),
        "versions": {
            p: importlib.metadata.version(p)
            for p in ["playwright", "langgraph", "openai", "langsmith"]
        },
        "online": online,
    }
    if online:
        from evals.preflight import preflight

        report["online_result"] = asyncio.run(preflight(settings, release_session))
    console.print_json(data=report)
    if not report["openai_key_present"] or (
        online and not report["online_result"].get("passed")
    ):
        raise typer.Exit(1)


@app.command()
def login(url: str = "about:blank", profile: str = "default"):
    """Open a dedicated browser for manual login; close the window when finished."""
    settings = Settings.load()
    settings.prepare()
    safe_name(profile)

    async def launch():
        browser = BrowserSession(settings.artifact_dir / "profiles" / profile)
        try:
            await browser.start(None if url == "about:blank" else url)
            await asyncio.to_thread(
                input, "Log in manually, then press Enter here to save and close: "
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
    release_session: str | None = None,
    headless: bool = False,
):
    """Run an ordinary natural-language task; headed browser is the default."""
    task = task or typer.prompt("Task")
    settings = Settings.load(budget_usd=budget_usd)
    result = asyncio.run(
        run_agent(
            settings,
            task,
            url,
            profile,
            headless=headless,
            responder=human,
            console=console,
            release_session=release_session,
        )
    )
    console.print(
        Panel(json.dumps(result, ensure_ascii=False, indent=2), title="RESULT"),
        markup=False,
    )
    if result["status"] != "completed":
        raise typer.Exit(2)


@app.command()
def resume(run_id: str):
    """Resume the latest checkpoint with the same profile and spending ledger."""
    result = asyncio.run(
        run_agent(Settings.load(), run_id=run_id, responder=human, console=console)
    )
    console.print_json(data=result)
    if result["status"] != "completed":
        raise typer.Exit(2)
