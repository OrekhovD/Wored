# Phase 3: Minimal Vertical Slice

## Цель
Собрать минимальную функционирующую систему с:
- Telegram receive/send
- HTX read-only market fetch
- Один AI provider adapter
- Routing policy
- Token logging
- Quota checks
- Health command
- Smoke tests

## Шаги
1. Запустить систему с заполненным .env
2. Проверить инициализацию базы данных
3. Проверить подключение Telegram бота
4. Проверить подключение хотя бы одного AI провайдера (dashscope)
5. Выполнить простой запрос через /ask
6. Убедиться, что токены логируются
7. Проверить команду /health
8. Запустить smoke-тесты

## Архитектура
- app/main.py: запуск FastAPI + bot polling
- bot/handlers.py: /ask обрабатывает через сервисы
- routing/: выбор модели через политики
- providers/: вызов AI провайдера
- accounting/: запись использования
- storage/: сохранение в БД

## Ожидаемый результат
Пользователь может отправить /ask в Telegram, получить ответ от AI модели, и система сохранит лог использования в БД.