"""Show the real terminal actor beside its isolated synthetic browser."""

import argparse
import asyncio
import json

import typer
from rich.panel import Panel

from browser_agent import cli
from browser_agent.agent import run_agent, safe_name
from browser_agent.browser import BrowserSession
from browser_agent.config import Settings
from evals.fixtures import FixtureServer
from evals.run import CORE, fixture_browser_factory


def recording_browser_factory(factory, width=None, height=None):
    if (width is not None or height is not None) and (
        not isinstance(width, int)
        or not isinstance(height, int)
        or not 320 <= width <= 3840
        or not 240 <= height <= 2160
    ):
        raise ValueError(
            "Supply both viewport dimensions: width 320–3840, height 240–2160."
        )

    class RecordingBrowser(factory):
        async def start(self, url=None):
            await super().start(url)
            try:
                if width is not None:
                    await self.page.set_viewport_size(
                        {"width": width, "height": height}
                    )
                await asyncio.to_thread(
                    input, "Arrange browser and Terminal, then press Enter to start: "
                )
            except BaseException:
                await self.close()
                raise

    return RecordingBrowser


async def run_demo(
    settings,
    task,
    *,
    fixture_name,
    seed=102,
    profile="demo-synthetic",
    viewport_width=None,
    viewport_height=None,
    runner=run_agent,
):
    safe_name(profile)
    if profile == "demo":
        raise ValueError("Use demo-synthetic; demo contains the real account.")
    with FixtureServer(fixture_name, seed) as fixture:
        return await runner(
            settings,
            task=task,
            url=fixture.url,
            profile=profile,
            headless=False,
            responder=cli.human,
            console=cli.console,
            synthetic=True,
            browser_factory=recording_browser_factory(
                fixture_browser_factory(fixture), viewport_width, viewport_height
            ),
        )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", choices=CORE, default="food_previous_order")
    parser.add_argument("--seed", type=int, default=102)
    parser.add_argument("--profile", default="demo-synthetic")
    parser.add_argument("--budget-usd", type=float, default=5)
    parser.add_argument("--viewport-width", type=int)
    parser.add_argument("--viewport-height", type=int)
    args = parser.parse_args(argv)
    settings = Settings.load(budget_usd=args.budget_usd)
    recording_browser_factory(BrowserSession, args.viewport_width, args.viewport_height)
    cli.console.print(
        Panel(
            "Synthetic local fixture. Type a task and review exact approvals in this terminal.",
            title="Browser agent",
        )
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
                viewport_width=args.viewport_width,
                viewport_height=args.viewport_height,
            )
        )
    except (KeyboardInterrupt, EOFError, typer.Abort):
        return 130
    cli.console.print(
        Panel(json.dumps(result, ensure_ascii=False, indent=2), title="RESULT"),
        markup=False,
    )
    return 0 if result["status"] == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
