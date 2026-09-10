# Autonomous browser agent

A Python browser agent built with **LangGraph, Playwright and OpenAI**. Give it a task in a terminal and watch it navigate a visible browser, read pages, fill forms and ask for confirmation before consequential actions.

[Watch the demo](docs/assets/synthetic-agent-demo.mp4) · [Architecture](docs/ARCHITECTURE.md) · [Test runbook](docs/FINAL-TEST.md) · [Validation results](docs/VALIDATION.md)

## Quick start

Requires Python 3.12, [uv](https://docs.astral.sh/uv/) and an OpenAI API key. Tested on macOS.

```bash
uv sync --frozen
uv run playwright install chromium
test -e .env.local || cp .env.example .env.local
chmod 600 .env.local
```

Set `OPENAI_API_KEY` in `.env.local`. Preserve an existing configured file. The configured model is `gpt-5.6-luna`; each task has a maximum $5 model budget, including retries.

```bash
uv run browser-agent doctor
uv run browser-agent run --url https://example.com 'Read this page and summarize what it offers.'
```

Omit the task argument to enter it interactively. The terminal shows tools, their arguments, observed results and the final report.

### Signed-in services

```bash
uv run browser-agent login --url https://your-service.example --profile personal
uv run browser-agent run --profile personal --url https://your-service.example 'Your task'
```

Log in manually in the opened browser, then press Enter to save the profile. Only one process can use a profile at a time. For action confirmations, inspect the displayed destination, target and form values, then type `yes` to approve that exact action. Any other answer declines it and stops the task.

## Design

One LangGraph loop connects observation, a typed model decision, host approval, execution and recovery. The model selects elements from current accessibility snapshots. It receives no site-specific selectors, navigation recipes or expected task answers.

Context consists of a bounded current snapshot, ten recent tool exchanges and a cumulative factual notebook required in each tool call. Scoped reads handle long pages. Transient provider errors use bounded backoff; stale elements trigger a fresh observation and replanning. An uncertain consequential action stops without automatic replay.

See [architecture and requirement mapping](docs/ARCHITECTURE.md) for tools, limits and technical choices.

## Tests and evaluations

```bash
uv run ruff check .
uv run pytest -q
uv run python -m evals.run --headed --langsmith
```

Tests exercise the actual LangGraph and Chromium with mocked model responses. The evaluation command uses the real model and three isolated synthetic apps: mail cleanup, food checkout and resume-based job applications. Set `LANGSMITH_API_KEY` for `--langsmith`, or omit the flag to keep results local. Each case is capped at $5; the full evaluation has a maximum of $15.

All three task families have passed their state checks. The successful three-case run cost approximately $0.11. [Validation](docs/VALIDATION.md) includes the evidence, earlier failures and limitations; these results do not establish universal live-site compatibility.

### Reproduce the video

```bash
uv run python scripts/demo_terminal.py --fixture food_previous_order --seed 102
```

Enter the task below. The launcher opens an isolated synthetic browser and pauses so you can arrange it beside Terminal. Press Enter to begin, and review each approval yourself.

> Тестовый сценарий: 9 сентября 2026. Закажи мне BBQ-бургер и картошку фри из того места, откуда я заказывал на прошлой неделе. Остановись перед финальным подтверждением оплаты; заказ не размещай.

The [2-minute-20-second recording](docs/assets/synthetic-agent-demo.mp4) shows the real actor reaching final payment review without placing an order. It is a continuous capture at normal speed, with excess idle footage after the final report removed.

## Repository layout

| Path | Contents |
|---|---|
| `src/browser_agent/` | Agent lifecycle, graph, browser tools, safety, context, model client and budget |
| `evals/` | Synthetic applications, independent state checks and LangSmith export |
| `tests/` | Browser, graph, protocol, safety and evaluator tests |
| `scripts/demo_terminal.py` | Interactive synthetic demo using the same agent |
| `docs/` | Architecture, test instructions, results and original assignment materials |

The original Russian [assignment](docs/assignment.ru.md), [HR criteria](docs/hr-requirements.ru.md) and three reference screenshots are preserved. Development for this assignment stays on `main`.

Credentials, browser profiles and raw run artifacts are ignored by Git. Live account content is not automatically exported to LangSmith. Manual login/security checks are supported; arbitrary process resume and Windows support are outside the validated scope. Live Yandex checkout remains unverified.
