# nvidia-api-keys.md

## Цель

Добавить в Hermes/WORED поддержку NVIDIA API-ключей формата `nvapi-*` без изменения основной модели Hermes и runtime WORED.

## Где хранятся ключи

Только в:

```
~/.hermes/.env
```

Права:
```bash
chmod 700 ~/.hermes
chmod 600 ~/.hermes/.env
```

## Формат одного ключа

```
NVIDIA_API_KEY=nvapi-...
```

## Формат пула ключей

```
NVIDIA_API_KEY_1=nvapi-...
NVIDIA_API_KEY_2=nvapi-...
NVIDIA_API_KEY_3=nvapi-...
NVIDIA_API_KEY_ACTIVE=1
```

## Как проверить наличие ключа

```bash
python scripts/nvidia_api_doctor.py --mode presence --json
```

Пример вывода:
```json
{
  "ok": true,
  "mode": "presence",
  "keys": {
    "NVIDIA_API_KEY": false,
    "NVIDIA_API_KEY_1": true,
    "NVIDIA_API_KEY_2": true,
    "NVIDIA_API_KEY_ACTIVE": "1"
  },
  "active_key_present": true,
  "secrets_printed": false
}
```

## Как проверить /v1/models

```bash
python scripts/nvidia_api_doctor.py --mode models --json
```

## Как проверить chat completion

```bash
python scripts/nvidia_api_doctor.py --mode chat --json
```

## Как переключить активный ключ

```bash
python scripts/nvidia_key_manager.py set-active 2
```

## Что запрещено

- Печатать `nvapi-*` в любом выводе
- Печатать `Bearer <token>`
- Добавлять ключи в `config.yaml`, `git`, `snapshot`, `logs`, `reports`
- Передавать ключи в командной строке
- Хранить ключи вне `~/.hermes/.env`

## Troubleshooting

| Ошибка | Решение |
|--------|----------|
| `NVIDIA API key missing` | Добавьте `NVIDIA_API_KEY_1=nvapi-...` и `NVIDIA_API_KEY_ACTIVE=1` в `~/.hermes/.env` |
| `401 Unauthorized` | Ключ неверный или просрочен — обновите его |
| `403 Forbidden` | У аккаунта нет доступа к выбранной модели — проверьте права в NVIDIA AI Enterprise Portal |
| `404 Model not found` | Модель не доступна для вашего аккаунта — попробуйте другую (`nvidia/llama-3.1-nemotron-70b-instruct`) |
| `timeout` | Увеличьте `--timeout` или проверьте интернет |
| `httpx not installed` | Выполните `pip install httpx` |

## Quick Commands

После добавления в `~/.hermes/config.yaml` можно использовать:

- `/nvidia-key-status` → `nvidia_key_manager.py status`
- `/nvidia-presence` → `nvidia_api_doctor.py --mode presence --json`
- `/nvidia-models` → `nvidia_api_doctor.py --mode models --json`
- `/nvidia-chat` → `nvidia_api_doctor.py --mode chat --json`