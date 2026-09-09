"""Verify bundled headed Chromium and a private persistent profile, without an LLM."""

import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright


async def main():
    root = Path(__file__).resolve().parents[1]
    profile = root / "artifacts/profiles/setup-check"
    profile.mkdir(parents=True, exist_ok=True)
    profile.chmod(0o700)
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            str(profile), headless=False
        )
        page = await context.new_page()
        await page.set_content(
            "<h1>Browser agent setup check</h1><p>Bundled Chromium is ready. This is a local synthetic test.</p><button>Verify browser</button>"
        )
        snapshot = await page.aria_snapshot(mode="ai", depth=5)
        assert "Browser agent setup check" in snapshot and "[ref=" in snapshot
        await page.get_by_role("button", name="Verify browser").click()
        await context.add_cookies(
            [
                {
                    "name": "setup_check",
                    "value": "synthetic",
                    "domain": "example.test",
                    "path": "/",
                    "expires": 2000000000,
                }
            ]
        )
        await context.close()
        context = await p.chromium.launch_persistent_context(
            str(profile), headless=False
        )
        assert any(c["name"] == "setup_check" for c in await context.cookies())
        await context.close()
    print(
        json.dumps(
            {
                "headed_chromium": True,
                "ai_snapshot": True,
                "persistent_profile": True,
                "real_site_login": False,
            }
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
