# Working on this repository

## Read before changing behavior

1. [Assignment](docs/assignment.md): the preserved employer specification, including all three task pages and reference screenshots.
2. [Evaluation criteria](docs/evaluation-criteria.md): the preserved HR clarifications.
3. [Architecture](docs/architecture.md): how the current implementation works and where its limits are.
4. [Acceptance tests](docs/acceptance-tests.md): the ordered checks required before handoff. [Testing](docs/testing.md) records the available evidence.

Use the assignment and HR criteria as the source of truth for requirements. Read their local contents before making implementation or scope decisions. External links identify the original sources. Architecture notes describe the current implementation; test results establish only the behavior exercised.

## Keep the scope clear

Preserve the Russian source wording, nested task text, and reference images. Keep the detailed acceptance runbook when cleaning documentation. Update commands and evidence as the implementation changes; do not remove an unmet requirement to make the project appear complete.

Keep the browser actor general. Acceptance steps are instructions for the human tester, not workflows to inject into the runtime prompt. Do not add site-specific routes, selectors, or task recipes to production code.

Use the existing Python, LangGraph, Playwright CLI, and Luna implementation. Keep the actor at max reasoning, the reviewer at medium, and the shared model budget at no more than $5 per task. Favor small changes that serve the assignment.

## Verify changes

Follow the quick start in [README](README.md) for installation and [Testing](docs/testing.md) for automated checks. For submission, follow [Acceptance tests](docs/acceptance-tests.md) in order and record failures, blockers, and unrun checks. Keep documentation consistent with observed behavior. Keep credentials, browser profiles, and private run artifacts out of Git.
