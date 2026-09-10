# Testing

This guide records test coverage and results. Follow [Acceptance tests](acceptance-tests.md) before submission. It maps requirements to checks and gives the exact task prompts, expected outcomes, failure cases, and handoff record.

A passing local test verifies the behavior it exercises; live-account completion also depends on the model, website, and account state.

## Automated checks

From the repository root, after following the quick start in [README](../README.md):

```bash
uv run ruff check .
uv run pytest -q
```

The browser tests start temporary local pages and use the pinned Playwright CLI with headless Chrome. They need Node.js, Chrome, and access to download the CLI on first use. Model and LangSmith calls in the suite are mocked; the tests do not require an OpenAI key or spend model credits.

Coverage includes:

- Browser navigation, filling, clicking, screenshots, generated artifacts, and CLI errors.
- Native function schemas, malformed responses, and compaction history.
- Approval, decline, classifier failure, and dispatching a command once.
- Provider retries, retry exhaustion, token limits, and budget accounting.
- Terminal input, cancellation, compact output, and diagnostic privacy.

For a focused check, run a file or test by name:

```bash
uv run pytest -q tests/test_agent.py
uv run pytest -q tests/test_browser_cli.py
```

## Current results

Checked on macOS on 2026-09-10 against the working tree:

| Check | Result |
| --- | --- |
| `uv sync --frozen --dry-run` | Pass; the locked environment needs no changes. |
| `uv run browser-agent --help` | Pass. |
| `npx -y @playwright/cli@0.1.19 --version` | Pass; reports `0.1.19`. |
| `uv run ruff check .` | Pass. |
| `.venv/bin/pytest -q` | 156 passed in 27.24 seconds, including the provider-stream shutdown regression tests. |
| Wheel build and packaged browser skill | Pass; the wheel includes the current runtime and skill, with no retired browser modules or local artifacts. |
| Live Yandex Mail, YandexEda, and hh.ru tasks | Not verified on the current implementation. |

The Russian assignment, all three task descriptions, evaluation criteria, and reference images are preserved locally. External links identify the original sources.

### Live Luna check on a local page

Run `b0599222-299a-4318-896d-d61712202aa0` used the real Luna actor and reviewer with headless Chrome on a temporary local page. The actor received the URL and task, without selectors or element references. Independent page state confirmed all seven checks: a Unicode message opened, address text was filled, a country was selected, a draft was sent after approval, deletion was declined and did not occur, the submitted subject/body matched, and a code drawn in a graphic was read correctly.

The actor chose one screenshot across 23 decisions. It recovered from one stale element reference by requesting a fresh snapshot. The run took 129 seconds; conservative budget accounting was $0.034283 and reported-usage cost was approximately $0.014604. Evidence is in `artifacts/cli-migration/live-result.json` and `artifacts/cli-migration/live/runs/<run_id>/events.jsonl` (ignored by Git). This check used no live account.

The successful run also emitted an asynchronous provider-stream cleanup warning during process shutdown. A later local HTTP reproduction using the installed OpenAI SDK deterministically reproduced the same `generator didn't stop after athrow()` traceback: closing the outer SDK iterators left 11 nested SSE/HTTP generators open per response. [Per-response iterator ownership](../src/browser_agent/provider_stream.py) now closes those generators in the request task. [The regression tests](../tests/test_stream_cleanup.py) cover ten completion, error, disconnect, and cancellation scenarios, repeated requests, and successful reuse afterward; every tracked generator closes before loop shutdown. The standalone reproduction now exits cleanly with zero pending generators. These local checks make no paid API calls and do not establish the cause of earlier reported hangs.

## Acceptance status

The detailed checks live in [Acceptance tests](acceptance-tests.md). The three source tasks remain unverified on the current implementation:

| Task | Required evidence still missing |
| --- | --- |
| Yandex Mail | Ten latest messages read, only approved spam removed, and a report that matches the mailbox. |
| Food order | Restaurant resolved from last week's history, correct items and checkout details, and a stop before payment or order placement. |
| hh.ru | Resume read, three relevant roles, grounded letters, approvals, and three verified applications. |

Browser attachment and other operating systems need their own verification. Approval binds the pending command but does not lock the website; changes while approval is pending remain a known limitation. After cancellation or an uncertain error, inspect the page before retrying an action.

The [assignment](assignment.md) also requires a short complex-task video with the terminal and browser visible. The user will record it separately. Automated checks and the local Luna run do not replace this demonstration.

For each new acceptance run, record the task, run ID, observed outcome, approvals, blockers, and model cost. Compare the final report with independent browser state. Keep credentials and private account evidence out of the repository, and summarize the result here without marking unrun checks as passed.
