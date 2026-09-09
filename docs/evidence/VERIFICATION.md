# Capture verification

Captured 2026-09-09 from [the employer's Craft assignment](https://kolbasa.craft.me/ai_test_task), using Playwright CLI in an isolated browser session.

## Coverage

- Expanded all four implementation groups and “Как выглядит идеальное решение”.
- Opened each linked task page: spam removal, food ordering, and job applications.
- Extracted rendered article HTML/text, preserving Russian punctuation, quoted prompts, and source typos (including “Наприер”, “подъодящие”, and “делился результатам”).
- Compared every retained heading/paragraph against `assignment.ru.md`: **77 text blocks, zero missing**. Comparison ignores Markdown emphasis/code delimiters.
- Converted disclosure groups to nested Markdown lists; converted task cards to links and appended their complete contents.
- Downloaded all three candidate screenshots directly from their rendered image URLs. Original files remain unedited. `sources.json` records original URLs and SHA-256 checksums.
- Visually inspected the main assignment capture, nested task captures, and all three reference images.
- HR evaluation message was read in Telegram using Computer Use, then checked against both full HR messages pasted by the user. Telegram pointer/scroll actions returned `AXError.notImplemented`; the first message therefore comes from user-provided text, not a claimed successful screen extraction.

## Files

- `assignment-full.png`: entire retained assignment section, including expanded requirements and references. Craft uses internal scrolling; viewport height was enlarged for this capture to avoid clipped offscreen content.
- `task-spam-full.png`, `task-food-full.png`, `task-jobs-full.png`: nested source pages.
- `../assets/ideal-solution-01.jpg` through `03.jpg`: original supplied examples.
- `sources.json`: provenance and image hashes.

The Russian assignment intentionally omits “С чего начать”, “Немного о нас”, “Аналог решения”, “Полезное”, closing encouragement, author metadata, and navigation/reaction controls. The useful engineering expectations from HR remain in their own source document. Source highlighting is represented as bold; disclosure layout becomes Markdown nesting. Markdown is a semantic transcription, not a pixel-exact page replica.

Raw HTML/text and full private HR messages are retained in Git-ignored `docs/private/` for local auditing. They are not required to read the public handoff. The combined `docs/CONTEXT.md` is generated from the handoff and both public source documents; update it whenever those documents change.

This verifies the original context capture only. At capture time, implementation and evaluations had not started. Current runtime and evaluation evidence are recorded in [VALIDATION.md](../VALIDATION.md).
