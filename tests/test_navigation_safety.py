"""Generic UI controls should not need repeated user permission to explore."""

import pytest

from browser_agent.browser import _CONTEXT_JS, BrowserSession
from browser_agent.safety import assess


@pytest.mark.parametrize(
    "markup,critical",
    [
        (
            '<form role="search" method="get"><input type="search"><button>Search</button></form>',
            False,
        ),
        ('<button aria-expanded="false">Menu</button>', False),
        ('<button aria-haspopup="menu">Delete selected messages</button>', True),
        ("<button>Continue</button>", True),
    ],
)
async def test_browser_semantics_distinguish_navigation_from_critical_buttons(
    tmp_path, markup, critical
):
    browser = BrowserSession(tmp_path / "profile", headless=True)
    try:
        await browser.start()
        await browser.page.set_content(markup)
        context = await browser.page.get_by_role("button").evaluate(
            # Use the production metadata reader, not invented classification data.
            _CONTEXT_JS
        )
        assessment = assess({"tool": "click", "args": {"ref": "observed"}}, context)
        assert assessment.requires_approval is critical
        assert not assessment.forbidden
    finally:
        await browser.close()
