# Terminal and browser demo

Run the actual agent in a native terminal, then tile its controlled browser beside it, matching the three original screenshots.

[Recorded synthetic demonstration](assets/synthetic-agent-demo.mp4): 2 minutes 20 seconds, real Terminal and Playwright browser, with exact human approvals. Run `e5d9d3fb-b612-4fe9-a563-38184157dc52` reached Payment review with the correct items and 315,000 VND total, without placing/paying for an order; 10 decisions, $0.018717.

```bash
uv run python scripts/demo_terminal.py --fixture food_previous_order --seed 102 --profile demo-synthetic --budget-usd 5 --viewport-width 600 --viewport-height 620
```

The terminal labels this **synthetic** and prompts for a task. The local fixture supplies the starting URL and isolated browser environment, not a procedure or expected answer. Enter:

> Тестовый сценарий: 9 сентября 2026. Закажи мне BBQ-бургер и картошку фри из того места, откуда я заказывал на прошлой неделе. Остановись перед финальным подтверждением оплаты; заказ не размещай.

After task entry the launcher opens the browser and waits once, so you can arrange both windows and start recording before the first model decision. Press Enter to begin. Show actual exploration, tool arguments/results, exact approval prompts, the final browser state and the agent's report. Type `yes` only after inspecting the exact requested action. The fixture runs the same LangGraph actor as the CLI; the demo does not use the evaluator's automatic approver.

The published video is one continuous capture at normal speed, resized to 1920×1200. Only excess idle footage after the final report was trimmed. The capture filter included just the prepared Terminal and synthetic browser windows; no audio, unrelated apps or real account data. The browser closes naturally at task completion. The earlier recording that stopped before payment review remains private as a failed attempt.

Stop before order placement/payment. Inspect the whole recording before sharing, and keep its synthetic label visible. A fixture video does not establish live Yandex compatibility.

For a real account, use `browser-agent login` and `browser-agent run` with a dedicated persistent profile. The existing `demo` profile is reserved for the user's live session. The synthetic launcher rejects that profile; it uses `demo-synthetic` by default.

The user has prepared native Terminal session `sp-assignment-demo` on this machine. Recording utilities and raw captures remain local under ignored `artifacts/final/`. The old checkpoint-based live run cannot be resumed by the simplified runtime; inspect the live account and supply genuine missing information before starting a new task. No live order has been completed.
