# Project instructions

- Work directly on `main`. Never create a second branch, feature branch, or separate worktree. This is the user's explicit instruction, 2026-09-09.
- Read `docs/SETUP.md` first: reuse the private `.env.local`, existing OpenAI/LangSmith projects and locked dependencies. User now chose `gpt-5.6-luna` and will add credits later; do not silently upgrade models or purchase credits.
- Read `docs/SYSTEM-DESIGN.md` as the current engineering specification, then `docs/HANDOFF.md`, `docs/assignment.ru.md`, and `docs/hr-requirements.ru.md` before implementation. Review all three images in `docs/assets/`.
- Speak with the user in English. Preserve Russian source quotations exactly, including source typos.
- Current stage: runtime implementation and validation in progress. Read docs/VALIDATION.md for actual evidence; do not infer release readiness from implemented mechanisms. Before implementation read `docs/SYSTEM-DESIGN.md` and `docs/IMPLEMENTATION-PLAN.md`; research documents explain rationale. User chose Python, OpenAI API access, and LangSmith evaluations. Recommended baseline: LangGraph StateGraph + SQLite checkpoints + native OpenAI Responses SDK + Pydantic + Playwright. Read `docs/LANGGRAPH-RESEARCH.md` for the latest orchestration recommendation; follow the documented plan when implementation is requested.
- Enforce the user's $5 cap per logical task run, including helpers, retries and any LLM evaluators; persist the ledger across resume. Bound aggregate evaluation experiments separately.
- Use Playwright for browser automation (user decision). Runtime models must satisfy the assignment's Claude/OpenAI requirement; coding-assistant recommendations are a separate matter.
- Build a universal agent: no site-specific workflows, hardcoded site selectors, or hidden task-specific navigation hints. Evaluation expectations must not be fed to the runtime as scripts.
- Keep browser profiles, credentials, private chat evidence, and private account data out of Git. `docs/private/` is local-only.
- Implement and pass the ordered acceptance runbook `docs/FINAL-TEST.md` before final submission. Its proposed commands must become real tested commands; missing/skipped tests are not passes.
- Document actual implemented behavior, validation results, limitations, and architecture decisions. Do not claim unrun evaluations passed.
