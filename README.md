# Autonomous browser agent

A Python agent that uses **LangGraph + Playwright** to carry out natural-language tasks in a visible browser. A real terminal shows the task, tool calls, approvals and final report. The acting model is **GPT-5.6 Luna** through the OpenAI API.

**Status:** the simplified LangGraph implementation passes 39 focused tests and lint. The three model-driven task evaluations and demo remain pending. See [validation](docs/VALIDATION.md) for actual results.

## Run

```bash
uv sync --frozen
uv run playwright install chromium
cp .env.example .env.local  # new checkout only; preserve an existing configured file
chmod 600 .env.local
# Fill in OPENAI_API_KEY locally.
uv run browser-agent doctor
uv run browser-agent run --url https://example.com 'Read this page and summarize what it offers.'
```

Python 3.12 is required. On the prepared machine, credentials already exist in the ignored `.env.local`; reuse them.

For a signed-in service:

```bash
uv run browser-agent login --url https://your-service.example --profile demo
uv run browser-agent run --profile demo --url https://your-service.example 'Your task'
```

Log in manually in the opened browser, then press Enter in Terminal to save and close the profile. One process can use a profile at a time. Keep Terminal beside the controlled browser, as in the [original reference screenshots](docs/assignment.ru.md).

## How it works

LangGraph connects observation, one model decision, host safety checks, execution and recovery. Tools reference elements discovered from the current accessibility snapshot; there are no site-specific selectors or task scripts. OpenAI native tool calls are validated by Pydantic.

The actor sees a bounded current snapshot, recent tool results and a small notebook. It can read long content in segments. Stale elements trigger a fresh observation and replanning; provider errors have bounded retry/backoff. An uncertain consequential action stops for inspection instead of being repeated.

Critical actions show the actual destination, target and form values. Type `yes` to approve that exact action; a denial ends the task. The browser checks that the target has not changed before executing. Unknown JavaScript buttons are conservatively confirmed. This is a practical safety gate, not a guarantee about arbitrary website code.

Each task has a maximum **$5 model budget**, including retries. The application checks a conservative estimate before a request and reconciles actual usage. Browser profiles persist; arbitrary program checkpoint recovery is outside this take-home scope.

## Verify

```bash
uv run ruff check .
uv run pytest -q
uv run python -m evals.run --headed --langsmith
```

The evaluation runner uses isolated synthetic versions of the three supplied task families: reading mail/removing spam, history-based food checkout, and resume-based job applications. LangSmith records synthetic inputs, outputs and scores. Fixture expectations are not available to the actor. The current command interface has been checked; task outcomes remain pending until the new evaluations run. Old-version results are not new passes.

Use [FINAL-TEST.md](docs/FINAL-TEST.md) for the focused acceptance checks and [SYSTEM-DESIGN.md](docs/SYSTEM-DESIGN.md) for requirement mapping. The exact Russian [assignment](docs/assignment.ru.md) and [HR criteria](docs/hr-requirements.ru.md) are preserved.

## Demo and limitations

The final video must show the real terminal and browser together. It is still pending. A synthetic demonstration will be labeled explicitly. The prepared Yandex session previously reached a request for a genuine delivery address; a complete live food task and useful last-week history remain unverified.

Credentials, profiles, account evidence and raw run artifacts are ignored by Git. Live account content is not automatically exported to LangSmith. Do not submit payments, delete real mail or send real applications merely to produce a demonstration.
