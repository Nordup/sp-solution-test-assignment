# Project instructions

- Work directly on `main`; never create a branch or worktree.
- **Scope correction, user 2026-09-10:** this is a two-day test assignment. Implement the original assignment and HR criteria, without adding production infrastructure or model-review layers. The former elaborate design and release gates are superseded.
- Read `docs/SETUP.md`, `docs/assignment.ru.md`, `docs/hr-requirements.ru.md`, and `docs/SYSTEM-DESIGN.md`. Review the three original images in `docs/assets/`.
- Use Python, LangGraph, Playwright, native structured OpenAI calls, `gpt-5.6-luna`, and LangSmith evaluations. The user explicitly reaffirmed LangGraph on 2026-09-10; keep a small StateGraph for the single actor loop. No runtime memory/clarification/endpoint/factual/risk reviewer models or SQLite checkpoint engine.
- Show a real terminal beside the visible browser, as in the reference screenshots. Persistent browser profiles support manual login.
- Keep context bounded with the current snapshot, recent tool results, and a small actor-maintained notebook. Implement actual bounded retry/replanning, and pause after an uncertain consequential action instead of replaying it.
- Show exact browser-resolved targets and form values for critical-action approval. Revalidate the target before execution. Denial stops the task. Never offer an approve-all production mode.
- Preserve the user's $5 maximum per task, including retries and any model evaluation. Reuse `.env.local` and the existing OpenAI/LangSmith projects; do not buy credits or upgrade the model.
- No site-specific runtime workflows, selectors, routes, or expected-answer hints. Keep fixture setup/answers in evaluation code.
- Speak English; preserve Russian source text exactly. Keep credentials, profiles, private chat/account data and raw run artifacts out of Git.
- Use the small requirement-focused runbook `docs/FINAL-TEST.md`. Test the three source tasks and meaningful failure paths. Do not recreate hundreds of adversarial tests or rerun unrelated suites after every edit.
- Record actual results and limitations in `docs/VALIDATION.md`; never claim unrun tests or synthetic runs establish live-site compatibility.
