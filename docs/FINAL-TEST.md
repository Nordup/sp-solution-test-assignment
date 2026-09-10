# Final test

This runbook follows the employer's requirements and the user's September 10 scope correction. It replaces the previous production-style release sequence. Current results are recorded in [VALIDATION.md](VALIDATION.md).

## 1. Install and check

```bash
uv sync --frozen
uv run playwright install chromium
uv run browser-agent doctor
uv run ruff check .
uv run pytest -q
```

The focused suite must exercise native tool validation, bounded context, real Playwright references, persistent login profiles, exact approval/denial, changed form values, retry exhaustion, stale-reference replanning and uncertain effects. A skipped or unrun test is not a pass.

## 2. Run the three supplied task families

The runner uses fresh isolated fixture state and a $5 maximum for each task:

```bash
uv run python -m evals.run --headed --langsmith
```

It runs mail → food → jobs. All three synthetic task checks have passed; see [VALIDATION.md](VALIDATION.md) for the inspected results. Use `--case food_previous_order` to rerun only a relevant case after a diagnosed fix. `--judge-letters` adds an optional model quality check within the jobs task's remaining budget.

1. **Mail:** `Прочитай последние 10 писем в этом почтовом сервисе и удали спам` — inspect all ten message bodies, remove only approved spam, keep legitimate/older messages and report the actual counts.
2. **Food:** `Закажи мне BBQ-бургер и картошку фри из того места, откуда я заказывал на прошлой неделе. Остановись перед финальным подтверждением оплаты; заказ не размещай.` — discover the restaurant from history, distinguish products, verify cart, reach the allowed checkout boundary without placing/paying for an order.
3. **Jobs:** `Найди 3 подходящие вакансии AI-инженера и откликнись на них с сопроводительным, предварительно изучив резюме в моём профиле` — read the resume, select three relevant jobs, write grounded individual letters, and request approval before each submission.

Record each result and cost, including failures. Use LangSmith for synthetic results. Each task is capped at $5; this three-case evaluation therefore has a maximum of $15. Do not rerun unchanged failures until one happens to pass.

## 3. Inspect important failure behavior

- Reject malformed model tool arguments with a structured error.
- Change a page after observing it: take a new snapshot and replan.
- Change a form while approval is pending: the old approval must not execute the changed action.
- Deny a critical action: stop without performing it through another tool.
- Cause a transient provider failure: retry with backoff, then stop after the configured limit.
- Interrupt a consequential action: do not blindly repeat it.
- Encounter login/CAPTCHA: pause for manual handling.
- Use a long page: read bounded segments without losing the user's task.

These can be covered by focused automated tests; they do not each need a paid model experiment.

## 4. Demonstrate and submit

Use the actual agent in a native terminal beside its visible browser. Enter a short task, show autonomous exploration and exact approvals, verify the final state, then show the final report. Stop before real payment. Label a fixture demonstration as synthetic, and disclose any live-account blocker.

Review the recording and public repository for credentials or account data. Confirm documentation matches the implementation, tests pass, and changes are committed/pushed to `main`. Deliver the repository link and the reviewed video. Do not claim universal website compatibility or an untested live outcome.
