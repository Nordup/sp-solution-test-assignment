# Final test

Acceptance uses the real terminal command and a visible persistent browser. Automated checks cover the architecture; the three task scenarios below are completed and reviewed by a person in the signed-in services.

## 1. Install and focused checks

```bash
uv sync --frozen
uv run playwright install chromium
uv run ruff check .
uv run pytest -q
```

The focused suite covers native tool validation, bounded context, current Playwright references, persistent profiles, exact approval and denial, changed form values, retry exhaustion, stale-reference replanning and uncertain effects. Report skipped checks as pending.

## 2. Product smoke test

Start the real terminal agent with no subcommand or options:

```bash
uv run browser-agent
```

Confirm that visible Chromium opens before the first `Task:` prompt and that the browser uses the persistent default profile. Enter a short read-only task that does not supply a starting URL; the actor should choose a public homepage or search engine with `navigate`, inspect the result, and discover the next control from the page. Confirm that a second task runs in the same open browser, then type `/exit` and verify that the browser closes normally.

For a signed-in task, log in manually in the visible browser at any point. Confirm that the user remains in control of passwords, CAPTCHA and security checks, and that the saved cookies are available to the next task in the same profile. Review any approval prompt and approve only the exact displayed destination, target and values.

## 3. Assignment task scenarios

Run each scenario through the same `uv run browser-agent` session. Give the actor the task text and account access only; do not provide routes, selectors or click recipes. Stop and record the actual browser state when a login challenge, missing account data or other blocker prevents completion.

1. **Yandex Mail:** `Прочитай последние 10 писем в Яндекс.Почте и удали спам`.

   Pass when the browser shows that the ten latest message bodies were read, only messages judged spam were moved/deleted after exact approval, the report gives the actual count and names relevant retained messages, and no unrelated message changed.

2. **YandexEda:** `На YandexEda закажи мне BBQ-бургер и картошку фри из того места, откуда я заказывал на прошлой неделе. Остановись перед финальным подтверждением оплаты; заказ не размещай.`

   Pass when the restaurant is discovered from visible order history, the exact BBQ burger and fries are in the cart, checkout reaches payment review, the final payment action is not taken, and no order is placed.

3. **hh.ru:** `Найди 3 подходящие вакансии AI-инженера на hh.ru и откликнись на них с сопроводительным, предварительно изучив резюме в моём профиле`.

   Pass when the profile resume is read first, three relevant roles are selected, each letter is grounded in the resume and its role, and every submission receives exact user approval before sending.

Record the task text, observed outcome, approvals, blockers and cost. A concise final report is useful evidence only when it agrees with the visible browser state.

## 4. Failure behavior

Focused tests should demonstrate these cases:

- malformed native arguments are rejected before execution;
- a page changed after observation causes a fresh snapshot and replan;
- a form changed while approval is pending invalidates the old approval;
- a denied consequential action is not repeated through another tool;
- a transient provider failure retries with bounded backoff and then stops at the configured limit;
- an interrupted consequential action is not blindly replayed;
- login or CAPTCHA pauses for manual handling;
- long pages are read in bounded segments and continued without losing the task.

## 5. Review

Review the terminal transcript, visible browser state, private run result and documentation. Confirm that no credentials or account data are committed, that links match the implementation, and that the original Russian assignment, HR text and three JPG reference screenshots remain unchanged. Do not claim universal website compatibility or a live outcome that was blocked or left uncertain.
