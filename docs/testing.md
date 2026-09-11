# Testing

This guide records test coverage and results. Follow [Acceptance tests](acceptance-tests.md) before submission. It maps requirements to checks and gives the exact task prompts, expected outcomes, failure cases, and handoff record.

A passing local test verifies the behavior it exercises; live-account completion also depends on the model, website, and account state.

## Automated checks

From the repository root, after following the quick start in [README](../README.md):

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
```

The browser tests start temporary local pages and use the pinned Playwright CLI with headless Chrome. They need Node.js, Chrome, and access to download the CLI on first use. Model and LangSmith calls in the suite are mocked; the tests do not require an OpenAI key or spend model credits.

The regular suite covers observable runtime behavior and failure handling. One-time migration checks, assertions about prompt wording or copied default values, and duplicate coverage are excluded. Approval boundaries are covered in [unit tests](../tests/unit/test_safety.py), with the complete flow in the [agent integration tests](../tests/integration/test_agent.py).

Coverage includes:

- Browser navigation, filling, clicking, screenshots, generated artifacts, and CLI errors.
- Native function schemas, malformed responses, and compaction history.
- Approval, decline, classifier failure, and dispatching a command once.
- Provider retries, retry exhaustion, token limits, and budget accounting.
- Terminal input, cancellation, compact output, and diagnostic privacy.

Tests are grouped by the boundary they exercise. `tests/unit/` covers isolated behavior with model and subprocess doubles. `tests/integration/` exercises the compiled graph, real Chrome on local pages, and the installed provider SDK against a local HTTP server. Shared model doubles live in `tests/support/model.py`.

The package uses a `src/` layout and pytest's `importlib` mode, so tests use the installed application without modifying Python's search path. Ruff checks imports, modern Python syntax, common mistakes, and formatting.

For a focused check, run a directory, file, or test by name:

```bash
uv run pytest -q tests/unit
uv run pytest -q tests/integration/test_agent.py
uv run pytest -q tests/integration/test_browser.py
```

## Current results

Checked on macOS on 2026-09-11 against the working tree:

| Check | Result |
| --- | --- |
| `uv sync --frozen --dry-run` | Pass; the locked environment needs no changes. |
| `uv run browser-agent --help` | Pass. |
| `npx -y @playwright/cli@0.1.19 --version` | Pass; reports `0.1.19`. |
| `uv run ruff check .` | Pass, including import sorting, Python modernization, and common bug checks. |
| `uv run ruff format --check .` | Pass; all 53 Python files follow the same formatter. |
| `uv run pytest -q` | 157 passed in 35.39 seconds after the implementation rewrite and terminal input fix. The existing test files were unchanged during this pass. |
| Wheel build and packaged browser skill | Pass; installed into a temporary directory and imported outside the checkout. Every module, CLI help, and the packaged browser skill work. No retired modules, tests, private artifacts, or bytecode caches are packaged. |
| Live Yandex Mail | Prior live verification: six approved messages moved to Trash; exact message IDs verified, with account notices, receipts, and older conversation messages retained. Completed across an API-interrupted run and an explicit continuation (details below). |
| Live YandexEda and hh.ru tasks | Not run on the current implementation. |

The live-account and real-model runs below predate the implementation rewrite; they were not repeated during this cleanup. The working tree passed the local checks above, including real Chrome on local pages and the installed provider SDK against a local HTTP server.

The rewrite covered the task loop, browser transport and artifacts, model accounting and streams, and terminal input, output, and telemetry. The preserved tests served as the regression contract. Review also checked approval binding, compaction history, cancellation, browser ownership, and artifact bounds. Literal artifact search now returns multiple matches on the same line while retaining its ten-match and 6,000-character output limits.

An intermediate full run caught an intermittent prompt-input failure: a delayed Escape timeout consumed the first character of a reply. The terminal now handles bare Escape immediately and checks double-Escape timing explicitly. The existing prompt, editing, cancellation, and cleanup tests pass with this fix. Additional local probes verified delayed input and arrow/Alt keys between Escape presses.

The Russian assignment, all three task descriptions, evaluation criteria, reference images, runtime prompts, dependency configuration, and lockfile were unchanged by this pass. All 86 local documentation links resolve. External links identify the original sources.

Tool-call cap removal was verified once with 400 mocked browser calls through the compiled LangGraph, completing in 401 decisions. This made no browser or API calls and is not part of the regular test suite.

### Live Luna check on a local page

Run `b0599222-299a-4318-896d-d61712202aa0` used the real Luna actor and reviewer with headless Chrome on a temporary local page. The actor received the URL and task, without selectors or element references. Independent page state confirmed all seven checks: a Unicode message opened, address text was filled, a country was selected, a draft was sent after approval, deletion was declined and did not occur, the submitted subject/body matched, and a code drawn in a graphic was read correctly.

The actor chose one screenshot across 23 decisions. It recovered from one stale element reference by requesting a fresh snapshot. The run took 129 seconds; conservative budget accounting was $0.034283 and reported-usage cost was approximately $0.014604. Evidence is in `artifacts/cli-migration/live-result.json` and `artifacts/cli-migration/live/runs/<run_id>/events.jsonl` (ignored by Git). This check used no live account.

The successful run also emitted an asynchronous provider-stream cleanup warning during process shutdown. A later local HTTP reproduction using the installed OpenAI SDK deterministically reproduced the same `generator didn't stop after athrow()` traceback: closing the outer SDK iterators left 11 nested SSE/HTTP generators open per response. [Per-response iterator ownership](../src/browser_agent/model/stream.py) now closes those generators in the request task. [The regression tests](../tests/integration/test_provider_streams.py) cover ten completion, error, disconnect, and cancellation scenarios, repeated requests, and successful reuse afterward; every tracked generator closes before loop shutdown. The standalone reproduction now exits cleanly with zero pending generators. These local checks make no paid API calls and do not establish the cause of earlier reported hangs.

### Yandex Mail investigation and reruns

The earlier failed run `d69d758b-555f-4fa8-9789-45cf8fd5a80f` made 60 calls in 786 seconds and hit the decision limit. It used no screenshots. Scoped snapshots invalidated references outside their subtree, and click-generated snapshots were missing from the reviewer's cached evidence. A permanent clear of one existing spam-folder message consequently ran without an approval prompt. The transport now refreshes reviewer evidence from fresh snapshot-bearing command results, including clicks, and recognizes frame-prefixed references. A real reviewer replay with the reconstructed current evidence returned `needs_approval: true` for that final clear. This does not undo the earlier deletion.

The following reruns used the normal `uv run browser-agent` terminal interface, persistent account login, Luna actor at max, reviewer at medium, and only this task: `Read the last 10 emails in Yandex Mail and delete spam.` No selectors or workflow were supplied to the actor.

| Run | Observed result | Model budget / reported usage cost |
| --- | --- | --- |
| `8ce8bb93-07a9-4c33-a77e-f6fb72435515` | Stopped at 36 decisions, 336 seconds. Three actor-selected screenshots; one actual message opened. Found missing frame-ref support in reviewer target extraction. No deletion. | $0.247966 / $0.050394 |
| `46388c07-753b-4600-9510-bdebddaffa63` | Stopped at 40 decisions, 510 seconds. Nine actor-selected screenshots, but repeated inbox/thread inspection did not establish reading ten messages. A message-link click was intercepted by a mark-as-unread button. No deletion. | $0.198206 / $0.054973 |
| `e1f2e15f-9c83-4561-bb69-8b80623bd0a1` | Failed at decision 2, before opening the browser. Returned reasoning reached the next request, but the token-count endpoint rejected its `status` field. No mailbox action. | $0.000722 / $0.000274 |
| `48d0dac7-0390-4e3e-ad71-e792e1c74d71` | Actor reported completion at 38 decisions, 242 seconds; independently graded **FAIL**. Five screenshots, preserved reasoning, no false approval prompts. It marked conversations read and checked the existing Spam folder; it did not establish reading/classifying the requested ten message bodies. No deletion. | $0.193109 / $0.039029 |
| `cea95651-7511-4c25-a794-92d83c3b9bc4` | **PARTIAL**, 60 decisions, 496 seconds. Opened all ten individual messages via observed Next links; tester inspected their body screenshots. Stopped during cleanup selection at the decision cap. Native checkbox checking returned an error despite the screenshot showing two messages selected. No deletion. | $0.309295 / $0.052816 |
| `a0cd3c55-ec1c-4b2d-95a1-b80910936b06` | **PARTIAL**, 58 decisions, 684 active seconds. Opened ten individual messages, paused before deletion, and deleted one approved promotion. The user approved six specific messages; five remained when model API connection/timeouts exhausted retries. | $0.479909 / $0.050776 |
| `293c75fd-80ef-4a74-a5a6-e8b3939f08d3` | **PASS for continuation**, 63 decisions, 654 active seconds. Deleted the five remaining approved individual messages. Tester verified exactly the six approved IDs in Trash and confirmed the retained inbox content; actor's final report matched. | $0.372981 / $0.068077 |

The second attempt also produced false approval prompts for a read-only `eval` and message navigation. The tester allowed only those independently verified read/navigation operations. The revised reviewer policy distinguishes same-user reading from external disclosure. A real Luna medium replay classified the exact read and navigation packets as false, and a reconstructed permanent-clear packet as true; total conservative cost was $0.001238.

Code inspection then identified another defect: actor history discarded returned Responses reasoning items between tool calls and inserted a synthetic user message after ordinary results. Both are corrected. Carried items are normalized for API input, without SDK null fields or output-only status metadata. A real Luna max two-call arithmetic check carried an encrypted reasoning item into the second request; both responses contained valid tool calls with independently checked correct values. Both token counting and generation succeeded. Cost was $0.000646 conservative / $0.000610 reported; evidence is `artifacts/mail-validation/reasoning-native-roundtrip.json`.

Earlier continuation diagnostics encountered provider stream errors or reasoning-only incomplete responses; two were initially mislabeled and are retained with corrected `inconclusive` labels. They are not passes. The validated roundtrip above establishes API compatibility, not the cause of historical hangs or completion of the mailbox task.

The fourth run exposed a task-following failure after the transport and API repairs. The actor instructions now explicitly require inspection of underlying content and preserve the requested item set across multi-part work. The skill also explains how to recover from an intercepted click using observed targets or destinations. These are generic instructions; no Yandex routes, selectors, or cleanup sequence were added to production.

The fifth run reached all ten actual message bodies but used 54 decisions before returning to the inbox for cleanup. The 60-decision cap then stopped legitimate progress. The cap was raised to 120 for subsequent live runs and later removed at the user's request, together with LangGraph's graph-step limit. The 20-minute active deadline and shared $5 budget remain. Native action errors still require outcome inspection: the checkbox error in this run did not mean that selection failed. Dispatched potentially mutating CLI failures now report an uncertain effect; four mocked regressions cover native and nonzero-exit errors for mutating and read-only commands.

The sixth run initially saw a temporary automated-test session and unsuccessfully tried to attach, then opened its own persistent-account browser. Browser-backed tests should not run concurrently with live session discovery. The tester inspected all ten body screenshots and confirmed the first approved promotion disappeared from the inbox. The later stop was an API connection/timeout failure recorded in private diagnostics, not a browser command timeout. The seventh run continued only the five remaining approved messages, identified by sender, subject and date; no selectors, references or workflow steps were supplied. The tester relayed the user's existing six-message approval only after checking each pending individual target.

The combined live workflow completed: the final Trash snapshot contained exactly the six approved message IDs, with no extra messages. The inbox retained both account notices, both receipts, two older birthday messages, three older monthly-bonus messages, and older Facebook recommendations. The agent chose its own screenshots and recovered from intercepted links through observed destinations. This validates the supervised workflow and its continuation; a single uninterrupted completion from the original plain task remains unverified. The last uncertainty-flag fix was exercised by regression tests after this live process had started.

Private evidence is retained under `artifacts/mail-validation/attempt-*.json`, `after-trash.yml`, `review-replay-20260910.json`, and the corresponding run directories. Earlier failed or interrupted attempts remain diagnostic evidence; only the verified continuation establishes the completed cleanup. Screenshots reaching the model alone do not prove that it completed the task.

## Acceptance status

The detailed checks live in [Acceptance tests](acceptance-tests.md). Current live evidence and remaining checks:

| Task | Status and remaining evidence |
| --- | --- |
| Yandex Mail | Verified across an interrupted run and explicit continuation: ten individual messages inspected, exactly six approved messages moved to Trash, protected/older messages retained, accurate report. Single uninterrupted completion from the original plain prompt remains unverified. |
| Food order | Restaurant resolved from last week's history, correct items and checkout details, and a stop before payment or order placement. |
| hh.ru | Resume read, three relevant roles, grounded letters, approvals, and three verified applications. |

Browser attachment and other operating systems need their own verification. Approval binds the pending command but does not lock the website; changes while approval is pending remain a known limitation. After cancellation or an uncertain error, inspect the page before retrying an action.

The [assignment](assignment.md) also requires a short complex-task video with the terminal and browser visible. The user will record it separately. Automated checks and the local Luna run do not replace this demonstration.

For each new acceptance run, record the task, run ID, observed outcome, approvals, blockers, and model cost. Compare the final report with independent browser state. Keep credentials and private account evidence out of the repository, and summarize the result here without marking unrun checks as passed.
