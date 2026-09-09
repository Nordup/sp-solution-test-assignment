# Complete implementation context

Combined copy of the handoff, Russian assignment, and public HR requirements. Prepared 2026-09-09. Source documents remain separately available for convenient reading.


---

<!-- Source: HANDOFF.md -->

# Implementation handoff

Prepared 2026-09-09. This repository contains source material and planning context only. No agent implementation or evaluation run has been completed.

## Read first

1. [Assignment in Russian](assignment.ru.md): complete retained task text, expanded requirements, ideal-solution description, and all three nested example tasks.
2. [HR evaluation clarification in Russian](hr-requirements.ru.md): engineering priorities and concrete rejection reasons.
3. [Reference 1](assets/ideal-solution-01.jpg), [reference 2](assets/ideal-solution-02.jpg), [reference 3](assets/ideal-solution-03.jpg): original downloaded screenshots supplied by the employer.
4. [Capture verification](evidence/VERIFICATION.md): source provenance, coverage, and limitations.

## User decisions and schedule

Source: user instructions, 2026-09-09; two-day turnaround also confirmed in the HR message supplied by the user.

- Public GitHub repository; work directly on `main`. **Never create a second branch or a separate worktree.**
- Browser automation: **Playwright**.
- Communicate in English; source documents may remain Russian.
- Prepare context first, then discuss implementation choices with the user. Do not treat this handoff as approval of a particular language, SDK, provider, model, or architecture.
- Reported employer turnaround: two days; Friday, 2026-09-11, about 17:00. Deadline timezone is unconfirmed.
- User's target: finish Thursday, 2026-09-10, by end of day. User's current local timezone is Asia/Ho_Chi_Minh; this does not establish the employer's deadline timezone.
- Deliver a repository link and a short video of the agent actually solving one complex task. The assignment does not specify repository visibility; public visibility is the user's choice.

## What the employer expects

A user enters a task in a terminal or separate window while a visible browser is open. The agent observes the page, chooses actions dynamically, calls tools, sees their results, and continues across pages until done or user input is required. Manual login must work through a persistent browser session.

Use Claude or OpenAI models. SDK, programming language, extraction strategy, tool architecture, dynamic-page handling, and MCP use are otherwise open choices. Do not confuse the page's historical coding-assistant setup recommendations with permission to use any runtime model.

The runtime cannot receive entire pages indiscriminately: implement an explicit token/context strategy. The written assignment asks for at least one advanced pattern (subagents, adaptive error recovery, or security confirmations). HR clarification additionally stresses both reliable critical-action confirmation and real programmatic retry/recovery; treat those as acceptance priorities rather than optional polish.

No predefined task execution scripts, prewritten site selectors, or site-specific hints about routes/button labels. An agent-generated plan can evolve from observations; an engineer-supplied spam/order/job workflow is forbidden. Discover selectors or element references from the live page.

Structured LLM/tool interaction is essential. HR specifically calls out regex extraction of JSON as a rejection reason. Use native structured tool calls with schema validation. Recovery must exist in code and permit strategy changes, not merely be requested in a prompt. Documentation must match actual behavior.

MCP absence or limited provider coverage alone was not disqualifying in previous submissions. Supporting one permitted provider well can be discussed; do not assume both are mandatory.

## Reference interaction pattern

The employer's images show browser and terminal side by side. The terminal contains the user prompt, tool calls with arguments/results, page analysis, and a final evidence-based summary. The examples visibly include `navigate_to_url`, `take_screenshot`, `query_dom`, `click_element`, `type_text`, and a DOM subagent. Those are observations from another candidate's demo, not mandatory names or an SDK prescription.

The three images show discovering controls, entering a search, adding an item, and verifying cart state. Reproduce the clarity of the demo and the autonomous behavior. Do not copy selectors or restaurant-specific actions from the images into the implementation.

## Proposed evaluation plan — not employer-supplied tests

These are candidate acceptance checks derived from the source tasks and HR message. The source calls them examples; it does not promise they are the complete hidden evaluation suite. Keep these expectations outside runtime prompts and tool implementations.

| Scenario | Setup and exact prompt | Evidence required for a pass |
| --- | --- | --- |
| Spam | Logged-in Yandex Mail; `Прочитай последние 10 писем в яндекс почте и удали спам` | Read the latest 10 inbox messages; identify spam from sender, subject, and content; request confirmation before deletion; remove/mark only approved spam; verify changed state; accurately report removed spam and important mail retained. |
| Food | Logged-in delivery account with relevant order history; `Закажи мне BBQ-бургер и картошку фри из того места, откуда я заказывал на прошлой неделе на сайте [...]` | Resolve the restaurant from history or clarify ambiguity; distinguish similar items; add the requested burger and fries; verify cart; reach checkout; stop before final payment (explicitly allowed by the source). |
| Jobs | Logged-in hh.ru profile with resume; `Найди 3 подъодящие вакансии AI-инженера на hh.ru и откликнись на них с сопроводительным, предварительно изучив резюме в моём профиле` | Read resume first; find three relevant positions; inspect their requirements; draft individualized letters grounded in the resume; apply only with appropriate user authorization; verify each submission and report outcomes truthfully. Source typo “подъодящие” preserved. |

Do not execute real deletions, purchases, or job applications merely to prepare this repository. Later evaluation should use controlled data/accounts or explicit authorization. A run that stops before a required application/deletion has not passed the complete scenario; report the boundary accurately.

Suggested cross-cutting checks:

- A novel task and changed page layout work without site-specific logic.
- Human login persists, and the agent resumes after clarification.
- Long pages and long histories remain within an explicit context budget.
- Stale elements, navigation timeouts, transient provider failures, and validation errors produce bounded retries or a changed strategy; no infinite loop or duplicate consequential action.
- Denied critical actions do not execute; changed action details require fresh approval; a generic initial prompt does not bypass the confirmation layer.
- Tool results and observable page state support the final answer; incomplete work is reported as incomplete.
- Repository instructions reproduce the observed demo on a clean setup.

## Decisions to discuss next

- Language and runtime: Python versus TypeScript, based on SDK ergonomics and the two-day timebox.
- SDK and provider: native OpenAI/Anthropic SDK versus an orchestration library; required structured tools, validation, retry control, and observability.
- Page representation: bounded DOM/accessibility extraction, element handles/references, screenshot use, and refresh after mutations.
- Context policy: page budget, conversation compaction, retained task state, and evidence storage.
- Recovery and security: action classification, concrete approval payloads, retry budgets, idempotency safeguards, and failure reporting.
- Whether a DOM subagent adds enough value to justify its complexity; it is shown in the reference, but not mandatory.
- Demo scenario and controlled evaluation setup; capture browser and terminal together.
- Confirm employer deadline timezone if necessary. Both relevant HR messages are captured. The full local-only text is in `docs/private/hr-messages.ru.md`.

Record decisions with rationale and tradeoffs as they are made. Keep this handoff current and replace proposed checks with actual evaluation results only after execution.


---

<!-- Source: assignment.ru.md -->

# Тестовое задание: AI-агент для автоматизации браузера

> Source: [original assignment](https://kolbasa.craft.me/ai_test_task). Captured 2026-09-09. Russian wording, punctuation and source typos are preserved. Expandable sections are represented as nested lists; task cards are links, with their full text below. Setup advice, company background, resource promotions, author metadata and site controls are omitted as requested.

### Задача

**Разработать AI-агента, который автономно управляет веб-браузером для выполнения сложных многошаговых задач.**

### Требования к решению

Должен открываться браузер и должна быть возможность написать агенту (можно в отдельном окне или в терминале). Агенту можно отправить сложную задачу текстом и смотреть, как он решает её в браузере. Агент должен работать полностью автономно, пока не потребуется дополнительная информация от пользователя или задача не будет выполнена.

Вот примеры некоторых задач, с которыми должен справляться агент:

- [✉️ Удаление спама](https://kolbasa.craft.me/ai_test_task/b/8B8AEE1D-3A86-4E9D-915C-1944B12B021B/%E2%9C%89%EF%B8%8F-%D0%A3%D0%B4%D0%B0%D0%BB%D0%B5%D0%BD%D0%B8%D0%B5-%D1%81%D0%BF%D0%B0%D0%BC%D0%B0)
- [🍔 Заказ еды](https://kolbasa.craft.me/ai_test_task/b/02731FDF-25EE-4BA7-B09C-DD1A5A9BC20E/%F0%9F%8D%94-%D0%97%D0%B0%D0%BA%D0%B0%D0%B7-%D0%B5%D0%B4%D1%8B)
- [💼 Поиск вакансий](https://kolbasa.craft.me/ai_test_task/b/858F6CE4-40BA-490B-8A75-315F142D5594/%F0%9F%92%BC-%D0%9F%D0%BE%D0%B8%D1%81%D0%BA-%D0%B2%D0%B0%D0%BA%D0%B0%D0%BD%D1%81%D0%B8%D0%B9)

### Что должно быть в реализации

- **Автоматизация браузера**
  - Программное управление браузером
  - Поддержка persistent sessions (пользователь может войти вручную, агент продолжает работу)
  - Видимый браузер (не headless) — нам нужно видеть, как это работает
- **Автономный AI-агент**
  - Использует модели Claude или OpenAI
  - Принимает решения без постоянного участия пользователя
  - Обрабатывает многошаговые задачи с переходами между страницами
- **Управление контекстом**

  Нельзя просто отправлять целые веб-страницы в контекст AI. Необходимо реализовать стратегии работы с ограничениями по токенам.

- **Продвинутые паттерны (как минимум один)**
  - Sub-agent architecture — специализированные агенты для разных задач
  - Обработка ошибок — агент адаптируется при неудачных действиях
  - Security layer — спрашивает, перед тем как сделать деструктивное действие (оплатить корзину, удалить имейл)
### Чего не должно быть в реализации

- Заготовки действий агента (например шаги по удалению спама или оформлению заказа). Агент должен уметь решать любую новую задачу, сам определять, что ему делать дальше в моменте, а не следовать заданному плану

- Преднаписанные селекторы (например `a[data-qa='vacancy']`) — вместо этого агент должен сам определять, на что нажать и какой у этого элемента селектор

- Подсказки для агента по ссылкам и элементам. Наприер, нельзя хардкодить, что страница с вакансиями — это `/vacancies`, или что для добавления в корзину надо нажимать на кнопку с текстом «Заказать». Агент должен додуматься до этого сам.

### Что можно выбрать самостоятельно

- Библиотека для автоматизации браузера (Puppeteer? Playwright? Selenium? Другое?)

- AI SDK (Anthropic? OpenAI? Прямые API-вызовы?)

- Язык программирования

- Как эффективно извлекать информацию со страницы

- Архитектура tool/function calling

- Как обрабатывать динамические страницы, попапы, формы

- Использовать ли MCP

Мы хотим увидеть твой процесс исследования и технические решения.

### Результат

**Запиши короткое видео, где видно как твой агент решает одну из сложных задач. Также прикрепи, пожалуйста, ссылку на репу с решением.**

- **Как выглядит идеальное решение**

  Ребята, которым мы отправили оффер, присылали видео, на котором было видно как открыт браузер и терминал одновременно.

  В терминале писали короткую задачу для агента и наблюдали, какие он вызывает инструменты и с какими аргументами. Агент исследовал страницу, нажимал на кнопки и вводил текст для решения задачи. Всё это было также одновременно видно в браузере. В конце работы агент делился результатам, что удалось сделать.

  Вот скрины из видео работы кандидата, который получил оффер:

  ![Скриншот идеального решения 1](assets/ideal-solution-01.jpg)

  ![Скриншот идеального решения 2](assets/ideal-solution-02.jpg)

  ![Скриншот идеального решения 3](assets/ideal-solution-03.jpg)

---

## Примеры задач — полный текст вложенных страниц

### ✉️ Удаление спама

[Source](https://kolbasa.craft.me/ai_test_task/b/8B8AEE1D-3A86-4E9D-915C-1944B12B021B/%E2%9C%89%EF%B8%8F-%D0%A3%D0%B4%D0%B0%D0%BB%D0%B5%D0%BD%D0%B8%D0%B5-%D1%81%D0%BF%D0%B0%D0%BC%D0%B0)

#### Цель

Прочитать последние письма в почте и удалить спам-письма.

#### Пользовательский опыт

Пользователь пишет: "Прочитай последние 10 писем в яндекс почте и удали спам"

Агент должен:

1. Перейти в почтовый сервис
2. Открыть папку "Входящие"
3. Прочитать последние 10 писем (тема, отправитель, краткое содержание)
4. Проанализировать каждое письмо и определить спам (рекламные рассылки, подозрительные отправители, фишинг)
5. Удалить спам-письма (переместить в корзину или пометить как спам)
6. Предоставить пользователю краткий отчёт: сколько спама удалено, какие важные письма остались

Предполагается, что перед началом задачи пользователь уже вошёл в свой аккаунт на почтовом сервисе

### 🍔 Заказ еды

[Source](https://kolbasa.craft.me/ai_test_task/b/02731FDF-25EE-4BA7-B09C-DD1A5A9BC20E/%F0%9F%8D%94-%D0%97%D0%B0%D0%BA%D0%B0%D0%B7-%D0%B5%D0%B4%D1%8B)

#### Цель

Оформить заказ на сервисе доставки еды (Яндекс.Еда/Лавка, Delivery Club...)

#### Пользовательский опыт

Пользователь пишет: "Закажи мне BBQ-бургер и картошку фри из того места, откуда я заказывал на прошлой неделе на сайте [...]"

Агент должен:

1. Перейти на сайт доставки еды
2. Найти нужный ресторан или найти BBQ-бургеры через поиск
3. Добавить правильные позиции в корзину (различать похожие товары)
4. Перейти к оформлению заказа
5. Пройти checkout (можно остановиться перед финальным подтверждением оплаты)

Предполагается, что перед началом задачи пользователь уже вошёл в свой аккаунт на сервисе заказа еды

### 💼 Поиск вакансий

[Source](https://kolbasa.craft.me/ai_test_task/b/858F6CE4-40BA-490B-8A75-315F142D5594/%F0%9F%92%BC-%D0%9F%D0%BE%D0%B8%D1%81%D0%BA-%D0%B2%D0%B0%D0%BA%D0%B0%D0%BD%D1%81%D0%B8%D0%B9)

#### Цель

Найти релевантные вакансии и составить персонализированные сообщения для рекрутеров

#### Пользовательский опыт

Пользователь пишет: "Найди 3 подъодящие вакансии AI-инженера на hh.ru и откликнись на них с сопроводительным, предварительно изучив резюме в моём профиле"

Агент должен:

1. Перейти на hh.ru
2. Изучить профиль юзера
3. Найти релевантные вакансии через поиск
4. Извлечь ключевую информацию о каждой позиции
5. Откликнуться на подходящие вакансии, приложив сопроводительное письмо

Предполагается, что перед началом задачи пользователь уже вошёл в свой аккаунт на hh.ru


---

<!-- Source: hr-requirements.ru.md -->

# Уточнения по оценке тестового задания

Source: HR Telegram evaluation message, visible at 5:18 PM, read with Computer Use on 2026-09-09 and verified against the full text supplied by the user on the same date. The preceding assignment-delivery message was supplied by the user after Telegram pointer/scroll controls returned `AXError.notImplemented`. Personal conversation, course/VPN access details, and compensation are excluded from this public extract; full messages are retained locally in `docs/private/hr-messages.ru.md`.

## Срок выполнения — из первого сообщения

⏱️ Срок выполнения ТЗ — 2 дня.
Если потребуется продление по уважительным причинам или тебе перестанет быть актуальной наша вакансия — тоже сразу дай знать.

## Критерии оценки — второе сообщение

В первую очередь мы оцениваем не процент формально выполненных пунктов, а то, насколько решение соответствует ключевым инженерным требованиям ТЗ.

Основные критерии: автономность агента и полноценный цикл принятия решений, универсальность решения без логики под конкретные сайты, корректная работа с представлением страницы, безопасность критичных действий, структурированная работа с LLM и инструментами без парсинга ответов через регулярные выражения, реальный механизм обработки ошибок и смены стратегии, а также общее качество кода и аккуратность репозитория/документации.

В других решениях причиной отказа становились, например, недостаточно надёжная система подтверждения опасных действий, отсутствие заявленных в ТЗ механизмов, regex-парсинг JSON, расхождения документации с реализацией или отсутствие полноценного программного retry-механизма.

Успешными считались решения, где основная архитектура была универсальной и автономной, без site-specific костылей, с надёжной обработкой действий и ошибок. При этом отдельные некритичные недоработки, например отсутствие MCP или ограничения поддержки некоторых провайдеров, сами по себе не являлись причиной отказа.

То есть в первую очередь советую обращать внимание именно на требования, которые в ТЗ обозначены как принципиальные ограничения: если решение напрямую им противоречит, это весит значительно больше, чем то, что остальные 90% задания выполнены корректно.
