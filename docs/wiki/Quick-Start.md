# Быстрый старт

## Требования

- Docker
- Docker Compose
- Python 3.11+ (если нужны локальные проверки и скрипты)
- `.env`-файл на базе примеров в корне репозитория

## Подготовка

1. Скопируйте шаблоны окружения:

```bash
cp .env.example .env
cp .env.postgres.example .env.postgres
cp .env.wored.example .env.wored
```

2. Добавьте реальные значения токенов и параметров доступа в `.env` и связанные файлы.

3. Для локального runtime используйте корневой `docker-compose.yml`.

## Запуск

### PowerShell

```powershell
Set-Location D:\WORED
Copy-Item .env.example .env
# отредактируйте .env
docker-compose up --build -d
docker-compose ps
docker-compose logs --tail 50 collector
docker-compose logs --tail 50 chatbot
Invoke-WebRequest http://localhost:8080/api/health
```

### Bash

```bash
cd /d/WORED
cp .env.example .env
# отредактируйте .env
docker-compose up --build -d
docker-compose ps
docker-compose logs --tail 50 collector
docker-compose logs --tail 50 chatbot
curl -fsS http://localhost:8080/api/health
```

## Проверка состояния

- `docker-compose ps`
- логи сервисов `collector`, `chatbot`, `webui`
- health check endpoint:

```bash
curl http://localhost:8080/api/health
```

## Безопасность

- Не храните ключи в репозитории
- Не коммитьте `.env`, `.env.postgres`, `.env.wored` и любые файлы с секретами
- Используйте минимально необходимые права на файлы и переменные среды

## Полезные команды

```bash
# Запуск тестов
pytest -q

# Проверка проекта
make test

# Полный review / QA
make qa
```

Подробнее см. корневой `README.md` и проектную документацию в папке `docs/`.
