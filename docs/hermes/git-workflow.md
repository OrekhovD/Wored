# Git Task Branch Workflow

## Цель
Обеспечить безопасное и отслеживаемое развитие WORED через изолированные git-ветки для каждой задачи Hermes.

## Правила
- Каждая задача уровня P0/P1/P2 создаёт отдельную ветку `hermes/<task-name>`.
- Ветка создаётся **локально**, `git push` выполняется только по ручному запросу адмирала.
- Все коммиты должны быть семантическими (`feat:`, `fix:`, `chore:`, `docs:`).
- Ветка должна содержать только изменения, относящиеся к задаче.
- После завершения — ветка удаляется (`git branch -d hermes/<task>`), PR не создаётся (все изменения — локальные).

## Шаги

### 1. Создание ветки
```bash
cd /mnt/d/WORED
git checkout -b hermes/ux-foundation-patch
```

### 2. Работа и коммиты
```bash
# после каждого логического изменения:
git add .
git commit -m "feat(webui): add status-dot and badge classes"
```

### 3. Проверка перед завершением
```bash
git status
# убедиться, что нет untracked файлов
# убедиться, что все изменения в индексе
```

### 4. Завершение задачи
```bash
# переключиться в main
git checkout main
# удалить ветку
git branch -d hermes/ux-foundation-patch
```

## Примеры названий веток
- `hermes/ux-foundation-patch` — P1-01
- `hermes/runtime-health-panel` — P1-02
- `hermes/collector-observability` — P1-03
- `hermes/prediction-quality-view` — P1-04
- `hermes/smoke-webui-script` — P2-01

## Запрещено
- `git push` без явного разрешения адмирала.
- Изменения в `main` напрямую.
- Ветки вне префикса `hermes/`.
- Коммиты без описания или с `WIP`.
