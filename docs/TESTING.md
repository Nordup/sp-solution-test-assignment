# Testing

Run the automated checks first, then evaluate the agent through the terminal and a visible browser. A passing local test verifies the behavior it exercises; live-account completion also depends on the model, website, and account state.

## Automated checks

From the repository root, after following the [setup instructions](../README.md#quick-start):

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
| `uv run pytest -q` | 145 passed in 28.25 seconds. |
| Wheel build and packaged browser skill | Pass; the wheel includes the current runtime and skill, with no retired browser modules or local artifacts. |
| Live Yandex Mail, YandexEda, and hh.ru tasks | Not verified on the current implementation. |

Local document links and headings resolve. The original Russian assignment, evaluation criteria, and three reference images are unchanged.

## Manual session check

Start `uv run browser-agent` in a real terminal beside the browser.

1. Enter `Open a new browser and visit IANA's website.` Confirm that the agent chooses the browser command and destination itself.
2. Ask a follow-up task. Confirm that it can use the existing browser and that manual login state survives a later session.
3. Exercise an action that requires approval on disposable data. Confirm that approval executes once, decline skips the action, and the final report matches the browser.
4. Press Esc twice during a model call and during a human prompt. Confirm that the task stops, the browser remains available, and another task can start.
5. Exit the session. Confirm that an owned browser closes. Test attachment separately using a prepared Playwright session, CDP endpoint, or extension; confirm that exit leaves the external browser running.

Check output at both normal and narrow terminal widths. User input should be clear, commands readable, and the final answer concise. Screenshots should be chosen by the actor and visible to the model. Private snapshots and diagnostic payloads should stay out of terminal output.

Approval does not lock the website. Leave the page unchanged while deciding whether to approve. After cancellation or an uncertain error, inspect the page before retrying an action.

## Assignment tasks

Use an account you can inspect and operate manually. Give the agent only the task and any necessary account information. Let it discover pages and controls. Login and security challenges may require human help.

### Yandex Mail

> Прочитай последние 10 писем в Яндекс.Почте и удали спам.

Pass when the ten latest message bodies were read, only spam was removed after approval, and the report identifies the actual affected messages and relevant retained mail.

### YandexEda

> На YandexEda закажи мне BBQ-бургер и картошку фри из того места, откуда я заказывал на прошлой неделе. Остановись перед финальным подтверждением оплаты; заказ не размещай.

Pass when order history identifies the restaurant, the correct items are selected, and payment review is reached without placing an order. If the account lacks the requested history or delivery details, record the blocker rather than treating a substitute as completion.

### hh.ru

> Найди 3 подходящие вакансии AI-инженера на hh.ru и откликнись на них с сопроводительным, предварительно изучив резюме в моём профиле.

Pass when the profile resume is read first, three relevant roles are selected, grounded letters are prepared, and each submission is approved before dispatch. The report must distinguish submitted applications from drafts and skipped actions.

## Review evidence

Record the task, observed outcome, approvals, blockers, and model cost. Compare the final report with browser state. Run artifacts are available under `artifacts/runs/<run_id>/`; keep credentials and private account content out of the repository.

The [assignment](assignment.ru.md#результат) requests a short video of one complex task with the terminal and browser visible. Current local checks do not replace that demonstration. Browser attachment, other operating systems, and live-account workflows need their own verification.
