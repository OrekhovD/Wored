# model-routing.md

## Purpose

Run the MODEL-P1.1 inventory and apply a safe Hermes routing matrix without
leaking secrets or changing WORED runtime services.

## Allowed Scope

- `scripts/hermes/*`
- `docs/hermes/*`
- `~/.hermes/secrets/model_keys.yaml`
- `~/.hermes/config.yaml`

Do not edit `chatbot/*`, `collector/*`, `webui/*`, `docker-compose.yml`, or the
project `.env`.

## Setup

Secret file:

```bash
test -f ~/.hermes/secrets/model_keys.yaml
test -f ~/.hermes/.env
stat -c '%a %n' ~/.hermes/secrets ~/.hermes/secrets/model_keys.yaml ~/.hermes/.env
```

Expected permissions:

```text
700 /home/hermes/.hermes/secrets
600 /home/hermes/.hermes/secrets/model_keys.yaml
600 /home/hermes/.hermes/.env
```

## Inventory

```bash
python3 /mnt/d/WORED/scripts/hermes/model_inventory.py --format json --timeout 8 --output /tmp/wored_model_inventory.json --validate-json
python3 /mnt/d/WORED/scripts/hermes/model_inventory.py --format markdown --timeout 8
```

For Hermes tool calls, prefer the absolute-path commands above. They avoid
multi-line shell syntax, `cd`, output redirection, and the common wrong path
`../../../tmp/wored_model_inventory.json`.

Tool selection rule:

- Terminal runs shell commands such as `python3 ...`, `cd ...`, `docker ...`,
  `git ...`, and `hermes ...`.
- Execute Code runs Python source only. Do not paste shell commands into it.

If Terminal is unavailable, use this one-line Execute Code Python snippet:

```python
exec(open("/mnt/d/WORED/scripts/hermes/run_model_inventory_execute_code.py", encoding="utf-8").read())
```

Schema check:

```bash
python3 - <<'PY'
import json
p=json.load(open("/tmp/wored_model_inventory.json", encoding="utf-8"))
assert p["status"] in ["ok", "partial", "no_keys"]
assert "providers" in p
for pr in p["providers"]:
    assert "provider" in pr
    assert "key_count" in pr
    assert "working_models" in pr
    assert "errors" in pr
print("MODEL_INVENTORY_SCHEMA_OK")
PY
```

Secret scan:

```bash
grep -RniE "nvapi-[A-Za-z0-9_\\-]{20,}|sk-[A-Za-z0-9_\\-]{20,}|AIza[0-9A-Za-z_\\-]{20,}" \
  scripts/hermes docs/hermes ~/.hermes/config.yaml ~/.hermes/SOUL.md 2>/dev/null \
  && echo "SECRET_LEAK_FOUND" || echo "SECRET_SCAN_OK"
```

## Routing Policy

- Qwen is the preferred Hermes coding driver when a Qwen probe works.
- If Qwen fails but another provider works, use the first confirmed fallback as
  `model.default` and keep Qwen in the proposed routing backlog.
- NVIDIA MiniMax M2.7 is reviewer/architect fallback.
- GLM/ZAI is Russian reasoning fallback.
- DeepSeek official is cross-provider fallback.
- Native Hermes reads the active provider credential from `~/.hermes/.env`;
  model-bound NVIDIA keys stay only in `model_keys.yaml`.
- If no provider returns `OK`, keep Hermes config minimal and fix keys/model ids first.

## Acceptance

Pass criteria:

- `MODEL_INVENTORY_SCHEMA_OK`
- `SECRET_SCAN_OK`
- `hermes doctor` runs after config update
- Docker services were only inspected with `docker compose ps`
