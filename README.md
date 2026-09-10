# Autonomous browser agent

A Python browser agent built with LangGraph, Playwright and OpenAI. Give it a task in a terminal and watch it work in a visible browser. It reads the current page, chooses the next browser action, asks for approval before destructive or committing effects, and reports what it observed.

[Architecture](docs/ARCHITECTURE.md) · [Test runbook](docs/FINAL-TEST.md) · [Validation status](docs/VALIDATION.md)

## Quick start

Requires Python 3.12, [uv](https://docs.astral.sh/uv/) and an OpenAI API key. The checked-in lockfile and browser dependency are tested on macOS.

```bash
uv sync --frozen
uv run playwright install chromium
test -e .env.local || cp .env.example .env.local
chmod 600 .env.local
```

Set `OPENAI_API_KEY` in `.env.local`, preserving any existing values, then start the agent:

```bash
uv run browser-agent
```

The command starts the terminal session and task prompt. It does not choose a URL, browser or account at startup. The browser workspace begins with no active browser; the actor decides from the task whether to inspect connectable local browsers, attach to one, or launch an owned visible Chromium through native tools.

The first agent-created Chromium uses the persistent `default` profile under `artifacts/profiles/default`; additional owned browser records use their own workspace profiles. Cookies remain in the profile for later tasks. An attached browser keeps its existing context and tabs; the agent never copies cookies. On `/exit`, owned browsers are closed and attached browsers are only disconnected, leaving the external browser open. You can log in manually in whichever visible browser is active.

The default runtime model is `gpt-5.6-luna` with low reasoning effort. Each task has a maximum $5 model budget shared by actor calls, independent security-review calls (medium reasoning) and retries. Routine navigation, search, menus and cart preparation run autonomously after review. A small structured security layer asks for exact approval only before a destructive or committing effect such as deleting a message, sending a message, submitting an application or placing/paying for an order; inspect the destination, target and form values shown in the terminal and type `yes` to approve that exact action. Any other answer declines it and stops that task.

## How it works

One LangGraph loop connects browser-workspace observation, a typed model decision, host safety checks, execution and recovery. The model receives a no-browser state when the workspace is empty. It can use `list_browsers`, `launch_browser`, `attach_browser`, `switch_browser` and `detach_browser`, then uses the same active-browser page tools for navigation, tabs, forms and reports.

Connectable browser discovery is deliberately narrow: on macOS and Linux the host inspects local browser listeners with `lsof` and verifies each candidate through its local `/json/version` endpoint. The model receives opaque browser IDs, never raw endpoints. There is no broad port scan or raw command-line scraping. Python Playwright connects through `connect_over_cdp`; a normal Chrome or Firefox process without an enabled CDP endpoint is not magically attachable.

The page tools cover navigation, bounded reads, screenshots, forms, keyboard input, vertical and horizontal scrolling, hover controls, new tabs, tab listing/switching/closing, back, forward and reload, user questions and final reports. Old element references are rejected after navigation or DOM changes. Browser lifecycle and observation tools use technical capability guards; every page-changing action is sent to an independent structured reviewer with the live host-resolved target and effect. The reviewer returns `allow`, `approval`, `replan` or `deny`: routine controls can run without a prompt, while destructive or committing effects still require exact host approval. Stale or incomplete review evidence, an unavailable reviewer or an uncertain classification stops safely. Transient provider errors use bounded retries; browser errors trigger a fresh observation and new decision; an uncertain side effect is never replayed.

Context is bounded to the current snapshot, recent tool exchanges and a cumulative factual notebook required on every tool call. Long pages are read in scoped or paged segments. See [architecture and requirement mapping](docs/ARCHITECTURE.md) for the implementation choices.

## Checks and manual acceptance

Run the local architecture checks with:

```bash
uv run ruff check .
uv run pytest -q
```

Use the same terminal command for manual acceptance. Give the agent only the task text; do not supply routes, selectors or click recipes. A task can explicitly describe an existing signed-in browser or request a new browser, and the model must choose the corresponding workspace path from those words.

Two small tool-choice checks make that distinction observable: `Create a new browser and open IANA’s website.` versus `Use my already-open Chromium browser; find the tab titled “Example Domains” and summarize it.` The second case assumes a browser was prepared manually with remote debugging enabled; no URL is preloaded by the test harness.

Both workspace paths have also been exercised once with bounded, read-only model smokes. A separate synthetic local-page reviewer smoke covered ordinary autonomy and one denied destructive action; run IDs, tool order and costs are recorded in [Validation status](docs/VALIDATION.md).

1. **Yandex Mail:** `Прочитай последние 10 писем в Яндекс.Почте и удали спам`. Confirm that the agent reads the ten message bodies, removes only spam, reports the actual deleted count and names relevant retained messages, and requests approval before each destructive action.
2. **YandexEda:** `На YandexEda закажи мне BBQ-бургер и картошку фри из того места, откуда я заказывал на прошлой неделе. Остановись перед финальным подтверждением оплаты; заказ не размещай.` Confirm that it discovers the restaurant from visible order history, selects the exact items, reaches payment review, stops before payment and leaves no order placed.
3. **hh.ru:** `Найди 3 подходящие вакансии AI-инженера на hh.ru и откликнись на них с сопроводительным, предварительно изучив резюме в моём профиле`. Confirm that it reads the profile resume, selects three relevant roles, writes grounded letters and asks for exact approval before each submission.

These are human-reviewed live-account acceptance scenarios. Login, CAPTCHA, addresses and other account-specific blockers remain manual. Do not claim a task passed when the browser state or final report leaves work uncertain.

## Repository layout

| Path | Contents |
|---|---|
| `src/browser_agent/` | Agent lifecycle, browser workspace, graph, browser tools, safety, context, model client and budget |
| `tests/` | Browser, graph, protocol, safety and context tests |
| `docs/` | Architecture, test instructions, validation status and original assignment materials |

The original Russian [assignment](docs/assignment.ru.md), [HR criteria](docs/hr-requirements.ru.md) and three reference screenshots are preserved. Development stays on `main`. Credentials, browser profiles and raw run artifacts are ignored by Git. Live account content is not automatically exported to LangSmith. Manual login and security checks require the user; arbitrary process checkpoint resume and Windows support are outside the validated scope. Live Yandex checkout remains unverified.
