# Implementation validation

Status: **IN PROGRESS — not ready for submission.** This file distinguishes implemented mechanisms from completed assignment deliverables. The original source assignment and final-test criteria remain unchanged.

## Current evidence

- Native function registry, strict Pydantic argument validation, bounded repairs and actual OpenAI token admission are implemented.
- LangGraph loop, pure human interrupts, SQLite checkpoints and separate durable action/approval/spend records are implemented.
- Playwright current-ref actions, persistent profile ownership, scoped/paginated observations and selective screenshots are implemented.
- Password values were found in native Playwright snapshots during integration testing. Explicit redaction, password-ref exclusion and screenshot masks now have regression coverage.
- The current `03-contracts.xml` records 94 passing tests and `04-browser.xml` records 24 passing tests, with no failures, errors or skips. These include durable scope-memory, P14 no-progress and B11 login-expiry coverage. They are admission checks, not proof of autonomous task completion; stage 8 must still follow the core evaluation path. Earlier integration failures remain saved separately.
- Genuine subprocess crash tests kill only a harness-owned process after its local server commits a submission. Restart preserves one submission and either reconciles observed success or retains uncertainty.
- The first integrated Luna preflight produced a valid structured call and settled 58 microdollars ($0.000058). The first LangSmith retrieval omitted its parent field; a subsequent read explicitly selected it and verified the existing root/child relationship. The preflight now passes with the original failure and a verification amendment retained, without another model call.
- After the memory/schema changes, preflight (`eacb45c0-c932-46e8-83c0-ce20cd35790c`) passed strict calls, usage accounting and nested LangSmith verification, with another $0.000058 settled. A fresh ordered preflight after the quote-feedback change also passed; its authoritative record is the session preflight JSON.
- The first three mail attempts failed: missing retained page content caused repetition, a no-progress check misclassified distinct inbox returns, and the actor expanded a changing collection beyond its original scope. The approval gate prevented the out-of-scope deletion. Those failed trajectories are retained in LangSmith. Generic memory/progress handling and evaluator evidence checks were strengthened before rerunning.
- The fourth mail attempt identified the correct original collection but failed exact-quote validation because the model joined separate snapshot nodes. It made no deletion; its failed trace is verified. Repair feedback now specifies a short contiguous observed quote. A fifth attempt is in progress.
- The mail, food, jobs, generalization and recovery model evaluations have not yet passed. No final video has been recorded. A separate two-second FFmpeg screen-capture smoke encoded and decoded successfully; it proves recorder capability only, not a task demonstration.

Private machine-readable results are under `artifacts/final/`, `artifacts/evals/` and `artifacts/runs/`. Final sanitized evidence and verified experiment/video links will be added after review. Initial failed attempts remain recorded.

## Implementation refinements

The runtime uses an 18 KB UTF-8 observation limit and exact provider request-token counting rather than claiming a fixed token count for every accessibility excerpt. Total input is capped at 20,000 tokens. This bounds giant labels and multilingual text without assuming character count equals token count.

Structured memory is required before the first consequential effect and every four decisions. An originally selected collection is stored with observed identities and exact evidence quotes; later page changes cannot replace it. Notes and actual action receipts survive checkpoint rewind. The independent reviewer receives that scope and rejects explicitly out-of-scope effects before approval. These mechanisms have deterministic coverage; their task-level effectiveness still requires the model evaluations below.

The default model is Luna, with low reasoning effort and a conservative $0.25-per-million input reservation (including the documented cache-write premium) and $1.20-per-million output. Verified against the [official Luna model page](https://developers.openai.com/api/docs/models/gpt-5.6-luna) on September 9, 2026. All retries and nonacting reviewers use the same task ledger.

A browser-dispatched action is recorded as `observed`; it is not itself semantic task success. Final claims require actual observation quotes and an independent completion review. Fixture evaluations additionally check server-side outcomes and factual consistency.

A user denial terminates the current run as partial. This conservative boundary prevents an alternate route or tool from silently revisiting the denied effect. A new task with a genuinely revised instruction can be started explicitly by the user.

Synthetic LangSmith exports are explicit and isolated from real-account runs. Automatic graph tracing is disabled; fixture network requests are restricted to their registered local origin. No production approve-all option exists.
