# Technical choices

The initial investigation was too broad for a two-day assignment. The implemented direction was narrowed on September 10 at the user's request, while retaining their explicit LangGraph choice.

- **LangGraph:** a small graph makes observation, decisions, approval, execution and recovery explicit. Browser/client objects stay outside graph state. No hosted graph service or checkpoint database is needed for the requested persistent browser login. [Graph API](https://docs.langchain.com/oss/python/langgraph/use-graph-api).
- **Native OpenAI SDK:** strict function calls provide typed tool arguments directly. Adding another agent runner or runtime model-review chain would duplicate responsibilities.
- **Playwright:** visible execution, persistent profiles and browser-resolved references suit the required workflow. Manual login keeps password entry outside model tools. [Persistent contexts](https://playwright.dev/python/docs/api/class-browsertype#browser-type-launch-persistent-context).
- **Bounded snapshots and notebook:** keep recent evidence and task progress small, with scoped reads for long content. This addresses the assignment's context requirement without a separate summarizing model or evidence-packing protocol.
- **LangSmith:** record isolated synthetic cases and observable outcomes. A fixture state check is stronger evidence of a completed action than the actor saying it succeeded. [Code evaluators](https://docs.langchain.com/langsmith/code-evaluator-sdk).
- **Terminal + screen recording:** show the task, actual tools and browser side by side. Playwright viewport video alone would omit the required terminal interaction. [Playwright video scope](https://playwright.dev/python/docs/videos).

These links were used during the original research. The source assignment, HR requirements, screenshots and captured pages are preserved in this repository. Earlier speculative designs and experiments remain in Git history; current behavior and limitations are described in SYSTEM-DESIGN.md and VALIDATION.md.
