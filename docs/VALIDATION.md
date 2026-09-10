# Validation

## Current status

The current revision changes the runtime lifecycle: the bare CLI opens visible Chromium before the first task, keeps one persistent default profile for multiple tasks, starts without a supplied URL, and adds generic tab/history/hover/scroll controls. Focused checks pass after those changes and the retirement of the prepared-fixture harness; manual account-task acceptance remains pending.

No live-account task has been run. One bounded, read-only real-model smoke was run from a fresh blank page to exercise generic navigation and tabs:

| Check | Evidence |
|---|---|
| Blank-start browser smoke | **PASS** — run `6886c259-494e-4470-b11c-fdfd12d337a7`, model `gpt-5.6-luna` at low reasoning, $0.007165 and 5 decisions. `BrowserSession.start()` received no URL; the initial page was `about:blank` with one tab. The actor chose `https://www.iana.org/help/example-domains`, then used `new_tab`, navigated and verified the documentation, closed the extra tab, and finished with the original tab active and one tab open. Private evidence is in `artifacts/general-smoke/evidence.json` (ignored by Git). |
| Focused tests | **PASS** — current tree: `uv run pytest -q`, 52 passed in 21.99s. |
| Lint and setup | **PASS** — current Ruff checks pass; the CLI help surface exposes only `--help`. Locked setup and Playwright installation were already available in the current environment. |
| Manual assignment tasks | **Pending.** Run the Yandex Mail, YandexEda and hh.ru scenarios through the real CLI and review browser state, approvals and final reports. |

The smoke was read-only and required no account, approval or challenge. Its event log records the first chosen URL, each tool proposal/result, tab IDs and final tab state. It does not replace the three manual acceptance scenarios.

## Acceptance coverage

Focused tests and the manual runbook cover the assignment requirements:

- generic navigation starts without a supplied URL and chooses destinations from observed pages;
- the visible persistent profile accepts manual login and multiple tasks;
- typed tools provide navigation, tabs, history, hover, vertical and horizontal scroll, forms, reads and reports;
- bounded snapshots, paged reads, recent exchanges and the cumulative factual notebook constrain model context;
- exact target/form approvals guard consequential actions and changed targets invalidate old approvals;
- stale references, provider retries, retry exhaustion, login challenges and uncertain effects have explicit recovery behavior;
- native Pydantic schemas enforce tool arguments without parsing model prose as JSON;
- the $5 per-task budget remains enforced, including retries.

The Yandex Mail, YandexEda and hh.ru scenarios are task-only acceptance cases. They intentionally do not provide routes, selectors or workflow recipes. Account-specific login, address, CAPTCHA and payment blockers remain manual and must be reported honestly.

## Archived historical evidence

The following eight synthetic fixture runs were recorded before the fixture package and demo helper were removed. They remain an accounting record only; no prepared-site end-to-end test is part of the current product or runbook. Costs and outcomes are preserved exactly for traceability.

| Run | Case | State checks | Letter review | USD | LangSmith |
| --- | --- | --- | --- | ---: | --- |
| `8d7144a3-1f58-4657-aa5c-04219ea06cf8` | mail_latest_10 | FAIL | not_applicable | 0.059082 | exported |
| `b4e5595c-a71d-4ca6-a50c-05b84ce1b99e` | food_previous_order | FAIL | not_applicable | 0.005724 | exported |
| `6108fbd3-5b59-4796-b3c3-b03321028afd` | jobs_resume_3 | FAIL | manual_review_required | 0.054694 | exported |
| `d9fcd2bb-3647-4b2f-9543-a034df2dff2d` | mail_latest_10 | PASS | not_applicable | 0.103816 | exported |
| `be384303-67f7-44f4-9282-a797cdffcdce` | mail_latest_10 | PASS | not_applicable | 0.059175 | exported |
| `4fb9e14a-11d1-4deb-85c3-34aec921fd9d` | food_previous_order | PASS | not_applicable | 0.015157 | exported |
| `a27a7384-1926-4d53-b930-fbd72ddd7a57` | jobs_resume_3 | PASS | manually_reviewed | 0.030998 | exported |
| `767c4ac5-1088-406a-9ebc-14a151ab30fc` | mail_latest_10 | PASS | not_applicable | 0.042904 | exported |

The preceding three successful task families read ten mail bodies and removed exactly three spam messages, reached food payment review at 315,000 VND without payment, and selected three jobs with individually reviewed letters. Their combined model cost was $0.105330. These results demonstrate controlled historical behavior; they do not certify live-site compatibility or the current lifecycle.

## Limits and prior fixes

No real orders, payments, emails or job applications were submitted during the archived synthetic runs. The prepared Yandex session previously needed a genuine delivery address; useful last-week order history and a complete live food task remain unverified. Login and security challenges require manual handling, and Windows support is not certified.

Earlier failures showed that too little recent history and an optional notebook caused repeated work. Requiring the factual notebook on every tool call fixed that issue. A prior food recording also stopped one page before payment review; the task prompt and finish description were clarified before the archived run. Those fixes do not guarantee every live model run.
