# Validation

Validated on macOS with Python 3.12, LangGraph, headed Playwright and OpenAI `gpt-5.6-luna`, September 10, 2026. Synthetic accounts and locally generated routes keep evaluation expectations outside the runtime.

| Check | Evidence |
|---|---|
| Focused tests | `uv run pytest -q`: 54 passed in 27.26s. Final-report detail propagation also passed its focused regression after the last rendering change. |
| Lint and setup | Ruff, locked dependency setup, doctor and CLI interfaces passed. |
| Mail | Run `be384303-67f7-44f4-9282-a797cdffcdce`: all ten bodies read, exactly three spam messages removed, other messages retained; 23 decisions, $0.059175. Follow-up `767c4ac5-1088-406a-9ebc-14a151ab30fc` also passed in 19 decisions, $0.042904. Its final notebook names all seven legitimate retained messages. |
| Food | Run `4fb9e14a-11d1-4deb-85c3-34aec921fd9d`: restaurant discovered from history, correct regular items, final payment review reached at 315,000 VND, no payment; 9 decisions, $0.015157. |
| Jobs | Run `a27a7384-1926-4d53-b930-fbd72ddd7a57`: resume read before applying, three suitable jobs, three distinct letters, exact approvals; 14 decisions, $0.030998. All submitted letters were manually checked against the resume and their own job descriptions: grounded and personalized. |
| LangSmith | All three core runs read back: outputs match local results, dataset examples linked, 20 state-check feedback scores passed. Manual letter-quality feedback also uploaded. |
| Terminal/browser video | [Reviewed synthetic demo](assets/synthetic-agent-demo.mp4), run `e5d9d3fb-b612-4fe9-a563-38184157dc52`: correct items and total, final Payment review, three exact approvals, no final payment action; 10 decisions, $0.018717. Full recording decoded successfully and timeline/frames inspected. |
| Public repository | Staged files passed configured-secret, private-path, documentation-link and whitespace checks. Original Russian sources and all three reference screenshots are unchanged. Work remains on `main` only. |

## Requirement coverage

- **Autonomy and universal navigation:** one actor chose each action from current browser observations; randomized fixture paths and selectors are confined to evaluation setup. The runtime contains no mail/order/job workflow or expected answers.
- **Context and tools:** bounded accessibility snapshots, scoped/paged reads, ten recent tool exchanges and a required cumulative factual notebook. Navigation, forms, tabs, scrolling, screenshots and user questions are available as strict native tools. Final factual notes accompany the short report.
- **Critical actions:** focused real-browser tests cover exact approval, denial, changed form values and no replay after uncertain effects. Synthetic runs approved only exact permitted fake-account actions. Generic search/menu controls do not require repeated permission.
- **Recovery:** tests cover stale references, temporarily disabled controls, provider counting/generation retries with bounded backoff, retry exhaustion, cancellation and manual login/challenge pauses.
- **Persistent sessions:** real-browser tests exercise saved profiles and exclusive profile ownership. Password values and credentials are excluded from model tooling.
- **Structured calls:** Pydantic validates native function arguments, including required bounded memory. No regex extraction of JSON from model prose.

The successful core runs used at most 11,585 input tokens per model call (cap 20,000) and 1,076 notebook characters (cap 6,000). The actor retained the original request, current page, recent results and prior source facts while completing all three workflows. Combined model cost of that three-case run was $0.105330, below the $5 cap for each task.

## Failures and fixes

The initial simplified run failed all three tasks because three recent exchanges and an optional notebook let the actor forget progress. Keeping more history helped, but the actor still ignored the memory tool. A required factual notebook on every tool call fixed repeated reading and duplicate additions without introducing another model or checkpoint system. All retained simplified evaluation attempts, including failures, are listed in [EVALUATION-RESULTS.md](EVALUATION-RESULTS.md).

The first recording stopped one page before payment review. The prompt and finish-tool description were clarified to distinguish noncommitting preparation from final commitment. The short mail summary sometimes omits retained item names even though the final notebook contains them; the terminal report now includes those factual notes as details. These are practical fixes, not a guarantee that every model run will succeed.

## Limits

These results establish the three controlled task behaviors, not compatibility with every live service. The prepared Yandex session previously needed a genuine delivery address; useful last-week order history and a complete live food task remain unverified. Login/security challenges require manual handling; the agent does not bypass them.

Unknown JavaScript actions are conservatively confirmed. Native dialog/file/password handling may require manual intervention. Browser login persists, but arbitrary program checkpoint resume is intentionally out of scope. Windows support is not certified. No real orders, payments, emails or applications were submitted during these synthetic validations.
