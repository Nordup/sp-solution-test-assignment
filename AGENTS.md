# Project instructions

- Work directly on `main`. Never create a second branch, feature branch, or separate worktree. This is the user's explicit instruction, 2026-09-09.
- Read `docs/HANDOFF.md`, `docs/assignment.ru.md`, and `docs/hr-requirements.ru.md` before implementation. Review all three images in `docs/assets/`.
- Speak with the user in English. Preserve Russian source quotations exactly, including source typos.
- Current stage: context preparation only. Discuss unresolved SDK/model/language choices with the user before implementing.
- Use Playwright for browser automation (user decision). Runtime models must satisfy the assignment's Claude/OpenAI requirement; coding-assistant recommendations are a separate matter.
- Build a universal agent: no site-specific workflows, hardcoded site selectors, or hidden task-specific navigation hints. Evaluation expectations must not be fed to the runtime as scripts.
- Keep browser profiles, credentials, private chat evidence, and private account data out of Git. `docs/private/` is local-only.
- Document actual implemented behavior, validation results, limitations, and architecture decisions. Do not claim unrun evaluations passed.
