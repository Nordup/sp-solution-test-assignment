# Playwright capability probe

Date: 2026-09-09. Scope: synthetic local HTML in headless installed Chrome on macOS, using an isolated uv environment with Playwright 1.62.0. No LLM, account, network task or LangSmith experiment was involved.

Command executed during research (the saved script has identical contents):

```bash
uv run --no-project --with playwright==1.62.0 python /tmp/sp-assignment-playwright-spike.py
```

Reproduce from this repository with `docs/research/playwright_snapshot_probe.py` in place of the temporary path. Installed Google Chrome is required for this research probe; the production conformance test should additionally use the bundled Chromium.

Observed output:

```text
playwright 1.62.0
- generic [ref=e1]:
  - heading "Capability probe" [level=1] [ref=e2]
  - button "Execute probe" [ref=e3]
  - textbox "Example" [ref=e4]
  - iframe [ref=e5]:
    - button "Frame probe" [ref=f1e2]
ref_click_verified True
iframe_ref_click_verified True
stale_ref_rejected True
```

Conclusions: AI snapshots expose refs; `aria-ref=` resolved a page control and an iframe control; a previous ref failed after page replacement. This probe prints results rather than providing a complete assertion-based test suite. Promote it into proper tests before relying on it in production.

The regular expressions in this small probe locate references in a known synthetic snapshot. They do not parse model responses or recover JSON from prose. Production LLM interaction must use native typed tool calls. The selector adapter needs a version pin and conformance tests; the published snapshot API is documented at [Playwright page.aria_snapshot](https://playwright.dev/python/docs/api/class-page#page-aria-snapshot).
