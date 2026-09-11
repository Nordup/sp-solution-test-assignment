# Testing

The [final review](final-review.md) contains the requirements summary and [per-check acceptance results](final-review.md#acceptance-results). The [acceptance runbook](acceptance-tests.md) defines how to perform each check.

## Run the checks

After following the quick start in [README](../README.md), run from the repository root:

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
```

The suite uses model and telemetry doubles, real Chrome on temporary local pages, and a local HTTP server for provider SDK behavior. It requires Node.js and Chrome, but no API key or model credits. The first browser run may download the pinned Playwright CLI.

For a focused run:

```bash
uv run pytest -q tests/unit
uv run pytest -q tests/integration/test_agent.py
uv run pytest -q tests/integration/test_browser.py
uv run pytest -q tests/integration/test_approval_state.py
```

## Coverage

| Area | Behavior covered |
| --- | --- |
| Browser | Navigation, forms, screenshots, scoped references, artifacts, profile reuse, attachment, and ownership on exit. |
| Agent and tools | Native function schemas, argument validation, graph transitions, and context preservation across compaction. |
| Approvals | Approval and decline, classifier failure, changed page/tab state, manual native dialogs, and stopping after an uncertain approved action. |
| Provider | Transient retries, permanent failures, stream cleanup, cancellation, token limits, and shared budget accounting. |
| Terminal and privacy | Input, interruption, continued session use, readable output, and exclusion of private content from telemetry. |

`tests/unit/` covers isolated behavior. `tests/integration/` covers component interactions, real browser behavior, and provider SDK streams. Shared model doubles live in `tests/support/`. Tests use the installed package through the `src/` layout and pytest's `importlib` mode.

## Recorded result

On macOS, 2026-09-11: **179 tests passed in 150.82 seconds**, with no failures or skips. Ruff lint and formatting passed. Frozen dependency installation and CLI help passed; the built wheel imported successfully outside the checkout and included the browser skill.

See [Acceptance results](final-review.md#acceptance-results) for the complete per-check record and [Final review](final-review.md) for the short outcome summary. Detailed run logs, screenshots, costs, and earlier attempts remain in local, Git-ignored `artifacts/`.
