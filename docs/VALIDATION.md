# Implementation validation

Status: **IN PROGRESS — not ready for submission.** This file distinguishes implemented mechanisms from completed assignment deliverables. The original source assignment and final-test criteria remain unchanged.

## Current evidence

- Native function registry, strict Pydantic argument validation, bounded repairs and actual OpenAI token admission are implemented.
- LangGraph loop, pure human interrupts, SQLite checkpoints and separate durable action/approval/spend records are implemented.
- Playwright current-ref actions, persistent profile ownership, scoped/paginated observations and selective screenshots are implemented.
- Password values were found in native Playwright snapshots during integration testing. Explicit redaction, password-ref exclusion and screenshot masks now have regression coverage.
- At the latest recorded staged run: stage 3 passed 78 deterministic tests; stage 4 passed 20 real Chromium tests. Further failure coverage is being added and the final stages will be rerun.
- Genuine subprocess crash tests kill only a harness-owned process after its local server commits a submission. Restart preserves one submission and either reconciles observed success or retains uncertainty.
- The first integrated Luna preflight produced a valid structured call and settled 58 microdollars ($0.000058). The first LangSmith retrieval omitted its parent field; a subsequent read explicitly selected it and verified the existing root/child relationship. The preflight now passes with the original failure and a verification amendment retained, without another model call.
- The mail, food, jobs, generalization and recovery model evaluations have not yet passed. No final video has been recorded.

Private machine-readable results are under `artifacts/final/`, `artifacts/evals/` and `artifacts/runs/`. Final sanitized evidence and verified experiment/video links will be added after review. Initial failed attempts remain recorded.

## Implementation refinements

The runtime uses an 18 KB UTF-8 observation limit and exact provider request-token counting rather than claiming a fixed token count for every accessibility excerpt. Total input is capped at 20,000 tokens. This bounds giant labels and multilingual text without assuming character count equals token count.

The default model is Luna, with low reasoning effort and a conservative $0.25-per-million input reservation (including the documented cache-write premium) and $1.20-per-million output. Verified against the [official Luna model page](https://developers.openai.com/api/docs/models/gpt-5.6-luna) on September 9, 2026. All retries and nonacting reviewers use the same task ledger.

A browser-dispatched action is recorded as `observed`; it is not itself semantic task success. Final claims require actual observation quotes and an independent completion review. Fixture evaluations additionally check server-side outcomes and factual consistency.

A user denial terminates the current run as partial. This conservative boundary prevents an alternate route or tool from silently revisiting the denied effect. A new task with a genuinely revised instruction can be started explicitly by the user.

Synthetic LangSmith exports are explicit and isolated from real-account runs. Automatic graph tracing is disabled; fixture network requests are restricted to their registered local origin. No production approve-all option exists.
