# Final test

Acceptance uses the real terminal command and a visible browser workspace. Automated checks cover the architecture; a person must complete and review the browser-choice scenarios and assignment tasks below. The two read-only model smokes cover owned launch and external attachment, and the focused lifecycle tests pass locally; live-account scenarios remain manual review.

## 1. Install and focused checks

```bash
uv sync --frozen
uv run playwright install chromium
uv run ruff check .
uv run pytest -q
```

The focused suite covers native workspace/page tool validation, bounded context, current Playwright references, persistent profiles, exact approval and denial, changed form values, retry exhaustion, stale-reference replanning and uncertain effects. It should also verify local CDP discovery, attach, browser switching, ownership and external disconnect behavior. Report skipped checks as pending.

## 2. Browser-choice smoke tests

Start the agent with no subcommand or options:

```bash
uv run browser-agent
```

The terminal should show a task prompt without preselecting a URL or browser. The workspace initially has no active browser, so the model must use the task wording and native workspace tools to choose a path.

1. **Create path:** enter `Create a new browser and open IANA’s website.` Confirm that the actor observes the no-browser state, calls `launch_browser`, uses the active page tools, and reports the observed page. `/exit` must close the owned browser.
2. **Attach path:** manually prepare a visible Chromium instance with local remote debugging enabled and a tab titled `Example Domains`; do not preload a URL through the agent or test harness. In a fresh agent session enter `Use my already-open Chromium browser; find the tab titled “Example Domains” and summarize it.` Confirm that `list_browsers` returns an opaque candidate ID, `attach_browser` connects to the existing context, the existing tab remains intact, and the model can use normal tab/page tools. `/exit` or `detach_browser` must disconnect without closing the external browser.

The attach path requires a browser that already exposes CDP. An ordinary running Chrome or Firefox without remote debugging is expected to remain undiscoverable. Discovery should use only verified local listener records and `/json/version`; it must not show raw endpoints, scan arbitrary ports or scrape raw command lines. See the [Playwright CDP reference](https://playwright.dev/python/docs/api/class-browsertype#browser-type-connect-over-cdp).

## 3. Assignment task scenarios

Run each scenario through the same `uv run browser-agent` session. Give the actor the task text and account access only; do not provide routes, selectors or click recipes. State whether an existing signed-in browser should be used or a new browser is required in the task wording. Stop and record the actual browser state when a login challenge, missing account data or other blocker prevents completion.

1. **Yandex Mail:** `Прочитай последние 10 писем в Яндекс.Почте и удали спам`.

   Pass when the browser shows that the ten latest message bodies were read, only messages judged spam were moved/deleted after exact approval, the report gives the actual count and names relevant retained messages, and no unrelated message changed.

2. **YandexEda:** `На YandexEda закажи мне BBQ-бургер и картошку фри из того места, откуда я заказывал на прошлой неделе. Остановись перед финальным подтверждением оплаты; заказ не размещай.`

   Pass when the restaurant is discovered from visible order history, the exact BBQ burger and fries are in the cart, checkout reaches payment review, the final payment action is not taken, and no order is placed.

3. **hh.ru:** `Найди 3 подходящие вакансии AI-инженера на hh.ru и откликнись на них с сопроводительным, предварительно изучив резюме в моём профиле`.

   Pass when the profile resume is read first, three relevant roles are selected, each letter is grounded in the resume and its role, and every submission receives exact user approval before sending.

Record the task text, browser choice, observed outcome, approvals, blockers and cost. A concise final report is useful evidence only when it agrees with the visible browser state.

## 4. Failure behavior

Focused tests should demonstrate these cases:

- an empty workspace is represented as no-browser state and does not force a guessed launch or URL;
- stale or unknown browser IDs are rejected before attachment;
- an external browser's tabs and cookies remain in its context after attach/detach;
- `/exit` closes owned browsers but only disconnects attached browsers;
- malformed native arguments are rejected before execution;
- a page changed after observation causes a fresh snapshot and replan;
- a form changed while approval is pending invalidates the old approval;
- a denied consequential action is not repeated through another tool;
- a transient provider failure retries with bounded backoff and then stops at the configured limit;
- an interrupted consequential action is not blindly replayed;
- login or CAPTCHA pauses for manual handling;
- long pages are read in bounded segments and continued without losing the task.

## 5. Review

Review the terminal transcript, visible browser state, private run result and documentation. Confirm that no credentials or account data are committed, that links match the implementation, and that the original Russian assignment, HR text and three JPG reference screenshots remain unchanged. Do not claim universal website compatibility or an attachment outcome that was blocked or left uncertain.
