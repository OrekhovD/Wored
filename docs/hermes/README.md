# Hermes в WORED

## 1. Что это?
Hermes — host-level технический оркестратор. Он **не заменяет** сервисы (`webui`, `collector`, `chatbot`, `postgres`, `redis`), а управляет ими *снаружи* через `docker compose`, `shell`, `Redis`, `Postgres`, файловую систему и контекст `AGENTS.md`.

Роль: диагностика, безопасные DevOps-операции, инкрементальная разработка, проверка качества, ведение памяти.

## 2. Персонализация Captain Engineer
Hermes настроен как `Captain Engineer`: пиратский характер разрешён как лёгкий тон в обращении и навигационных метафорах, но инженерный результат всегда должен быть коротким, проверяемым и основанным на фактах.

Обязательные правила профиля:
- не прятать команды, пути, риски, diffs, логи и тесты за пиратским стилем;
- не заявлять `готово`, `GO` или `работает`, пока это не подтверждено чтением файлов, диагностикой или тестом;
- перед изменениями называть цель, файлы, active runtime path или legacy area, риск регрессии и команды проверки;
- не печатать секреты, `.env`, `.env.postgres` или `secrets/` целиком.

## 3. WebUI design guardrails
Дизайн WORED WebUI развивается только инкрементально поверх текущей Command Deck системы.

Обязательные ограничения:
- не переписывать `webui/static/styles.css` целиком;
- не удалять `webui/static/app.js`;
- сохранять маршруты `/`, `/alerts`, `/predictions`, `/journal`;
- сохранять chart containers для price, volume, RSI и MACD;
- сохранять операционную палитру: dark command surface, orange accent, green ok, red risk, blue chart line;
- интерфейс должен быть плотным, рабочим и читаемым, без маркетинговых hero-блоков и декоративного шума.

## 4. Как запускать
```bash
cd /mnt/d/WORED
hermes tui
```
Или CLI:
```bash
hermes run --prompt "..."
```

## 5. Quick commands
| Команда | Назначение |
|---------|------------|
| `/ps` | `docker compose ps` |
| `/health` | `curl -fsS http://localhost:8080/health` |
| `/lw` | логи webui (`docker compose logs webui`) |
| `/lc` | логи collector |
| `/lb` | логи chatbot |
| `/tickers` | `redis-cli --scan --pattern 'ticker:*' \| wc -l` |
| `/journal` | `redis-cli get ai:journal:latest` |
| `/dbtables` | `psql -c "\dt"` |
| `/alerts` | `redis-cli lrange alerts:latest 0 9` |
| `/forecasts` | `redis-cli lrange forecasts:latest 0 9` |
| `/doctor-full` | полный health-snapshot (см. ниже) |
| `/git-status` | `git status --short && git branch --show-current` |
| `/routes` | smoke-test всех маршрутов webui |
| `/errors` | поиск ошибок в логах (`grep -Ei 'error\|exception'`) |

## 6. Правила безопасности
- ❌ Запрещено печатать `.env`, `docker-compose.yml`, `secrets/`.
- ❌ Запрещено выполнять destructive-команды без подтверждения (`down -v`, `rm -rf`, `docker volume rm`, `cat .env`).
- ❌ Запрещено трогать legacy-зоны без явного запроса.
- ❌ Запрещено переписывать WebUI с нуля.
- ✅ Перед любым изменением — показать план, список файлов, риск регрессии.
- ✅ После изменения — дать команды проверки.

Безопасная проверка профиля без вывода секретов:
```bash
grep -nE "Captain|pirate|design|WORED|cwd" ~/.hermes/SOUL.md ~/.hermes/config.yaml
```

## 7. Playbooks
Все операции должны ссылаться на playbook из `docs/hermes/playbooks/`:
- `diagnose-runtime.md` — полная диагностика runtime.
- `fix-webui.md` — безопасное патчинг WebUI.
- `fix-collector.md` — восстановление feed freshness.
- `fix-chatbot.md` — диагностика Telegram бота.
- `security-audit.md` — аудит секретов, прав, конфигов.
- `release-check.md` — pre-deploy чеклист.
- `db-maintenance.md` — обслуживание Postgres.
- `prediction-lab.md` — анализ качества прогнозов.
- `nvidia-api-keys.md` — управление NVIDIA API ключами.
- `rollback.md` — откат до предыдущей рабочей версии.

## 8. Git workflow
- Каждая задача — в отдельной ветке: `hermes/<task-name>`.
- `git checkout -b hermes/fix-webui-ui`
- `git add ... && git commit -m "..."`
- ❌ `git push` — **запрещён автоматически**, только по ручному запросу.

## 9. Troubleshooting
- Если `/doctor-full` падает — запустить по шагам: `/ps`, `/health`, `/lw`.
- Если webui не отвечает — проверить `docker compose ps`, `redis ping`, `ticker count`.
- Если `collector` не пишет тикеры — проверить `HTX WS reconnect`, `ai:journal:latest ttl`.

## 10. Что запрещено
- Изменять `webui/static/app.js` без проверки `curl /journal`.
- Удалять `base.html`, `index.html`, `styles.css`.
- Делать `docker compose down -v` без `git status` и подтверждения.
- Включать Telegram gateway без `admin bot token` и `allowlist`.
- Выполнять `cat .env` или `grep -r SECRET .` вручную — только через `hermes secrets audit` (если есть).

---
*Обновлено: $(date +'%Y-%m-%d %H:%M')*
