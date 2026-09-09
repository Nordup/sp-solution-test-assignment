# Implementation plan and next-agent goal

Prepared 2026-09-09. This is a proposed execution contract. Implementation and paid evaluation have not started. The detailed rationale and primary sources are in [IMPLEMENTATION-RESEARCH.md](IMPLEMENTATION-RESEARCH.md).

## Fixed constraints and chosen baseline

User decisions: Python, Playwright, OpenAI API keys available, LangSmith evals, **$5 per logical task run**, public repository, English communication, Russian source preserved. **Use `main` only. Never create another branch or worktree.**

Recommended baseline: Python 3.12 + uv, native async OpenAI Responses SDK, Pydantic, Playwright 1.62.0, Rich/Typer CLI, LangSmith, pytest/Ruff. Start with configurable `gpt-5.6-sol` at low reasoning effort. Use an explicit single-controller loop and an independent nonacting risk reviewer. These recommendations should be revised only for a concrete compatibility or evaluation finding, recorded in the decision log.

Aim for September 10 EOD. Reported employer deadline is September 11 around 17:00, timezone unconfirmed. Deliver working code, reproducible instructions, evaluation evidence, repository URL and a short demonstration video. No deployment is needed.

## Work sequence

### Milestone 1 — runnable skeleton and compatibility checks

Create `pyproject.toml`, `uv.lock`, `.env.example`, source package, CLI entry point, configuration and test setup. Keep secrets and profiles ignored. Add `doctor` to check Python/browser installation, configured model, key presence without printing it, and LangSmith configuration. Network checks must be explicit and cost-aware.

Smoke-test the locked native SDK with strict tool calling, usage accounting and input-token counting. Smoke-test the LangSmith wrapper with the Responses API. Confirm the configured model is available before investing in prompt tuning. Never silently switch provider or remove the budget limit on failure.

Promote the [research probe](research/playwright_snapshot_probe.py) into proper browser adapter conformance tests. Verify the bundled Chromium version, not only the installed Chrome used during research. Implement dedicated persistent profiles, manual login, page IDs, snapshot refs, bounded extraction and screenshots.

Exit: a clean checkout can open a visible browser, capture a bounded AI snapshot and execute a validated current ref on a local fixture. No claim of agent autonomy yet.

### Milestone 2 — complete loop with safety and accounting

Implement the generic tool registry and strict schemas. Preserve Responses call/result continuity. Each decision must pass input-budget admission, tool validation and policy evaluation before browser execution. Refresh observations after actions. Provide explicit completion and partial/failure statuses.

Implement the shared persisted cost ledger before making repeated model calls. Cap each task at $5 including risk checks, compaction, retries and optional judges. Disable hidden SDK retries. Implement bounded transient-provider retries, structured tool errors, stale-ref replanning and uncertain-action verification.

Implement concrete approvals with one-time payload binding, revalidation, denial persistence and an audit trail. There must be no alternate tool path around the gate. Keep passwords out of agent inputs; pause for manual login or CAPTCHA.

Exit: an agent solves a small unfamiliar local multi-step task from a short prompt, blocks a consequential action pending approval, resumes correctly and reports observed evidence. Deterministic boundary tests pass.

### Milestone 3 — fixtures and LangSmith evaluation

Build three deterministic local fixture applications matching the semantic difficulty of the supplied mail, food and jobs examples. Use synthetic data, multiple routes, believable competing choices, history/profile dependencies and meaningful final state. Keep fixture internals outside runtime inputs. A lightweight local HTTP server and static/JS pages are sufficient; use a small server framework only if it simplifies state and fault injection.

Create a versioned LangSmith dataset with inputs containing task and starting URL; put expected state and seed in evaluator-side configuration/reference outputs. Add an async evaluation target, code evaluators and structured result export. Remote dataset creation must be idempotent: find/update by name and case ID rather than duplicating on every run.

Run the three core cases once with concurrency 1 and an aggregate $15 ceiling. Reserve $5 per case before starting it. Do not auto-repeat experiments indefinitely. Fix substantive failures, then run a bounded regression selection. The final three-seed core reliability experiment has nine cases and a $45 maximum; treat that as a separate explicitly configured experiment, not a hidden extension of the first $15 invocation.

Add unseen-task, changed-layout, injection, denied/changed approval, duplicate-submit, context and budget tests. Most failure-policy tests should be deterministic with a fake provider and need no API spend. At least one live-model trace must show recovery after a fixture-induced browser failure, rather than only unit-test coverage.

Exit: real LangSmith experiment links, exported case-level results and complete failure accounting. Never mark missing credentials or skipped tests as passes.

### Milestone 4 — real-site smoke, demo and final review

Use an available dedicated logged-in account for a real task. Prefer food history → correct items → verified checkout, stopping before final payment. If history/account access is unavailable, record that blocker; do not hardcode a restaurant or claim a synthetic fixture is the real service.

Capture browser and terminal together during an actual run. Preserve sufficient continuity to demonstrate autonomy; include task, actions, any clarification, verification and final outcome. Remove sensitive data from the shareable copy. Finalize Playwright videos by closing the context. Keep full private artifacts outside Git, publish only intentionally sanitized evidence.

Review runtime code for site/task-specific hints, unchecked mutation paths, blind retries, regex parsing of model prose, ignored exceptions and documentation claims. Reproduce setup from the lockfile and run the relevant checks once after final changes. Commit and push directly to `main`.

Exit: submission checklist below complete, or a precise external-dependency report explaining which deliverable still needs human input.

## Suggested package organization

```text
src/browser_agent/
  cli.py             # doctor, login, run, resume; terminal rendering
  config.py          # validated configuration, model and price table
  agent.py           # explicit decision loop and terminal statuses
  llm.py             # Responses adapter, retry and token-count interface
  browser.py         # single controller, profiles, tabs, target resolution
  observation.py     # bounded snapshots, current-ref registry, evidence
  tools.py           # generic schemas and dispatcher
  safety.py          # risk review, exact approval binding, denial rules
  budget.py          # reservations, settled and unknown costs
  context.py         # bounded history, progress notes, compaction
  journal.py         # append-only events, atomic checkpoints, resume
  telemetry.py       # LangSmith spans, redaction and usage metadata
  models.py          # shared typed state and result structures
  prompts.py         # universal task-independent instructions

evals/
  fixtures/          # synthetic sites, reset/inspect API, seeded data
  cases/             # task inputs and evaluator-only expected outcomes
  evaluators.py      # objective state and trajectory checks
  run.py             # bounded aevaluate experiment runner

tests/               # deterministic policy/protocol/browser tests
```

This is a responsibility map, not a requirement to create empty files. Merge small cohesive modules when useful. Keep all fixture-specific knowledge under `evals/` or tests, never in runtime prompt/tool logic.

## Proposed CLI contract

These commands do not exist yet; the implementation should provide them or document any intentional naming change.

```bash
uv sync --frozen
uv run playwright install chromium
uv run browser-agent doctor
uv run browser-agent login --profile demo
uv run browser-agent run --profile demo --budget-usd 5 "<ordinary task>"
uv run browser-agent resume <run-id>
uv run pytest
uv run ruff check .
uv run python -m evals.run --suite core --repetitions 1 --max-experiment-usd 15
```

Configuration should include `OPENAI_API_KEY`, `OPENAI_MODEL`, `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT`, optional workspace/endpoint as required, tracing/privacy mode, profile/artifact directories, per-run budget and experiment cap. Never put actual values in `.env.example`. Reject budgets above the user's $5 task cap unless the user explicitly changes it; a CLI flag is not independent authorization.

## Required deterministic tests

| Area | Concrete assertions |
| --- | --- |
| Protocol | Unknown tools, malformed args, extra fields, refusal and incomplete responses never become browser actions; call/result IDs remain paired |
| References | Missing, stale, wrong-page and ambiguous refs are rejected; iframe refs work on the pinned browser |
| Safety | Denial stops effect; Enter and click use the same gate; changed letter/amount/recipient invalidates approval; no production auto-approve |
| Resume | Consumed approval cannot replay; uncertain dispatch is observed before any retry; spend survives restart |
| Recovery | Retry count/backoff bounded; auth failure stops; timeout after submit does not duplicate submission |
| Context | Large names and pages are bounded; truncation visible; compaction keeps constraints and unresolved protocol items |
| Budget | Helper calls count; output reserve includes maximum; unknown billing retained; no call dispatched over remaining cap |
| Privacy | Trace exports omit configured sensitive values; profiles and local journals remain ignored |

Do not write tests that only repeat implementation constants. Exercise observable boundary behavior and state changes.

## Result and grading contract

Each run exports a structured object with `run_id`, status, user-facing summary, verified outcomes, remaining work, evidence IDs, step count, token usage, settled cost, uncertain reservations and LangSmith trace URL when available. The evaluator independently attaches task/safety/recovery/context/evidence scores from fixture state and trajectory. Runtime success and evaluator pass are separate fields.

Safety is a hard gate, not averaged away by task completion. Failure cases should name the actual cause. A model-generated final answer is not independent evidence. A real-site pause before a required deletion/application is incomplete for that scenario; checkout before payment is an explicitly allowed food boundary.

The fixture approval responder must validate a proposed effect against the fixture's permitted changes. It must never control a production account or inject a step-by-step plan into the actor. Seed data and reference outputs may be stored in LangSmith but not forwarded as actor input.

## Submission checklist

- Clean install and visible-browser run documented and reproduced.
- Universal autonomous loop with typed tools, bounded context and actual recovery.
- Reliable tested approval boundary and truthful limitation statement.
- Persistent manual login and resume demonstrated.
- Three core semantic tasks evaluated, with all results and exact sample sizes reported.
- Safety/context/recovery tests and at least one unfamiliar task included.
- Per-run $5 enforcement and aggregate experiment accounting verified.
- LangSmith dataset/experiment identifiers and sanitized result export available.
- One short video of an actual complex task; label fixture versus real site accurately.
- README explains architecture, setup, limitations, tests and evidence; research claims replaced by measured implementation claims where appropriate.
- No credentials, profiles, private mail/resume data, or unredacted account artifacts committed.
- Only `main` exists; repository pushed and working tree clean.

## Copy-paste goal for the next agent

> Implement this assignment end to end in the existing public repository, directly on main. Never create another branch or worktree. Read docs/CONTEXT.md and the three reference images first; follow docs/IMPLEMENTATION-RESEARCH.md and docs/IMPLEMENTATION-PLAN.md as the baseline. Build Python + native OpenAI Responses + Pydantic + Playwright + LangSmith with a visible browser and terminal interface. Use generic live-observation tools, an autonomous decision loop, bounded context, persistent manual login, code-enforced critical-action approvals, real bounded retries/replanning and evidence-based completion. Do not add site-specific scripts, paths, selectors or regex extraction of JSON from model prose. Enforce $5 total per logical task including all helper/retry/evaluator calls, persisting spend across resume. Bound each evaluation experiment explicitly; start with three core cases once and a $15 experiment cap. Build deterministic fixture/state-based evals for the three supplied examples plus safety, recovery, context and an unseen task. Create and run LangSmith experiments when credentials are configured; never fabricate passing results. Produce reproducible setup, tested code, honest evaluation results and a short actual-run demo video; use a real food checkout task if a suitable account is available, stopping before payment. Keep secrets and private artifacts out of Git. Work autonomously through implementation and fixes; ask only for missing external credentials/login or exact consequential-action approval. If an external dependency blocks a real-site deliverable, finish independent work and state the exact remaining requirement. Commit and push the finished work to main and report repository, experiment and video locations plus any measured limitations.

This text is ready to use after the user decides to start implementation. No separate Codex task or persistent goal was created during research.
