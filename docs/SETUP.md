# Local setup and implementation readiness

Prepared 2026-09-09. **Credentials, dependencies and runtime are installed on this machine.** Acceptance tests and evaluation tooling are implemented; the final release suite remains incomplete. See [VALIDATION.md](VALIDATION.md) and [REQUIREMENTS.md](REQUIREMENTS.md) for current implementation/evidence status. This setup status supersedes older design-stage statements that credentials/model access are unverified or that no model call has occurred.

## Ready

- Dedicated OpenAI project: `sp-solution-test-assignment`.
- Project-scoped OpenAI key: `sp-solution-local-dev`, created through Firefox. It has All API-resource permissions within the dedicated project; existing keys were not changed.
- LangSmith tracing project: `sp-solution-test-assignment`, in the existing workspace.
- LangSmith key: `sp-solution-test-assignment-local`, personal token with a 30-day expiry (created September 9). It uses the existing workspace; do not describe it as isolated to this tracing project.
- LangSmith dataset `sp-solution-acceptance-v1` was created as an empty setup scaffold. The implemented evaluator idempotently exports fixture-derived examples and actual results; dataset existence does not establish an evaluation pass.
- Python 3.12 virtual environment, pinned dependencies and `uv.lock` installed successfully.
- Playwright bundled Chromium and FFmpeg installed. Headed browser, AI snapshot and persistent-profile cookie round trip verified on macOS.
- GitHub remote already works; main is the only branch. No GitHub token is needed in the application environment.

## Private environment file

Credentials are in **`.env.local`**, ignored by Git, untracked, with mode `0600`. The committed `.env.example` contains blank credential placeholders only. Explicitly load `.env.local` using `python-dotenv`; plain `load_dotenv()` does not necessarily choose that filename.

```python
from dotenv import load_dotenv
load_dotenv(".env.local", override=False)
```

Do not print the file, send its content to LangSmith, or include values in exceptions, command arguments or commits. Use `scripts/setup_check.py` for presence checks. Read-only setup verification reports and project identifiers are retained in ignored `docs/private/` files; they do not contain the credential values.

## Model and spending decision

The user selected **`gpt-5.6-luna` for now**, using existing OpenAI credits, and will add credits later. This overrides earlier Sol-first recommendations. Keep Luna configurable, but do not silently switch to a more expensive model. Luna's capability on the three tasks remains to be evaluated; a successful connectivity check is not a task-quality benchmark.

The $5 limit remains a maximum per logical task, not a target spend or a guarantee of available account credit. Use small bounded Luna experiments; if quota is exhausted, continue code/offline tests and report that funding is needed. Do not buy credits, enable auto-reload or raise limits automatically. Runtime admission now enforces persisted task and aggregate ledgers; configuration values alone never establish a pass. Budget and privacy boundary evidence is mapped in TEST-COVERAGE.md.

## Verification performed

1. OpenAI model-list authentication succeeded, and Luna is available.
2. Luna Responses API strict function call completed with expected validated arguments.
3. Input-token count endpoint succeeded; actual call used 132 input and 18 output tokens. Conservative cost estimate: $0.0000546, using an input premium; actual standard uncached-text calculation is lower. No Sol generation call was made.
4. LangSmith project read, dataset create/read and synthetic setup trace write/read succeeded. An immediate trace read initially returned 404; a later project-scoped read succeeded. Current SDK warns that legacy `read_run` is deprecated; prefer `client.runs.retrieve(run_id, project_id=...)` in new code and account for ingestion delay.
5. Headed bundled Chromium snapshot/click/profile persistence check passed. The synthetic profile is under `artifacts/profiles/setup-check`; it contains no real-site login.
6. `.env.local` is ignored, untracked and owner-readable/writable only. Setup scripts pass Ruff checks.

These are setup checks, not `FINAL-TEST.md` passes. The setup trace is explicitly labeled `setup-connectivity-check` with `agent_evaluated=false`.

## Commands that work now

```bash
uv sync --frozen
uv run python scripts/setup_check.py
uv run python scripts/browser_setup_check.py
uv run ruff check scripts
uv run browser-agent doctor
uv run browser-agent --help
```

The browser check opens and closes only its own synthetic profile; it makes no model calls. These setup probes are separate from the implemented offline `browser-agent doctor`. The project now installs an editable source package and the CLI entry point. Current runtime and evaluation commands are in README.md and FINAL-TEST.md; their existence does not mean the complete release suite passed.

## Remaining human-dependent work

Continue implementation validation and local synthetic evaluations using the existing setup. A dedicated `demo` profile was prepared and Yandex Eda was checked as described below; verify its current authentication and history when running the final live demo. Firefox’s existing login is not automatically the Playwright profile. Final consequential-action approvals remain required. Recorder capability has now been checked as described below; the actual browser-and-terminal demonstration remains to be recorded and reviewed.

The user will top up API credit later. Until then, preserve the existing balance and use Luna. No subscription, payment method, auto-reload or unrelated account settings were changed.

## Next agent

Read this file, then SYSTEM-DESIGN.md, IMPLEMENTATION-PLAN.md and FINAL-TEST.md. Reuse `.env.local` and the existing projects/dataset instead of creating duplicate credentials. Continue the ordered acceptance sequence and unresolved evaluation repairs; do not repeat tiny paid smoke calls without a new reason. Never upload real account data just because tracing is enabled: `AGENT_TRACE_MODE=synthetic-only` is enforced through explicit synthetic export and disabled automatic graph tracing. Review this boundary before real-site usage.

## Shopee demo candidate

The user has a Shopee Vietnam account with recent order history and proposed it for the real demo. A dedicated profile was opened at `https://shopee.vn/` for manual login. Login completion is recorded separately below; do not infer authentication from profile existence.

Reusable manual-login launcher (no model and no site-specific actor logic):

```bash
uv run python scripts/open_demo_browser.py https://shopee.vn/
```

Profile: `artifacts/profiles/demo`. Close the launched browser before another process opens this profile. The launcher only opens a user-supplied URL and waits; all agent navigation remains generic. It does not inspect password fields or copy Firefox cookies.

Proposed demo task: identify a product from recent completed order history, inspect the current listing and matching variant, compare price/availability with the historical order, and optionally prepare a cart **without placing an order or paying**. Use an unambiguous real product/date after inspecting history with user authorization. Do not treat a cart as a completed purchase. Shopee is an additional marketplace scenario, not a replacement for the exact three fixture examples. The live site's compatibility with the final actor remains untested; handle login challenges or unsupported controls honestly.

Current manual-login status: **user confirmed successful Shopee login on 2026-09-09** in the dedicated demo browser. At that setup check the launcher was still running; check current profile ownership and close any holder normally before reusing `artifacts/profiles/demo`. Authentication persistence after reopening and compatibility with the final actor remain to be verified. Google OAuth initially rejected the automated browser; the successful login method was not specified. Do not copy cookies from another browser.

Official login instructions: https://help.shopee.vn/portal/4/article/79436

## Live-browser operating preference

The user requests minimizing bot-check triggers. Reuse the logged-in demo profile, keep actions sequential, and avoid repeated login/reload attempts. Challenge-aware behavior is implemented, including persisted manual handover and Retry-After deadlines; regression scopes are recorded in TEST-COVERAGE.md. SYSTEM-DESIGN.md and FINAL-TEST.md retain the required live behavior. If challenged, pause for manual verification rather than polling or trying to evade detection. No guarantee of avoiding site challenges has been established.

## Preferred food demo: Yandex Eda

On 2026-09-09 the user confirmed: “yandex eda is ready”. Use Yandex Eda as the preferred live food-order demo candidate, with Shopee retained as an additional scenario. This was initially user-reported readiness; the subsequent read-only verification below established authentication and populated history. Persistence after reopening, relevant previous-week history and product availability still need current live verification. Reuse the prepared authenticated profile once identified; do not create a fresh login session unnecessarily.

Run the supplied history-dependent BBQ-burger and fries task if the account history supports it. Verify the restaurant from actual order history, then products, cart and checkout state. Stop before final order placement/payment unless exact consequential-action approval is supplied. Do not substitute invented history or claim success when the required prior order/products are unavailable. Preserve the challenge-aware browsing rules.

### Yandex Eda read-only verification — 2026-09-09

Verified through Computer Use in the existing Chrome for Testing window: the home page loads, the profile menu shows an authenticated account and Log out, and Orders opens a populated history with delivered and canceled orders. No CAPTCHA or security challenge appeared during this short check. No cart changes or order submissions were made.

Visible order dates were April 2025; a previous-week order was not verified. Use an accurately dated history-based prompt for an adapted live demo if necessary, label the adaptation, and retain the exact source task in fixture evaluations. Do not claim the literal previous-week requirement passed. Login persistence after browser restart and the final Playwright actor remain untested. An initial accessibility read during navigation was empty; the subsequent screenshot showed the loaded history, reinforcing the need for readiness waits and observation fallback. Private addresses, order IDs and screenshots are not included in the public documentation.

### Subsequent existing-session check — 2026-09-09

A later read-only Computer Use check again showed the existing authenticated Yandex Eda session and populated order history, still dated April 2025, without a visible challenge. This check reused the existing browser; it did not restart the profile or exercise the implemented Playwright agent. It therefore confirms current visible account access only. Previous-week history, restart persistence and autonomous live-task compatibility remain unproven; no new order or payment was submitted.

## Recorder capability check — 2026-09-09

On this Mac, FFmpeg is available and screen-capture permission was granted. A two-second H.264 screen recording encoded and decoded successfully at 2560×1600. This proves recorder operation only; it is not the assignment demonstration. The raw smoke file is private at `artifacts/final/recorder-smoke.mp4` and is not a public deliverable.

AVFoundation reported `Capture screen 0` as device 3 during that check. List devices before a later recording because indices can change:

```bash
ffmpeg -f avfoundation -list_devices true -i ""
```

After verifying the screen device, a bounded recording command is:

```bash
ffmpeg -f avfoundation -framerate 10 -capture_cursor 1 -pixel_format nv12 -i "3:none" -t 120 -c:v libx264 -pix_fmt yuv420p artifacts/final/demo-private.mp4
```

The example has no audio and a two-minute limit; change the duration deliberately for the actual run. Arrange the visible agent browser and terminal together before capture, then inspect the entire recording and redact private information in a separate shareable copy. Do not publish the raw screen recording or describe the recorder smoke as a completed demo.

## Actual actor live attempt — 2026-09-09

Run `9ad93502-a357-41e0-b888-b92413f295a5` reopened the prepared profile through the actual runner; a private actor screenshot confirmed authenticated Yandex Eda access. A native location prompt was declined manually. This goes beyond the earlier Computer Use-only checks, but it did not complete a task: an observation stalled for about 217 seconds and later invalid human-readable read scopes produced `unknown_ref` and manual handover. The console was stopped normally at 15:26:54 UTC, with no unresolved action recorded. No cart/order change or final video resulted.

The authenticated screenshot remains private in that run's evidence directory. Do not publish account details or infer that historical order/product requirements passed. The implementation now bounds whole observations to 10 seconds and describes exact-ref/null read scopes. Twenty-six focused browser/runner tests passed; full new staged/live evidence remains separate. Timeout asks for manual recovery, without automated reload or effect replay. Use the current validation record before retrying the live task.
