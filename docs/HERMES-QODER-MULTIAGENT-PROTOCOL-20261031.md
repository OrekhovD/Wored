# WORED Multi-Agent Protocol v1 — Hermes ⇄ Qoder (2026-10-31, Asia/Bangkok)

Статус: **PROPOSED** (владелец утвердил направление; изменения в этот файл = решение владельца или Hermes после его "го").
Заменяемая схема: Codex goal-owner (docs/QODER-CODEX-GOAL-CONTRACT-20261027.md). Codex-очередь остаётся как исторический канал, **активного владельца цели в Codex больше нет**.

---

## 1. Роли

| Уровень | Кто | Что делает | Не делает |
|---|---|---|---|
| **L0 Owner** | владелец | Решения гейтов A/B, подтверждения live-действий, конечные приёмки целей | — |
| **L1 Goal-owner** | **Hermes** (я) | Декомпозиция цели в гейты (DAG), постановка задач Qoder, **независимая верификация** отчётов, интеграция, prod-операции (только с решения L0) | Не пишет продукт-код по гейтам Qoder |
| **L1 Workers** | Hermes-subagents (`delegate_task`, ≤10 параллельных) | Анализы, документация, тесты-черновики, QA-прогоны, миграции-черновики — работа вне allowlist Qoder | Prod-действия, commit/push |
| **L2 Executor** | **Qoder** (IDE, Qwen3.8-Flash Thinking medium) | Один гейт за один ход: ограниченный код/тесты/док по allowlist, disposable QA, отчёт `QD-NN-{DONE,NEEDS_INPUT,BLOCKED}.md` | Не выбирает модель/усилие сам, не трогает файлы вне allowlist, не принимает сам себя |
| **L2 Verifier-помощь** | Hermes-subagents | Независимый перегон тестов, SHA-сверка, diff-review, red-before семантика | — |

Правило приёмки без изменений (из goal contract §Полномочия): **отчёт Qoder — вход для проверки, а не приёмка**. Статус гейта меняет только Hermes после независимой верификации.

## 2. Каналы (двоичная почта через рабочее дерево)

```
D:/WORED/artifacts/
  qoder-tasks/QD-NN-TASK.md          ← Hermes пишет постановку (гейт-карточка)
  qoder-handoff/QD-NN-{DONE|NEEDS_INPUT|BLOCKED}.md   ← Qoder пишет отчёт
  qoder-handoff/.sent/               ← dedupe-маркеры (уже работает)
  qoder-handoff/verdicts/QD-NN-VERDICT.md            ← Hermes пишет вердикт верификации
  locks/QD-NN.lock                   ← владение горячими файлами (см. §5)
  llm-budget/2026W44.json            ← append-only счётчик облачных вызовов
```

- **Hermes → Qoder**: гейт-карточка. Qoder читает её как первое действие хода (правило workflow.md).
- **Qoder → Hermes**: отчёт. Уведомление НЕ нужно через `codex queue` — Hermes видит дерево напрямую и читает папку в каждом ходе. `scripts/qoder_notify_codex.ps1` можно вызывать как раньше (лог в codex-очередь не мешает), но это НЕ является сигналом доставки.
- **Qoder → Hermes срочное (owner-вопрос)**: `NEEDS_INPUT` с ровно одним вопросом; Hermes доводит до L0.

## 3. Гейт-карточка (формат постановки)

```markdown
# QD-NN — <название>
Owner-решение: <ссылка или "не требуется">
Mode: PLAN_ONLY | APPLY | VERIFY_ONLY
Allowlist (исчерпывающий): <файлы/пути>
Запрещено: <зоны — обычно collector/, chatbot/, .env, compose, migrations при APPLY>
Тесты: targeted → full ladder (дискретный wored_qa), команды
DONE-критерий: <конкретно>
Red-before: <для багфиксов — семантический сценарий, который старый код проходит, новый должен валить>
Handoff формат: QD-NN-{STATUS}.md, ≤12 KiB, факты/SHA/счётчики/остаточные риски
```

## 4. Цикл гейта (Hermes = owner+verifier)

1. **PLAN**: декомпозиция → карточка. Финансовое/файлы collector+chatbot → `PLAN → DIFF → APPLY → TEST → REPORT`, APPLY только после решения L0.
2. **EXEC**: Qoder работает. Hermes НЕ прерывает; параллельно может вести другие дорожки (§5).
3. **VERIFY (Hermes, независимая)**: 
   - `git diff` — только allowlist, ничего чужого;
   - SHA-256 всех тронутых файлов до/после;
   - независимый QA `docker compose -p wored-qa -f docker-compose.qa.yml run --rm **--build** checks …` (правило: после ANY правки файлов — всегда `--build`, stale-image ловушка QD-12);
   - red-before семантика на багфиксах;
   - ruff в QA-контейнере.
4. **VERDICT**: `artifacts/qoder-handoff/verdicts/QD-NN-VERDICT.md`. PASS (только Hermes) → следующий гейт из DAG; FAIL → rework-карточка; BLOCKED/NEEDS_INPUT → L0.
5. **LOG**: backfill-строка в backlog-таблицу `docs/QODER-G7-TO-PROOF-BACKLOG-20261025.md`.

## 5. Параллельность без конфликтов

- **Решение конфликтов = владение файлами**: перед постановкой гейта Hermes отмечает allowlist-файлы в `artifacts/locks/`. **Горячие файлы** (`paper_trading/runner.py`, `service.py`, `repository.py`) — только один держатель за раз.
- **Разные дорожки = disjoint allowlist**. Пример: Qoder правит тест-фикстуру (дорожка Q), Hermes-subagent пишет документацию/анализ (дорожка H, трогает только docs/), Hermes сам коммитит (дорожка P, трогает только git-индекс).
- **Очередь git**: коммит/пуш делает ТОЛЬКО Hermes и только при пустом чужом work-in-progress на файлы, которые он коммитит. Qoder никогда не коммитит, пока правило действует (живая граница goal contract).

## 6. LLM-бюджет (экономия — принцип: local first)

| Модель | Где | Ограничение | Что даём ей | Что НЕ даём |
|---|---|---|---|---|
| **Qwen3.8-Flash (Thinking medium)** | Qoder IDE | безлимит (владелец) | Весь executor-гейт: локальный поиск, правки, тесты | Финансовую политику, самостоятельный выбор модели |
| **Bonsai-27b локально** (`bonsai-27b:lmstudio-q1`, host:8088) | collector/chatbot/bot_rachello/Hermes при желании | безлимит, медленная (11–13 tok/s, cold ~145s) | ВСЁ вспомогательное: summaries, пересказы отчётов, черновики формулировок, intent-classification | Reasoning для гейт-постановок (слишком медленно/поверхностно) |
| **Kimi K3 free** (TokenRouter) | Hermes | free-квота TokenRouter (щедрая) | utility-тексты: шаблоны, переводы, быстрые драфты | Критические рассуждения о деньгах |
| **glm-5.3-flash:cloud** | Ollama Cloud Pro | дневные/недельные квоты | Короткий reasoning при постановке гейта, верификация сложных инвариантов | Рутина, длинные тексты, что-то, что решит K3/Bonsai |
| **deepseek-v4.1-flash:cloud** | Ollama Cloud Pro | дневные/недельные квоты | Эскалация: трудный финансовый инвариант, конкурентность PG | Anything cheaper может |
| **glm-5.3:cloud, gemma4:31b:cloud** | Ollama Cloud Pro | квоты | Только когда flash-версии недостаточно (эскалация 2-го уровня) | По умолчанию никогда |

**Правила расхода Ollama Cloud (жёстко):**
1. Hermes сначала пробует **K3/Bonsai**; cloud-модель — только эскалация и только с записью причины в счётчик.
2. Один reasoning-ход на вопрос; никаких повторов/пережарок (retry на другой модели, не на той же).
3. Счётчик вызовов: `artifacts/llm-budget/<ISO-неделя>.json`, append-only, `{"model","role","purpose","ts","tokens_in/out"}`. Обзор по требованию владельца.
4. Прогнозные роли (`prediction_engine`, chatbot chains) считаются как раньше, их вызовы тоже логируются в счётчик.

## Граница live (без изменений)

До отдельного решения L0: чтение прод-БД/сервисов, правки разрешённого кода, disposable QA — да; записи/DDL в `trading`, restart/recreate, изменение `.env`/compose, Telegram TEST, реальные сделки, commit/push руками Qoder — нет. Компиляция/исправление продовых миграций Qoder'ом — только по явно выданной карточке. Live-гейт всегда runbook (preflight, stop-критерии, проверка), QA не выдаётся за live.

## 8. Пилотная проверка (первый параллельный ход)

- **Q (Qoder)**: карточка QD-13 = вариант B QD-12 (runner lazy-store → `event.parser_version`), файлы `paper_trading/quote_store.py`, `paper_trading/runner.py`, тесты. Hot-lock на runner.py → я не трогаю runner.py в это время.
- **H (Hermes-subagent)**: коммит 87 незакоммиченных файлов по группам (кроме горячих), push.
- **P (Hermes)**: накат миграций в прод — только после решения L0.