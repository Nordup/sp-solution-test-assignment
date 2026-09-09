# Local setup and implementation readiness

Prepared 2026-09-09. **Credentials and dependencies are ready for implementation on this machine.** The autonomous runtime and final acceptance suite are not implemented yet. This setup status supersedes older design-stage statements that credentials/model access are unverified or that no model call has occurred.

## Ready

- Dedicated OpenAI project: `sp-solution-test-assignment`.
- Project-scoped OpenAI key: `sp-solution-local-dev`, created through Firefox. It has All API-resource permissions within the dedicated project; existing keys were not changed.
- LangSmith tracing project: `sp-solution-test-assignment`, in the existing workspace.
- LangSmith key: `sp-solution-test-assignment-local`, personal token with a 30-day expiry (created September 9). It uses the existing workspace; do not describe it as isolated to this tracing project.
- Empty LangSmith dataset scaffold: `sp-solution-acceptance-v1`. Add actual fixture-derived examples/reference outputs during implementation; no evaluation has passed yet.
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

The $5 limit remains a maximum per logical task, not a target spend or a guarantee of available account credit. Use small bounded Luna experiments; if quota is exhausted, continue code/offline tests and report that funding is needed. Do not buy credits, enable auto-reload or raise limits automatically. Local environment budget/privacy values are configuration only; the implementation must actually enforce them.

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
```

The browser check opens and closes only its own synthetic profile; it makes no model calls. Neither script is the future `browser-agent doctor` implementation. The CLI and final-test commands in the design/runbook still need to be built. `tool.uv.package=false` is a dependency-only bootstrap; change packaging/entry points when the source package is implemented.

## Remaining human-dependent work

Implementation and local synthetic evaluations can start now. A real-site demo still needs the chosen service and a manual login to the agent's dedicated profile; Firefox's existing login is not automatically the Playwright profile. Final consequential-action approvals remain required. Video capture must be checked when the application is ready.

The user will top up API credit later. Until then, preserve the existing balance and use Luna. No subscription, payment method, auto-reload or unrelated account settings were changed.

## Next agent

Read this file, then SYSTEM-DESIGN.md, IMPLEMENTATION-PLAN.md and FINAL-TEST.md. Reuse `.env.local` and the existing projects/dataset instead of creating duplicate credentials. Implement the runtime and acceptance tests; do not repeat tiny paid smoke calls without a new reason. Never upload real account data just because tracing is enabled: `AGENT_TRACE_MODE=synthetic-only` must be honored by application code before real-site usage.

## Shopee demo candidate

The user has a Shopee Vietnam account with recent order history and proposed it for the real demo. A dedicated profile was opened at `https://shopee.vn/` for manual login. Login completion is recorded separately below; do not infer authentication from profile existence.

Reusable manual-login launcher (no model and no site-specific actor logic):

```bash
uv run python scripts/open_demo_browser.py https://shopee.vn/
```

Profile: `artifacts/profiles/demo`. Close the launched browser before another process opens this profile. The launcher only opens a user-supplied URL and waits; all agent navigation remains generic. It does not inspect password fields or copy Firefox cookies.

Proposed demo task: identify a product from recent completed order history, inspect the current listing and matching variant, compare price/availability with the historical order, and optionally prepare a cart **without placing an order or paying**. Use an unambiguous real product/date after inspecting history with user authorization. Do not treat a cart as a completed purchase. Shopee is an additional marketplace scenario, not a replacement for the exact three fixture examples. The live site's compatibility with the final actor remains untested; handle login challenges or unsupported controls honestly.

Current manual-login status: Google sign-in rejected the automated browser with “This browser or app may not be secure.” Shopee authentication is not verified. Try Shopee’s supported QR login using the already-authenticated mobile app; manual confirmation is pending. Do not bypass Google security checks or copy another browser’s cookies. This does not block implementation or synthetic evaluations.

Official login instructions: https://help.shopee.vn/portal/4/article/79436
