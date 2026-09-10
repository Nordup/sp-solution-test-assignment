# Autonomous browser agent

A Python browser agent built with LangGraph, Playwright and OpenAI. Give it a task in a terminal and watch it work in a visible Chromium browser. It reads the current page, chooses the next browser action, asks for approval before consequential actions, and reports what it observed.

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

The command opens visible Chromium before showing the first `Task:` prompt. It uses one persistent `default` profile under `artifacts/profiles/default`, so cookies and other browser session data remain available across tasks. Enter tasks at the prompt, or type `/exit` to close the browser and end the session. The same browser stays open while several tasks run.

There is no startup URL or task-specific command. The actor can start from the current page, open a public homepage or search engine with `navigate`, and discover the required site routes and controls from the observed page. You can log in manually in the visible browser whenever a task needs it; credentials stay outside the model tools and the saved cookies are available to later tasks.

The default runtime model is `gpt-5.6-luna` with low reasoning effort. Each task has a maximum $5 model budget, including retries. For a consequential action, inspect the destination, target and form values shown in the terminal and type `yes` to approve that exact action. Any other answer declines it and stops that task.

## How it works

One LangGraph loop connects observation, a typed model decision, host safety checks, execution and recovery. The model chooses from the current accessibility snapshot; it receives no site-specific selectors, navigation recipes or expected task answers.

The native browser tools cover navigation, bounded reads, screenshots, forms, keyboard input, vertical and horizontal scrolling, hover controls, new tabs, tab listing/switching/closing, back, forward and reload, user questions and final reports. Old element references are rejected after navigation or DOM changes. Transient provider errors use bounded retries; stale elements trigger a fresh observation and replanning; an uncertain consequential action stops for inspection instead of being replayed.

Context is bounded to the current snapshot, recent tool exchanges and a cumulative factual notebook required on every tool call. Long pages are read in scoped or paged segments. See [architecture and requirement mapping](docs/ARCHITECTURE.md) for the implementation choices.

## Checks and manual acceptance

Run the local architecture checks with:

```bash
uv run ruff check .
uv run pytest -q
```

Use the same terminal command for manual acceptance. Give the agent only the task text; do not supply routes, selectors or click recipes. The visible browser and terminal should make each decision, approval and final state reviewable.

1. **Yandex Mail:** `Прочитай последние 10 писем в Яндекс.Почте и удали спам`. Confirm that the agent reads the ten message bodies, removes only spam, reports the actual deleted count and names relevant retained messages, and requests approval before each destructive action.
2. **YandexEda:** `На YandexEda закажи мне BBQ-бургер и картошку фри из того места, откуда я заказывал на прошлой неделе. Остановись перед финальным подтверждением оплаты; заказ не размещай.` Confirm that it discovers the restaurant from visible order history, selects the exact items, reaches payment review, stops before payment and leaves no order placed.
3. **hh.ru:** `Найди 3 подходящие вакансии AI-инженера на hh.ru и откликнись на них с сопроводительным, предварительно изучив резюме в моём профиле`. Confirm that it reads the profile resume, selects three relevant roles, writes grounded letters and asks for exact approval before each submission.

These are human-reviewed live-account acceptance scenarios. Login, CAPTCHA, addresses and other account-specific blockers remain manual. Do not claim a task passed when the browser state or final report leaves work uncertain.

## Repository layout

| Path | Contents |
|---|---|
| `src/browser_agent/` | Agent lifecycle, graph, browser tools, safety, context, model client and budget |
| `tests/` | Browser, graph, protocol, safety and context tests |
| `docs/` | Architecture, test instructions, validation status and original assignment materials |

The original Russian [assignment](docs/assignment.ru.md), [HR criteria](docs/hr-requirements.ru.md) and three reference screenshots are preserved. Development stays on `main`. Credentials, browser profiles and raw run artifacts are ignored by Git. Live account content is not automatically exported to LangSmith. Manual login and security checks require the user; arbitrary process checkpoint resume and Windows support are outside the validated scope. Live Yandex checkout remains unverified.
