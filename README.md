# Autonomous browser agent

A terminal agent that completes tasks in a visible browser. It chooses where to navigate, what to inspect, and which actions to take. An independent reviewer checks actions that may need human approval.

Built with Python, LangGraph, OpenAI Responses, and the official Playwright CLI. The runtime contains no workflows or selectors for specific websites.

## Quick start

You need Python 3.12, [uv](https://docs.astral.sh/uv/), Node.js 18 or later with npm, Google Chrome, and an OpenAI API key with access to `gpt-5.6-luna`. Development and the current checks use macOS.

From the repository root:

```bash
uv sync --frozen
test -e .env.local || cp .env.example .env.local
chmod 600 .env.local
npx -y @playwright/cli@0.1.19 --version
```

Set `OPENAI_API_KEY` in `.env.local`, then start the agent:

```bash
uv run browser-agent
```

At `Task:`, describe what you want. For a first check:

> Open a new browser, visit IANA's website, and explain what the organization does.

The agent chooses whether to open a browser or attach to an existing one. Keep the terminal beside the browser. Log in manually if asked; the default browser profile preserves login state between sessions. Close one session before starting another with the same profile.

Attachment requires a Playwright session, a browser with remote debugging enabled, or the Playwright extension. A browser being open does not by itself make it attachable.

## During a task

The terminal shows commands, questions, and the final result. Page snapshots and detailed diagnostics stay in local files.

| Input | Effect |
| --- | --- |
| `y` or `yes` at an approval prompt | Execute the pending action once. |
| Enter, `n`, or `no` at an approval prompt | Skip that action and continue. |
| Esc twice within half a second | Stop the current task and return to `Task:`. |
| `/exit` at `Task:`, or Ctrl+C | Exit the session. |

The double-Esc shortcut requires an interactive terminal. Stopping a task cannot undo an action already sent to the browser. On exit, the application closes its owned browser or detaches from an external browser.

## Configuration and results

[.env.example](.env.example) lists the configuration options. Existing environment variables take precedence over `.env.local`. Restart the agent after changing configuration. For a separate test profile, set `AGENT_BROWSER_PROFILE_DIR=artifacts/profiles/acceptance`.

The actor uses Luna at max reasoning; the reviewer uses Luna at medium reasoning. Each task has a shared model budget of at most $5. You can lower it with `AGENT_BUDGET_USD`. The current price tables support Luna only.

Local output is saved under `artifacts/`:

- `runs/<run_id>/result.json`: outcome, remaining work, token usage, and cost.
- `runs/<run_id>/events.jsonl`: task events and diagnostics.
- `browser/<session>/`: Playwright output and generated snapshots.
- `profiles/default/`: persistent browser login state.

LangSmith tracing is optional. Enable it in `.env.local` to export metrics and status. Browser content is sent to OpenAI as needed for the task; LangSmith receives no task text, page content, tool arguments, or user answers. See the data handling section in [Architecture](docs/architecture.md).

## Review and verification

```bash
uv run ruff check .
uv run pytest -q
```

The [testing guide](docs/testing.md) records current results and known gaps. Before submission, follow the [final acceptance runbook](docs/acceptance-tests.md): setup, automated checks, browser behavior, the three assignment tasks, failure cases, and the final repository/video review. Local tests do not establish that the live-account workflows are complete.

## Assignment and documentation

The [preserved assignment](docs/assignment.md) and [HR evaluation criteria](docs/evaluation-criteria.md) are the source of truth for requirements and implementation decisions. They contain the Russian source text, all three nested task descriptions, and local reference screenshots. Reading them does not require the original website. [AGENTS.md](AGENTS.md) gives coding agents the required reading order.

- [Architecture](docs/architecture.md): agent loop, browser tools, approvals, context, and failure handling.
- [Testing](docs/testing.md): automated coverage and recorded verification results.
- [Acceptance tests](docs/acceptance-tests.md): ordered steps, expected outcomes, and evidence to collect before handoff.
