# Recording the actual agent console

`scripts/demo_console.py` is an optional local browser interface to the existing `run_agent` runner. It shows the runner’s actual Rich console stream, accepts an ordinary task, and displays the exact live approval or clarification request. It is explicitly labeled **Agent console**, not a native Terminal. No canned trajectory, automatic approval or hidden task script is used. This recording interface does not replace the CLI or certify an evaluation pass.

The runtime, model, headed Playwright browser, profile lock, $5 task limit, release ledger, checkpointing and safety gate are unchanged. The console server itself makes no model call until a user submits a task. The existing release session must already exist; the interface never creates or raises its allowance. If it is exhausted, the agent cannot start another paid call.

## Synthetic recording

Use this when recording a clearly labeled fixture demonstration:

```bash
uv run python scripts/demo_console.py --fixture food_previous_order --seed 102 --profile demo-synthetic --budget-usd 5 --release-session final-candidate
```

Open the private `http://127.0.0.1:PORT/#TOKEN` link printed by the script in a supported browser. The session token stays in the URL fragment until the page reads and removes it; it is not sent as a URL query or included in access logs. Reopen the original link after reloading the page. Keep the link private and out of the published recording.

The evaluator-owned fixture starts before the console and remains available until the script exits, including while the agent waits for answers. It supplies only its starting URL to the runner; reference answers, route maps and expected IDs are not passed to the actor. The fixture browser permits requests only to that fixture’s origin. The interface prominently labels the run **Synthetic evaluation — local fixture**. Its separate profile avoids using the prepared real-account profile.

Type a short task yourself, for example:

> Закажи мне BBQ-бургер и картошку фри из того места, откуда я заказывал на прошлой неделе на этом сайте. Остановись до оплаты.

This wording is a labeled demonstration prompt. Preserve the exact source prompts and separate scored evaluation records for acceptance. The demo console does not run graders or export a LangSmith evaluation; its synthetic label is not a task-pass claim. Restarting the script creates fresh fixture state; clicking Start again in the same server reuses the existing fixture state. Never present a reused state as a fresh evaluation.

## Existing live account

Close the current browser process holding `artifacts/profiles/demo` before the runner opens it. Reuse the prepared profile; do not create fresh logins or copy cookies:

```bash
uv run python scripts/demo_console.py --url https://eda.yandex.ru/ --profile demo --budget-usd 5 --release-session final-candidate
```

Use the actual prepared service URL if it differs. This does not establish that the live account or runtime works: verify the current browser state. Previous read-only Yandex Eda checks showed authenticated order history from April 2025, without a visible challenge; they did not establish previous-week history or restart persistence. An adapted historical task must be labeled honestly. Automatic real-account LangSmith tracing remains disabled by the existing runner. Actual private account content can appear locally in the console and browser, so review and redact the recording before sharing.

## Viewport for a tiled recording

If the browser window is tiled to half the screen, its default Playwright viewport may be wider than the visible area. Supply both optional dimensions to size the initial agent page, for example:

```bash
uv run python scripts/demo_console.py --fixture food_previous_order --seed 102 --profile demo-synthetic --budget-usd 5 --release-session final-candidate --viewport-width 640 --viewport-height 620
```

Width must be 320–3840 pixels and height 240–2160. Omitting both preserves the existing default. The console displays the selected initial viewport. The wrapper waits for the original browser startup and fixture isolation, then resizes the initial page before the runner proceeds; it does not change the safety gate, task, profile or budget. The setting remains on that page during navigation, but does not configure later tabs/popups or resize the native window. Arrange the native window separately and inspect that the whole page fits before recording. This option is for console-launched tasks; it does not alter native CLI resume behavior.

## Recording and interaction

1. Open the console link, then arrange this browser console beside the agent’s headed browser. The latter opens after Start task. Start the screen recording before entering the task if possible, or explicitly identify any setup segment excluded from the recording.
2. Enter the ordinary task and click **Start task**. Only one runner can be active in this console. It uses the CLI-selected URL, profile and release session; task text is never interpreted as a shell command.
3. Watch actual tool calls and browser changes. The console mirrors the runtime’s existing event formatting, including its per-event display limits; private `events.jsonl` retains the runtime event records. The display retains a bounded recent output window and explicitly labels earlier omitted output.
4. If asked, inspect the full concrete request. **Approve this exact action** sends its request ID and an explicit boolean approval to the existing runner. **Deny** sends an explicit denial. Stale, mismatched or duplicate answers are rejected; no approve-all control exists. Ordinary clarifications use the separate reply form.
5. **Save and pause** is available at a pending question and returns the runner’s existing `needs_user` boundary. A paused task is not complete. Resume its saved run through `uv run browser-agent resume RUN_ID`; starting another console task creates a new logical run, not a resume.
6. Verify the final report against the browser. Stop before final payment unless the actual exact action was explicitly authorized. The console displays the actual returned result, including partial, needs-user or failure states.
7. Stop and inspect the complete recording. Publish only a reviewed shareable copy showing both the console and actual browser, with the synthetic/live distinction visible. Recorder setup is in [SETUP.md](SETUP.md).

Ctrl-C closes the server and cancels its active runner through the runner’s existing cleanup. Cancellation does not mean an in-flight website action was rolled back; inspect the saved journal and resume the same run before retrying any consequential operation. Closing the console tab alone does not cancel a running task; reopen its private link to continue. Stop the script when finished.

The HTTP server binds only to `127.0.0.1`. State reads require the unpredictable session bearer token. Mutations additionally require the exact loopback Host/Origin, a separate CSRF token, bounded JSON payloads and the pending-question binding. Page content is rendered as text, scripts/styles use CSP nonces, and no external assets are loaded. Credentials/environment configuration are not exposed as API fields. This is a private local control surface, not a deployable multi-user web service.

## Verification

```bash
uv run ruff check scripts/demo_console.py tests/test_demo_console.py
uv run pytest tests/test_demo_console.py -q
```

The tests exercise actual localhost HTTP admission, single-flight execution, exact approval/denial/replay rejection, clarification/pause delivery, exception-value redaction and cancellation using a fake runner. A real Playwright UI test checks authenticated polling, actual streamed output and approval-button binding without model calls. An actual Playwright fixture test checks the selected initial viewport, reload persistence and continued external-request isolation; validation rejects incomplete/out-of-range dimensions and the default factory stays unchanged. These tests verify the optional console; they do not substitute for the runtime acceptance suite, live compatibility or the final video review.
