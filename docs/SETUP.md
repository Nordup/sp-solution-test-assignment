# Setup

The machine already has `.env.local` with project-specific OpenAI and LangSmith credentials. It is ignored by Git and has mode `0600`. Reuse it; do not generate replacement keys or copy its contents into logs.

- OpenAI project: `sp-solution-test-assignment`.
- Model: `gpt-5.6-luna`, selected by the user. Do not upgrade or purchase credits automatically.
- LangSmith project: `sp-solution-test-assignment`.
- LangSmith dataset: `sp-solution-acceptance-v1`.
- Python 3.12 and locked dependencies; Playwright Chromium installed.
- Work directly on `main`.

```bash
uv sync --frozen
uv run playwright install chromium
uv run browser-agent doctor
```

On a new machine, copy `.env.example` to `.env.local`, fill in credentials locally, and set `chmod 600 .env.local`. The application explicitly loads `.env.local`.

## Manual login

```bash
uv run browser-agent login --url https://eda.yandex.ru/ --profile demo
```

Log in manually, then press Enter in the terminal to close and save the profile. Do not open two processes on the same profile. The existing `demo` profile contains the user's prepared session; avoid repeated logins or copying cookies between browsers.

Earlier live Yandex checks confirmed authenticated access, but the attempted food task stopped for a genuine delivery address. Visible history was dated April 2025, which does not establish last-week history. That live task has not completed. Do not invent the address or claim a live-site pass.

## Recording

Place the actual agent Terminal and its controlled browser side by side, matching the three source screenshots. Record the prompt, browser exploration, exact approvals and final result. Review the whole recording before sharing. Local recorder utilities, account evidence and raw recordings remain in ignored `artifacts/`.

Setup of credentials and browser access predates the simplified runtime. See [VALIDATION.md](VALIDATION.md) for which current checks have actually run.
