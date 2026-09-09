"""Open the dedicated demo profile for manual login. No LLM or credential capture."""

import argparse
import asyncio
from pathlib import Path

from playwright.async_api import Error, async_playwright


async def main(url: str):
    profile = Path(__file__).resolve().parents[1] / "artifacts/profiles/demo"
    profile.mkdir(parents=True, exist_ok=True)
    profile.chmod(0o700)
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            str(profile), headless=False
        )
        closed = asyncio.Event()
        context.on("close", lambda _: closed.set())
        page = context.pages[0] if context.pages else await context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        except Error as exc:
            print("Page load needs manual attention:", type(exc).__name__, flush=True)
        print(
            "Dedicated demo profile open for manual login. Close this browser when finished to save and release the profile.",
            flush=True,
        )
        await closed.wait()
        print("Demo browser closed; profile released.", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="Site to open for manual login")
    args = parser.parse_args()
    if not args.url.startswith(("https://", "http://")):
        parser.error("Use an HTTP(S) URL")
    asyncio.run(main(args.url))
