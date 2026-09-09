# Browser automation AI agent — test assignment

Preparation repository for an autonomous browser agent. Credentials, locked dependencies and setup probes are ready locally; the autonomous runtime is not implemented yet.

Read [SETUP.md](docs/SETUP.md) first. Default model is **GPT-5.6 Luna**, per the user's latest choice.

Start with the [complete system design](docs/SYSTEM-DESIGN.md): every requirement mapped to implementation, failure handling and acceptance tests, plus graph/data contracts, safety, terminal UX and delivery gates.

Use [FINAL-TEST.md](docs/FINAL-TEST.md) as the ordered final acceptance runbook: setup, safety tests, three tasks, interruptions/recovery, live demo and sign-off. Commands are specifications until implementation exists.

For a single copy-paste document, use [complete context](docs/CONTEXT.md).

Start with [the implementation handoff](docs/HANDOFF.md), [the Russian assignment](docs/assignment.ru.md), and [HR's evaluation criteria](docs/hr-requirements.ru.md). Original reference images and captured page evidence are in `docs/`.

Confirmed: **Python, Playwright, OpenAI API access, LangSmith evals, $5 per task run**. Recommended: LangGraph StateGraph + native OpenAI Responses SDK + Pydantic, with Playwright tools, SQLite checkpoints and a central safety gate. See the [focused LangGraph research](docs/LANGGRAPH-RESEARCH.md) for the revised recommendation and verified checkpoint probe.

Read the [full cited implementation analysis](docs/IMPLEMENTATION-RESEARCH.md) and [execution plan with a copy-paste next-agent goal](docs/IMPLEMENTATION-PLAN.md). The plan includes budget enforcement, state-based evaluations, recovery, approvals and demo acceptance criteria. A tiny Luna setup call has been verified; no agent evaluation has run; a narrow [Playwright snapshot capability probe](docs/research/PLAYWRIGHT-PROBE.md) was verified.

**Branch policy: work directly on `main`; never create a second branch or a separate worktree.**

Target completion: September 10, 2026, end of day. Reported submission deadline: September 11, about 17:00, timezone unconfirmed. Submission requires a repository link and a short video showing one complex task being solved.
