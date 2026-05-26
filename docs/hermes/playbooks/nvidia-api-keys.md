# nvidia-api-keys.md

## Цель

Поддерживать пул NVIDIA API ключей формата `nvapi-*` для Hermes/WORED без вывода секретов в терминал, логи, Telegram или git.

## Где хранятся ключи

Основной runtime path для Hermes в WSL:

```bash
/home/hermes/.hermes/secrets/nvidia_keys.txt
```

Допустимые дополнительные источники:

```bash
/home/hermes/.hermes/.env
```

Переменные в `.env`, если они уже используются:

```dotenv
NVIDIA_API_KEY=nvapi-...
NVIDIA_API_KEY_1=nvapi-...
NVIDIA_API_KEY_2=nvapi-...
NVIDIA_API_KEY_ACTIVE=1
```

Файл `nvidia_keys.txt` хранит один ключ на строку. Значения нельзя печатать.

## Импорт ключей

Импортировать ключи из текстового файла с несколькими `nvapi-*` токенами:

```powershell
wsl -e python3 /mnt/d/WORED/scripts/nvidia_key_manager.py --keys-file /home/hermes/.hermes/secrets/nvidia_keys.txt import-file "/mnt/d/WORED/Новая папка/Текстовый документ.txt" --replace --json
```

Ожидаемый безопасный вывод:

```json
{
  "ok": true,
  "source_keys_found": 7,
  "keys_written": 7,
  "secrets_printed": false
}
```

## Проверка наличия

```powershell
wsl -e python3 /mnt/d/WORED/scripts/hermes/probe_nvidia_minimax.py --dry-run
```

Pass means:

- `"ok": true`;
- `"total_keys"` больше `0`;
- `"secrets_printed": false`;
- в выводе нет строк, начинающихся с `nvapi-`.

## Проверка MiniMax M2.7

Быстрый probe до первого успешного ключа:

```powershell
wsl -e python3 /mnt/d/WORED/scripts/hermes/probe_nvidia_minimax.py --timeout 20
```

Проверка всех ключей, даже если один уже сработал:

```powershell
wsl -e python3 /mnt/d/WORED/scripts/hermes/probe_nvidia_minimax.py --timeout 20 --exhaustive
```

Pass means:

- `"ok": true`;
- `"working_key_index"` содержит номер ключа;
- `"secrets_printed": false`.

Если все ключи дают timeout или upstream error, это не раскрывает секреты и не считается успешным provider-probe.

## Key manager

Проверить статус pool без вывода значений:

```powershell
wsl -e python3 /mnt/d/WORED/scripts/nvidia_key_manager.py --keys-file /home/hermes/.hermes/secrets/nvidia_keys.txt status --json
```

Сменить legacy active index в `.env`, если старые команды всё ещё смотрят на `NVIDIA_API_KEY_ACTIVE`:

```powershell
wsl -e python3 /mnt/d/WORED/scripts/nvidia_key_manager.py set-active 1 --json
```

Новый doctor не зависит от active index: он перебирает все найденные ключи по порядку.

## Что запрещено

- Печатать `nvapi-*` в любом выводе.
- Печатать `Bearer <token>`.
- Добавлять реальные ключи в `config.yaml`, git, runtime snapshots, logs или reports.
- Передавать ключи аргументом командной строки.
- Использовать `cat ~/.hermes/secrets/nvidia_keys.txt`.
- Использовать `echo >>` для записи ключей.

## Troubleshooting

| Ошибка | Решение |
|---|---|
| `NVIDIA API key missing` | Импортируйте ключи в `/home/hermes/.hermes/secrets/nvidia_keys.txt` через `import-file`. |
| `The read operation timed out` | Увеличьте `--timeout`; если timeout повторяется на всех ключах, проверьте доступность `https://integrate.api.nvidia.com/v1`. |
| `unauthorized_or_invalid_key` | Ключ неверный или просрочен; удалите его из secret pool вручную через редактор. |
| `forbidden_for_key_or_account` | У аккаунта нет доступа к выбранной модели. |
| `model_or_endpoint_not_found` | Проверьте `--model minimaxai/minimax-m2.7` и базовый endpoint. |

## Quick Commands

Hermes quick command для probe должен запускать:

```bash
cd /mnt/d/WORED && python3 scripts/hermes/probe_nvidia_minimax.py
```

Для сухой проверки:

```bash
cd /mnt/d/WORED && python3 scripts/hermes/probe_nvidia_minimax.py --dry-run
```
