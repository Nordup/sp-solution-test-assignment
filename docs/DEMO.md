# Record the real terminal and browser

Use a native Terminal window beside the visible Playwright-controlled browser, as shown in the three [reference screenshots](assets/ideal-solution-01.jpg). The terminal shows the typed task, actual tool calls/results, exact approval questions and the final result. The retired browser-based console is not part of the solution.

## Synthetic demonstration

Run this command in Terminal from the repository root:

```bash
uv run python scripts/demo_terminal.py --fixture food_previous_order --seed 102 --profile demo-synthetic --budget-usd 5 --release-session final-candidate --viewport-width 640 --viewport-height 620
```

The terminal displays **Synthetic evaluation — local fixture**, then prompts `Task:`. Enter an ordinary task, for example:

> Закажи мне BBQ-бургер и картошку фри из того места, откуда я заказывал на прошлой неделе на этом сайте. Остановись перед финальным подтверждением оплаты; заказ не размещай.

The typed task is forwarded unchanged. The fixture supplies only the starting URL and the isolated browser environment; its reference answers, task script and automatic evaluation approver are not supplied to the runner. The visible browser opens after task entry. The terminal uses the existing CLI's actual Rich output and `human` responder. There is no HTTP control panel.

Each launcher invocation starts fresh synthetic fixture state and a new logical run. The fixture stays alive while the runner waits for a terminal answer. Only its own origin is allowed by the existing fixture browser factory. The profile defaults to `demo-synthetic`; the prepared real-account profile `demo` is rejected by this synthetic launcher. Close any existing holder of the selected profile first.

The existing `final-candidate` release ledger must already exist. The launcher reads that allowance without creating or increasing it; all actor/reviewer/retry calls retain the same $5 logical-task and shared release limits. It makes no model call before task entry. It does not run graders or export a LangSmith evaluation. A synthetic demonstration is separate from the scored acceptance records and must remain labeled synthetic in the recording.

## Terminal interaction and stopping

- At an approval prompt, inspect the exact destination and submitted values. Type `yes` to approve that exact request; any other answer denies it. There is no approve-all mode. Denial stops the run as partial.
- At a clarification prompt, supply only the genuinely missing fact or choice. `/pause` saves the run and exits. A synthetic launcher then closes its fixture: native CLI resume does not reconstruct that server or its origin isolation, so synthetic demonstration resume is unsupported. Answer the current question to continue this run, or deliberately start a new labeled demonstration.
- Ctrl-C triggers normal runner cancellation and cleanup, then closes the fixture. Cancellation does not prove a website effect was rolled back. Inspect the private action journal before any further consequential operation.
- A completed result exits with code 0; partial, needs-user, failed or cancelled results return code 2. An interrupt at the initial prompt returns 130. The displayed result is the runner's actual result, not a successful-outcome template.

The optional viewport dimensions apply to the initial page after browser startup and isolation. Supply both: width 320–3840, height 240–2160. Omitting both preserves the default. They persist on that page through navigation but do not configure new tabs/popups or resize the native window. Tile and inspect the actual windows separately.

## Existing live account

For a new live task, use the existing CLI directly in Terminal:

```bash
uv run browser-agent run --profile demo --url https://eda.yandex.ru/ --budget-usd 5 --release-session final-candidate
```

For the already saved Yandex task, resume its existing logical run and ledger:

```bash
uv run browser-agent resume 9ad93502-a357-41e0-b888-b92413f295a5
```

Reuse the prepared profile and close any existing holder. Do not copy cookies or create another login unnecessarily. The saved task is paused for a genuine delivery address. Earlier authenticated history was dated April 2025; that does not establish previous-week history or a completed food task. Label any historical-task adaptation accurately. Automatic live-account LangSmith tracing remains disabled. Private account content can appear in the terminal and browser; review the recording before sharing.

## Recording and review

1. Open a real Terminal window and prepare the command. Start screen recording before entering the task; if a setup segment is excluded, identify that honestly.
2. Tile the controlled browser and terminal side by side once the browser opens. Keep both readable, with the synthetic/live distinction visible.
3. Show actual exploration, proposed tools, browser changes and exact approvals. Answer necessary questions without supplying navigation scripts or coaching every step.
4. Verify the final report against the browser and stop before payment/order placement. A partial or paused task must remain labeled incomplete.
5. Stop the recorder, inspect the complete video for privacy and correctness, and publish only a reviewed shareable copy. Recorder commands are in [SETUP.md](SETUP.md).

Runtime events have their existing per-event terminal display limit; full private events remain in the run's `events.jsonl`. Do not replace missing video steps with a fabricated trajectory or publish credentials, browser profiles or raw private artifacts.

## Verification

```bash
uv run ruff check scripts/demo_terminal.py tests/test_demo_terminal.py
uv run pytest tests/test_demo_terminal.py -q
```

These tests verify terminal argument/result forwarding, the real CLI approval and clarification responder, fixture lifetime during answers and cancellation, existing-release admission, and actual Playwright viewport/network isolation. Paid behavior, full task success and the final video still require their separate evidence.
