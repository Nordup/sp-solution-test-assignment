"""Run a labeled synthetic demonstration in a real terminal beside the browser."""

from __future__ import annotations

import argparse
import asyncio
import json

import typer
from rich.panel import Panel

from browser_agent import cli
from browser_agent.browser import BrowserSession
from browser_agent.config import Settings
from browser_agent.runner import run_agent, safe_name
from browser_agent.storage import Store
from evals.fixtures import FixtureServer
from evals.run import fixture_browser_factory


def recording_browser_factory(browser_factory, width=None, height=None):
    """Resize the initial page after the original startup and network isolation."""
    if width is None and height is None:
        return browser_factory
    if (
        type(width) is not int
        or type(height) is not int
        or not 320 <= width <= 3840
        or not 240 <= height <= 2160
    ):
        raise ValueError(
            "Recording viewport requires both width (320–3840) and height (240–2160)."
        )

    class RecordingBrowser(browser_factory):
        async def start(self, url=None):
            await super().start(url)
            try:
                await self.page.set_viewport_size({"width": width, "height": height})
            except BaseException:
                await self.close()
                raise

    return RecordingBrowser


def validate_setup(settings, profile, release_session, width, height):
    safe_name(profile)
    safe_name(release_session)
    if profile == "demo":
        raise ValueError("Keep the live demo profile separate; use demo-synthetic.")
    recording_browser_factory(BrowserSession, width, height)
    settings.prepare()
    # Read the existing allowance; this launcher cannot create or raise it.
    Store(settings.artifact_dir / "state" / "operations.sqlite").budget(
        "release:" + release_session
    )


async def run_demo(
    settings,
    task,
    *,
    fixture_name,
    seed=102,
    profile="demo-synthetic",
    release_session,
    viewport_width=None,
    viewport_height=None,
    runner=run_agent,
):
    validate_setup(settings, profile, release_session, viewport_width, viewport_height)
    # The fixture remains alive during terminal approval/clarification interrupts.
    # Its reference task, expected outcomes and automatic approver are never passed.
    with FixtureServer(fixture_name, seed) as fixture:
        factory = recording_browser_factory(
            fixture_browser_factory(fixture), viewport_width, viewport_height
        )
        return await runner(
            settings,
            task=task,
            url=fixture.url,
            profile=profile,
            headless=False,
            responder=cli.human,
            console=cli.console,
            release_session=release_session,
            synthetic=True,
            browser_factory=factory,
        )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fixture",
        required=True,
        choices=["food_previous_order", "mail_latest_10", "jobs_resume_3"],
    )
    parser.add_argument("--seed", type=int, default=102)
    parser.add_argument("--profile", default="demo-synthetic")
    parser.add_argument("--release-session", required=True)
    parser.add_argument("--budget-usd", type=float, default=5)
    parser.add_argument("--viewport-width", type=int)
    parser.add_argument("--viewport-height", type=int)
    args = parser.parse_args(argv)
    settings = Settings.load(budget_usd=args.budget_usd)
    try:
        validate_setup(
            settings,
            args.profile,
            args.release_session,
            args.viewport_width,
            args.viewport_height,
        )
    except (ValueError, RuntimeError) as exc:
        parser.error(str(exc))
    cli.console.print(
        Panel(
            f"Synthetic evaluation — local fixture\nModel: {settings.model}\n"
            f"Task cap: ${settings.budget_usd:g} | Existing release: {args.release_session}\n"
            f"Profile: {args.profile}\nType your task; review each exact approval in this terminal.",
            title="Browser agent demonstration",
        ),
        markup=False,
    )
    try:
        task = typer.prompt("Task")
        result = asyncio.run(
            run_demo(
                settings,
                task,
                fixture_name=args.fixture,
                seed=args.seed,
                profile=args.profile,
                release_session=args.release_session,
                viewport_width=args.viewport_width,
                viewport_height=args.viewport_height,
            )
        )
    except (KeyboardInterrupt, EOFError, typer.Abort):
        cli.console.print(
            "Stopped. Inspect the private run journal before further actions."
        )
        return 130
    cli.console.print(
        Panel(json.dumps(result, ensure_ascii=False, indent=2), title="RESULT"),
        markup=False,
    )
    return 0 if result["status"] == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
