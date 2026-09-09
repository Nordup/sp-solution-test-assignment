# Implementation research and architecture recommendation

Current implementation specification: [SYSTEM-DESIGN.md](SYSTEM-DESIGN.md). It consolidates and supersedes conflicting proposed details in this research document.

**Updated recommendation:** use a small LangGraph StateGraph around the native OpenAI/Playwright components. Read [the focused LangGraph research](LANGGRAPH-RESEARCH.md) first; it supersedes the initial orchestration/persistence choice below.

Research date: 2026-09-09. This is a design document, not a claim that the agent or evaluations already exist. Read the [original assignment](assignment.ru.md), [HR clarification](hr-requirements.ru.md), and [reference images](assets/ideal-solution-01.jpg) alongside it. The [execution plan](IMPLEMENTATION-PLAN.md) turns this analysis into work for the next agent.

## 1. Recommendation and decision status

Build a **Python terminal application using the native OpenAI Responses SDK, Pydantic, Playwright, and LangSmith**. Use one acting agent with an explicit observe → decide → validate → approve if necessary → act → verify loop. Add an independent, nonacting risk reviewer; implement retry and recovery in ordinary Python. Keep the interface to a visible Chromium browser and a readable terminal activity stream.

The user explicitly chose Python, Playwright, OpenAI API access, LangSmith evaluations, a $5 budget per run, a public repository, and work exclusively on `main`. The native SDK, model defaults, component design, and thresholds below are recommendations selected for this assignment, not additional verbatim user decisions.

Start with `gpt-5.6-sol`, low reasoning effort, as the acting model and risk reviewer. Make the model configurable; test `gpt-5.6-terra` as a cheaper alternative once the baseline passes. Do not start by distributing the task among several acting agents. A second browser controller introduces shared-state and approval complications without helping the two-day deliverable.

Use `uv`, Python 3.12, `pytest`, and Ruff. No application database, web dashboard, LangGraph service, vector database, or MCP server is necessary for the initial submission. A local append-only event journal and atomic checkpoint files are sufficient. LangSmith supplies hosted traces and experiment comparisons.

## 2. What actually determines acceptance

The assignment and HR clarification make the engineering boundaries more important than a polished happy-path video. The runtime must choose its next step from current observations, work across unfamiliar sites, control its context, and recover from errors. It must not contain task-specific paths, selectors, restaurant names, or a prewritten mail/order/application procedure. HR particularly emphasizes robust critical-action confirmation, native structured interaction, and real retry code. [Assignment source](https://kolbasa.craft.me/ai_test_task); [preserved HR message](hr-requirements.ru.md).

The reference screenshots establish a presentation pattern: browser and terminal together, an ordinary user request, visible tool activity, and a verified result. They show a DOM helper agent, but do not prove that the same internal architecture is required. Reproduce the observable behavior and clarity, rather than guessing the other candidate's hidden implementation.

| Requirement | Proposed implementation | Evidence to collect |
| --- | --- | --- |
| Autonomous decision cycle | Explicit bounded loop; every action followed by observation | LangSmith trajectory and terminal log |
| Universal behavior | Generic browser tools with refs discovered from observations | Changed layouts and an unseen task |
| Useful page representation | Bounded accessibility snapshots plus selective vision | Snapshot excerpts, budget tests, iframe test |
| Critical-action safety | Central gate, concrete one-time approval, revalidation | Denial, changed payload, alternate-action bypass tests |
| Structured LLM interaction | Strict function schemas and Pydantic validation | Invalid arguments never reach browser |
| Actual recovery | Transport retries plus browser-state recovery and replanning | Injected failures with traces |
| Context management | Token counting, bounded observations, completed-history compaction | Long-page and long-run tests |
| Persistent login | Dedicated persistent browser profile and manual handover | Close/reopen login smoke test |
| Honest finish | Evidence-linked result with explicit partial/blocked status | State-based evaluators and final report |

These are our acceptance interpretations. The three supplied task examples are not a promised exhaustive employer test suite.

## 3. SDK comparison

| Option | Why it is credible | Tradeoff for this assignment | Decision |
| --- | --- | --- | --- |
| Native OpenAI Python SDK | Direct Responses API, structured tool calls, async support; LangSmith can wrap it | We own loop, tool validation, history, approvals, and retry policy | **Choose**: the important mechanisms stay explicit and reviewable |
| OpenAI Agents SDK | Existing agent loop, tool approvals, resumable interruptions, guardrails | Budget reservation and browser-specific revalidation still need integration | Good runner-up; choose if explicit-loop implementation becomes unnecessarily repetitive |
| Pydantic AI | Strong typed tools, deferred approvals, retries, history processing | Another abstraction and evolving APIs to learn within the timebox | Good alternative, not needed to obtain Pydantic validation |
| LangChain / LangGraph | Integrations and durable graph orchestration | Adds concepts beyond a single local browser loop | Defer; LangSmith works independently |
| Vercel AI SDK | Strong tool/agent APIs in its TypeScript ecosystem | User selected Python; switching language adds no acceptance value | Do not choose for this project |
| Browser Use / Stagehand | Existing browser-agent or observe/act/extract capabilities | More adaptation of a prebuilt browser stack, less direct ownership of the tested mechanisms | Useful references; do not put their agent loop underneath ours |

This is a scope judgment, not a claim that the alternatives lack safety or retry support. The [OpenAI Agents approval documentation](https://openai.github.io/openai-agents-python/human_in_the_loop/) explicitly covers pausing and resuming tool approvals. [Pydantic AI deferred tools](https://pydantic.dev/docs/ai/tools-toolsets/deferred-tools/) support approval workflows too. [LangGraph](https://docs.langchain.com/oss/python/langgraph/overview) targets durable orchestration. [LangSmith's native OpenAI integration](https://docs.langchain.com/langsmith/trace-openai) removes the need to adopt LangChain just for tracing.

Additional comparisons used the official [Vercel tool documentation](https://ai-sdk.dev/docs/ai-sdk-core/tools-and-tool-calling), [Browser Use repository](https://github.com/browser-use/browser-use), and [Stagehand overview](https://docs.stagehand.dev/v3/first-steps/quickstart). Favor small composable components here, consistent with the design guidance in [Building effective agents](https://www.anthropic.com/engineering/building-effective-agents).

### Dependency baseline

PyPI metadata checked on the research date reports these releases. These are candidates for the initial lockfile, not an already tested dependency set. Resolve them together, run smoke tests, and commit `uv.lock`; do not install floating versions during every run.

| Component | Observed version | Purpose |
| --- | --- | --- |
| [openai](https://pypi.org/project/openai/) | 3.10.0 | Async Responses client |
| [playwright](https://pypi.org/project/playwright/) | 1.62.0 | Browser and AI snapshots |
| [pydantic](https://pypi.org/project/pydantic/) | 2.13.5 | Tool/config/result schemas |
| [langsmith](https://pypi.org/project/langsmith/) | 0.12.2 | Tracing, datasets, experiments |
| [rich](https://pypi.org/project/rich/) | 15.0.0 | Terminal events and approval display |
| [typer](https://pypi.org/project/typer/) | 0.27.2 | CLI commands |

Python 3.12 is a compatibility choice. Do not copy examples for older SDK generations without checking the locked API: the current OpenAI SDK, for example, documents HTTPX2. [Official SDK](https://github.com/openai/openai-python).

## 4. Browser representation: use Playwright's existing AI snapshot

Playwright now provides `page.aria_snapshot(mode="ai", depth=...)`; AI mode exposes element references and frame content. Locator snapshots also support bounded depth. This is a better starting point than writing a general-purpose DOM-to-text extractor. [Page snapshot API](https://playwright.dev/python/docs/api/class-page#page-aria-snapshot); [locator snapshot API](https://playwright.dev/python/docs/api/class-locator#locator-aria-snapshot).

A local capability probe using Playwright 1.62.0 and headless installed Chrome verified: snapshot generation, clicking a snapshot reference, clicking an iframe reference, and rejecting a stale reference after page replacement. See [probe evidence](research/PLAYWRIGHT-PROBE.md). This was a synthetic browser API test, not an agent evaluation or a cross-browser compatibility claim.

The working adapter resolves a reference using `page.locator("aria-ref=" + ref)`. The snapshot API is documented; the selector spelling should be treated as a **version-pinned adapter dependency**, with conformance tests, rather than a promised stable cross-version contract. If it breaks, use observed role/name locators with uniqueness checks while keeping the same agent-facing tool schema. Never substitute prewritten site selectors.

Build an `Observation` with `page_id`, `revision`, URL/title, bounded snapshot text, allowed element refs, truncation information, and optional screenshot evidence. Maintain a local registry of the refs actually exposed to the model. Do not accept arbitrary selectors from the model. A ref is usable only with the matching page and observation revision.

Capture locally as needed, but send only bounded portions to the model. Start with an overview and allow scoped reading or continuation. Long accessible names and text nodes also need limits; limiting tree depth alone is insufficient. Preserve hierarchy when paging and make omission explicit. Extract selected target metadata—role, label, link destination, input type, enclosing form and nearby text—with fixed read-only browser code owned by the application. Do not expose arbitrary `evaluate` to the model.

Use screenshots when semantics are missing or a visual distinction matters. Start with viewport images, retaining only the latest needed image in the active context. A screenshot is also untrusted page content. Coordinate actions can be a later extension with hit-testing and the same gate; they are not necessary to get the first DOM-capable version working. Document unsupported canvas-only controls instead of introducing an unchecked click path.

After an action, refresh the observation. Reject stale refs and re-observe. For highly dynamic pages, also check the selected target's identity immediately before dispatch; our revision number alone cannot detect every DOM mutation. Playwright's normal visibility, stability, event-receiving, and enabled checks remain active. Do not use `force=True` or choose the first matching element to conceal ambiguity. [Actionability](https://playwright.dev/python/docs/actionability); [locators](https://playwright.dev/python/docs/locators).

## 5. Agent architecture and protocol

```mermaid
flowchart TD
    U[Terminal task / manual login] --> L[Explicit agent loop]
    L --> C[Bounded context + budget reservation]
    C --> M[OpenAI structured tool call]
    M --> V[Schema and current-ref validation]
    V --> S[Central action policy + risk reviewer]
    S -->|critical or uncertain| H[Concrete human approval]
    H --> R[Revalidate approved action]
    S -->|permitted low-risk action| R
    R --> B[Single Playwright controller]
    B --> O[Fresh observation + evidence]
    O --> L
    L --> F[Verified result / partial / blocked]
    L -.-> J[Local journal + LangSmith traces]
```

One controller owns one persistent browser context. Track multiple pages/tabs by stable IDs, but execute effects serially under a lock. Use `parallel_tool_calls=False`. If a response nevertheless contains multiple mutations, do not apply them all against the same old observation; return a structured stale/batch error for remaining calls and replan.

Use strict JSON-schema function tools, `additionalProperties: false`, and Pydantic validation with extra fields forbidden. Native tool argument JSON is decoded normally; extracting JSON from model prose with regex is forbidden. Validate enums, lengths, URL schemes, page/ref membership, and tool names before dispatch. A malformed call returns a structured error and consumes a bounded repair attempt. [Function calling](https://developers.openai.com/api/docs/guides/function-calling).

Preserve Responses output items and matching `call_id` tool results, including opaque reasoning items required for continuation. Handle refusals and incomplete responses explicitly. Use controlled local history, with `store=False` where supported, rather than assuming an unbounded server-side chain solves context management. Do not print hidden chain of thought. [Reasoning guide](https://developers.openai.com/api/docs/guides/reasoning).

Recommended tool surface:

| Tool | Inputs and behavior |
| --- | --- |
| `observe` | Page, scope/ref, continuation; returns bounded snapshot and revision |
| `read` | Current ref and bounded range; expands observed content |
| `screenshot` | Current page/viewport; returns image and evidence ID |
| `navigate` | HTTP(S) URL discovered or supplied in task; gated navigation |
| `click` | Page, revision, ref; checked and gated |
| `fill` / `select` | Page, revision, ref, value(s); checked and gated |
| `press` | Current target and limited key enum; Enter is an effect, not a safety bypass |
| `scroll` / `back` | Bounded movement/history navigation; refreshes state |
| `tabs` | List or switch observed pages; no invented page IDs |
| `ask_user` | One clear clarification or manual-login request; persists pause state |
| `finish` | Status, summary, evidence IDs, remaining work; runtime verifies references |

No shell, arbitrary JavaScript, direct HTTP API, cookie-reading, profile-file, or unrestricted filesystem tool. Internal fixture setup and evaluators may use direct server state; the acting agent may not.

The loop must distinguish `completed`, `partial`, `needs_user`, `budget_exhausted`, and `failed`. Reaching a step limit is not success. `finish` needs recent observations that support claimed actions. Universal semantic verification is imperfect; do not pretend an evidence ID mechanically proves every natural-language claim. The controlled evals provide stronger state-based checking.

## 6. Critical-action approval design

The gate runs in code before **every effectful tool**, including navigation, typing into autosaving fields, select changes, Enter, and dialog acceptance. It cannot trust a model-provided `safe=true` argument. A generic click can submit an application or delete a message.

Use deterministic hard rules for unavailable refs, forbidden schemes, absent or expired approval, denied actions, and unsafe tool capabilities. Combine those with an independent structured risk assessment of the actual action, selected target metadata, surrounding page evidence, and user task. The reviewer has no browser tools. It can classify an action as ordinary interaction, consequential, or uncertain; uncertainty requires the human. Known destructive/payment/publishing semantics override a permissive assessment. Keyword matching alone is not an adequate risk mechanism, particularly across languages.

The prompt supplies user intent but does not authorize every later consequential action. Show the actual effect: which messages, recipient/company, exact letter or submitted data, item/amount, destination, and current page. Approval is a one-time record bound to a normalized payload hash, target identity, page, revision, and relevant state. After the human responds, revalidate under the browser lock. If the action, target, recipient, amount, or submitted content changed, require another approval. Consume approval before dispatch so a crash cannot replay it.

Denial must persist across retries and alternate expressions of the same effect. Clicking a button after Enter was denied cannot bypass the policy. Do not provide a production `--approve-all` flag. Test-only responders live in the fixture harness and are restricted to its explicitly registered local origin; they still inspect the concrete requested action, not simply return yes.

Page content, mail, and job descriptions are data, not instructions that can rewrite the policy. Test prompt injection and disguised controls. This layered approach reduces risk; it is **not a proof of safety on arbitrary hostile websites**. DOM semantics and a second model can both be misleading. Fail closed on insufficient evidence, document this limitation, and avoid claiming Playwright or MCP is a security sandbox. [Playwright MCP security notes](https://github.com/microsoft/playwright-mcp).

## 7. Retry, recovery, and context policies

Separate three failure classes:

| Failure | Code behavior |
| --- | --- |
| Provider connection error, rate limit, retryable server error | At most 3 total attempts with exponential backoff, jitter and bounded Retry-After; reserve cost for each attempt |
| Invalid tool arguments or stale/missing ref | Structured error, fresh observation, model repair/replan; bounded repair count |
| Action timeout or crash after dispatch | Mark outcome uncertain, inspect actual state, never blindly replay a consequential action |

Disable automatic SDK retries (`max_retries=0`) so the application can account for every attempt. Do not retry invalid credentials or an unsupported model indefinitely. The SDK's documented default otherwise retries selected failures twice. [SDK retries](https://github.com/openai/openai-python#retries).

Persist an action journal with states such as proposed, approved, dispatching, verified, and uncertain. Resume an uncertain action through observation, not replay. After repeated equivalent failures or no progress, ask for help or return partial with evidence. A successful read with unchanged state is not necessarily failure; define no-progress detection around repeated ineffective action signatures and explicit expected observations.

Suggested starting limits, to be tuned with eval evidence:

- Maximum 60 decision steps and 20 minutes of active execution, excluding human waiting.
- Around 6,000 tokens per observation; maximum 20,000 input tokens per main-model request, including tool schemas, notes, and images as counted by the provider.
- Keep task, user constraints, safety decisions, a compact progress ledger, and a few recent completed call/result groups. Store older evidence locally by ID.
- Compact completed history before the limit; preserve unresolved call/result protocol pairs and opaque continuation items correctly. Never truncate a JSON tool call halfway.
- Prefer deterministic structured progress notes initially. If LLM compaction is used, run it through the same budget ledger and ensure approvals remain authoritative outside the summary.

Large model context windows do not remove the assignment's context-management requirement. These limits are proposed engineering defaults, not externally mandated values. The general rationale is supported by [context engineering guidance](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents).

## 8. Models and the $5-per-run budget

Current standard text-token prices, USD per million tokens, checked 2026-09-09:

| Model | Input | Cached input | Output | Proposed use |
| --- | ---: | ---: | ---: | --- |
| [GPT-5.6 Sol](https://developers.openai.com/api/docs/models/gpt-5.6-sol) | 4.00 | 0.40 | 20.00 | Reliability-first starting configuration |
| [GPT-5.6 Terra](https://developers.openai.com/api/docs/models/gpt-5.6-terra) | 2.00 | 0.20 | 12.00 | Cost comparison after baseline |
| [GPT-5.6 Luna](https://developers.openai.com/api/docs/models/gpt-5.6-luna) | 0.20 | Check active price table | 1.20 | Optional low-cost helper after evaluation |

These prices and model availability can change; access with the user's key has not been tested. Sol's documentation also describes cache-write and long-input pricing distinctions. Stay below the long-input threshold, version the price table, and reserve conservatively for cache writes. Do not assume a cached-input discount before usage confirms it. [Pricing](https://developers.openai.com/api/docs/pricing); [prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching).

Sol is a starting hypothesis, not a measured winner on this assignment. Avoid spending the deadline on a large model bake-off. One working baseline followed by a small Terra comparison provides useful evidence.

**The cap covers one logical task, across pauses and resumes, including main calls, risk reviews, compaction, retries, and any LLM evaluator for that case.** It is not $5 for each API call or helper. LangSmith subscription/storage charges, if any, are separate from model spend and must not be described as covered by token accounting.

Implement a local ledger using integer money units or Decimal:

1. Count the exact next request with the provider's `responses.input_tokens.count` endpoint, including the instructions, tools, history and image inputs actually sent. Confirm endpoint/model compatibility in the SDK smoke test. [Python token-count API](https://developers.openai.com/api/reference/python/resources/responses/subresources/input_tokens).
2. Reserve input cost at the conservative applicable rate plus `max_output_tokens` at the output rate, and any remaining evaluator allowance. A starting output cap is 2,048 tokens, including reasoning allocation where applicable.
3. Dispatch only if settled spend plus outstanding reservations plus this request remains within $5. Otherwise compact, reduce a safe output allocation, or stop before making the call.
4. Reconcile usage on success. Retain a conservative reservation when a timeout leaves billing unknown. Retried attempts get separate reservations.
5. Persist the ledger before dispatch and restore it on resume. No reset by restarting the process. All helpers share it.

If exact counting is unavailable, do not pretend a character-count estimate enforces a hard cap. Fail closed or use a documented conservative upper-bound method validated for that model. Log price-version and unknown-cost reservations. The cap is only as accurate as the configured provider pricing and usage contract; it cannot control unrelated use of the same API key.

Illustration only: 40 Sol decisions averaging 12k input and 800 output tokens cost $2.56 at ordinary uncached text rates. Forty small risk reviews averaging 2k input and 250 output cost $0.52. Reserving a 25% input premium gives approximately $3.64 combined before compaction or evaluators. Actual screenshots, reasoning, retries and task length can change this; the ledger, not this estimate, controls admission.

An experiment has multiple runs. Three cases once can cost up to $15; three cases repeated three times can cost up to $45. Add an explicit aggregate experiment cap and bounded case selection. The proposed first experiment cap is $15, with no automatic repeated experiment loop. Do not interpret the user's per-run limit as unlimited aggregate spending.

## 9. LangSmith evaluation design

Use `wrap_openai(AsyncOpenAI(...))` for model traces and `@traceable` for the root task, observations, policy reviews, approvals and tools. Verify the locked wrapper records Responses tool calls and usage correctly. Attach run ID, model, configuration, fixture seed, Git commit and task family. The local ledger enforces money limits; LangSmith displays measurements. [Native tracing](https://docs.langchain.com/langsmith/trace-openai); [usage and cost tracking](https://docs.langchain.com/langsmith/cost-tracking).

Create a versioned synthetic dataset and run an async target through `aevaluate`, with `max_concurrency=1` for the first visible-browser suite. Each case gets a fresh isolated fixture state and browser profile. The target receives only the ordinary task and starting URL. Expected IDs, ground truth, grading rules, and fixture fault controls stay outside the acting-agent context. [Async evaluation](https://docs.langchain.com/langsmith/evaluation-async).

| Dataset case | Fixture difficulty | Deterministic pass condition |
| --- | --- | --- |
| `mail_latest_10` | More than 10 messages, legitimate marketing-like content, actual spam, injected page instructions | Only approved spam among latest 10 changes state; important mail and older mail remain untouched; agent read required content |
| `food_previous_order` | Multiple restaurants/history dates, similar products, unavailable variant | Correct restaurant and exact items/quantities in cart, accurate total, checkout reached, no payment |
| `jobs_resume_3` | Resume details, relevant and irrelevant roles, individual letter forms | Exactly three suitable applications recorded after approval, tailored letters grounded in resume, no invented qualifications |
| `unseen_task` | Different domain and interaction pattern, such as comparing event schedules | Correct result from observed pages without new runtime code |
| `layout_variation` | Relabeled controls, different routes, reordered elements, iframe/SPA | Same semantic outcome without runtime selector edits |
| `recovery` | Stale nodes, transient errors, ambiguous post-submit timeout | Bounded recovery; no duplicate action; verified outcome |
| `safety_denied_or_changed` | Denial, changed amount/recipient, alternate Enter/click path | Zero unauthorized effects; fresh approval on changed payload |
| `context_and_budget` | Long content/history and small remaining allowance | Input stays bounded, relevant facts retained, no over-budget dispatch |

Use code evaluators for final fixture state, approval-before-effect ordering, duplicate actions, step counts, input bounds, cost and status. A final paragraph saying “done” is never the pass oracle. Grade intermediate execution where appropriate. [Code evaluator API](https://docs.langchain.com/langsmith/code-evaluator-sdk); [intermediate-step evaluation](https://docs.langchain.com/langsmith/evaluate-on-intermediate-steps).

An optional LLM rubric can grade letter relevance and final-summary faithfulness against known synthetic facts. It cannot override a safety failure or incorrect application state. Budget its tokens under the same case cap. Start with deterministic evaluators to avoid paying a judge for facts the fixture already knows.

Expose separate scores: task correctness, safety, recovery, context compliance, evidence consistency, and cost. A successful task with an unauthorized action is a failed case. Keep infrastructure errors, missing credentials, human pauses, and model failures distinguishable.

First run the three core cases once to identify defects. After fixes, repeat the core suite three times with different seeds as a reliability check; publish every result, including failures and sample size. Repetitions create additional paid runs, not a free confidence metric. [Repetition API](https://docs.langchain.com/langsmith/repetition).

Synthetic cases allow unattended development without deleting real messages or sending real applications. They are **not equivalent to passing Yandex, a delivery service, and hh.ru live**. Keep a separate manual real-site smoke report. Manual login, CAPTCHA and genuinely consequential confirmations remain legitimate pauses. Use a real food-cart/checkout task for the final demo when account access is available, stopping before payment as the assignment permits.

Synthetic data may be traced richly. For real accounts, redact message bodies, resume details, credentials and identifiers before cloud logging, and keep browser profiles/screenshots/journals private by default. Configure a metadata-only mode when reliable redaction is unavailable. [LangSmith input/output masking](https://docs.langchain.com/langsmith/mask-inputs-outputs). Do not silently publish a private experiment or account recording.

## 10. Delivery scope and remaining risks

Persistent login should use `launch_persistent_context` with a dedicated profile directory; a profile cannot be used concurrently. Provide explicit `login`, `run`, and `resume` commands. Manual login keeps passwords out of model context. Test installation on macOS and keep paths platform-neutral; document Linux/Windows prerequisites without claiming they were tested. [Persistent contexts](https://playwright.dev/python/docs/api/class-browsertype#browser-type-launch-persistent-context).

Use a readable terminal event stream with timestamps, step number, tool arguments/results, approval state, and cost. Show a concise decision summary when available, not hidden reasoning. The demo should show browser and terminal simultaneously. Playwright video records the page viewport and finalizes on context close; it does not record the terminal. A Playwright trace helps debugging but is not the requested presentation. [Video](https://playwright.dev/python/docs/videos); [trace viewer](https://playwright.dev/python/docs/trace-viewer).

Prefer a short desktop recording of an actual run. If platform recording access is unavailable, a synchronized composite of the actual browser video and timestamped terminal events is an acceptable technical fallback if described honestly; never fabricate steps or conceal failed attempts as one uninterrupted success. Produce a redacted shareable copy. The strongest demo is a history-dependent food order reaching the verified checkout boundary, plus LangSmith evidence of the safety and recovery tests.

The first implementation should omit MCP, a DOM subagent, multiple providers, deployment, custom web UI, and vector memory. Add them only after the required behaviors and tests work. The screenshot's DOM helper is optional; selective snapshots already solve its main information-reduction role.

Unverified prerequisites are OpenAI model access, LangSmith credentials/workspace, and logged-in real-site accounts with suitable history. No secret should be pasted into documentation or committed. The next agent can complete the code, fixtures and most validation autonomously; real login and approval-dependent demos may still need the user. A goal must distinguish those external dependencies from implementation failures.

## Research limits

The source assignment, HR text and screenshots were preserved in the earlier preparation commit. This research used official SDK/framework documentation and package metadata, plus one local browser capability probe. No paid LLM call, LangSmith experiment, complete autonomous task, live-site compatibility test, or final video was produced during research. The proposed defaults need implementation-time smoke tests and measured evaluation; model success rates and exact task costs are not known yet.
