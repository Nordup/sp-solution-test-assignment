# Validation

## Current status

The current revision adds a model-driven browser workspace: it starts empty, discovers verified local CDP browsers by opaque IDs, and lets the actor choose launch, attach, switch or detach from the task wording. Two bounded read-only model smokes passed those launch and attachment paths. Focused automated lifecycle tests pass; live-account scenarios remain pending.

The 52-test result and IANA smoke below are historical baselines from before workspace attachment. They do not prove the new discovery, ownership or external disconnect behavior.

| Check | Evidence |
|---|---|
| Browser workspace model smokes | **Passed:** new-browser run `7b2c5bdd-b525-4003-9dad-d26a36519b99`, 3 decisions, $0.002718, `launch_browser → tabs → finish`; it created owned browser `b-cb1a17b11b9744` with one active blank `about:blank` tab whose title was empty. Attachment run `945d1f3c-0dd0-440c-8ade-8c1d814d20d2`, 3 decisions, $0.002873, `list_browsers → attach_browser → finish`; it attached a temporary test-owned CDP tab titled `Attachment smoke`, reported heading `Attachment smoke unique proof`, proposed no `launch_browser`, and the external browser stayed connected with its tab and heading intact after workspace close. Private evidence: `artifacts/workspace-smoke/20260910T035211Z/evidence.json`. |
| Focused tests | **PASS — current tree:** `uv run pytest -q`, 56 passed in 22.02s. The targeted workspace file also reports 4 passed in 4.12s, including owned/attached switching and external detach liveness. |
| Lint and setup | **PASS — current tree:** Ruff checks, whitespace checks and locked setup pass; the CLI help surface exposes only `--help`. |
| Blank-start browser smoke | **Historical only:** run `6886c259-494e-4470-b11c-fdfd12d337a7`, model `gpt-5.6-luna` at low reasoning, $0.007165 and 5 decisions. The old `BrowserSession.start()` received no URL from `about:blank`, chose `https://www.iana.org/help/example-domains`, opened and closed an extra tab, and finished with one tab. It did not exercise browser discovery or CDP attachment. Private evidence remains in `artifacts/general-smoke/evidence.json`. |
| Manual assignment tasks | **Pending.** Run the Yandex Mail, YandexEda and hh.ru scenarios through the real CLI and review browser choice, state, approvals and final reports. |

The two workspace smokes were read-only and required no account, approval or challenge. The IANA run remains a historical generic-navigation baseline; it does not exercise attachment.

## Acceptance coverage

The current checks cover these assignment requirements:

- the task determines whether the actor should discover and attach an existing browser or launch an owned one;
- an empty `BrowserWorkspace` is visible to the model as a no-browser state, without a deterministic initial browser, URL or menu;
- `list_browsers` exposes opaque IDs only for local listeners found with `lsof` and verified through `/json/version`;
- Python Playwright `connect_over_cdp` attaches existing Chromium contexts while preserving tabs and cookies without copying them;
- owned browsers close on `/exit`, while attached browsers are only disconnected and remain open;
- active-browser page tools provide navigation, tabs, history, hover, vertical and horizontal scroll, forms, reads and reports;
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

The three successful historical task families read ten mail bodies and removed exactly three spam messages, reached food payment review at 315,000 VND without payment, and selected three jobs with individually reviewed letters. Their combined model cost was $0.105330. These results demonstrate controlled historical behavior; they do not certify live-site compatibility or the current workspace lifecycle.

## Limits and prior fixes

An external browser must already expose a local remote-debugging endpoint. An ordinary running Chrome or Firefox cannot be attached just because it is open, and the agent does not copy cookies or manufacture a CDP endpoint. Discovery is limited to macOS and Linux `lsof` listener records verified through `/json/version`; it does not scan arbitrary ports or scrape raw process command lines. ChromeMCP extension/channel support is not implemented.

No real orders, payments, emails or job applications were submitted during the archived synthetic runs. The prepared Yandex session previously needed a genuine delivery address; useful last-week order history and a complete live food task remain unverified. Login and security challenges require manual handling, and Windows support is not certified.

Earlier failures showed that too little recent history and an optional notebook caused repeated work. Requiring the factual notebook on every tool call fixed that issue. An earlier food attempt stopped one page before payment review; the task prompt and finish description were clarified before the archived run. Those fixes do not guarantee every live model run.
