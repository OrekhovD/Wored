# WORED/Hermes Model Routing

## Purpose

Define how Hermes selects AI models for WORED without storing raw keys in git,
docs, scripts, reports, Telegram output, or `~/.hermes/config.yaml`.

## Credential Sources

Runtime secrets live in:

```bash
~/.hermes/secrets/model_keys.yaml
~/.hermes/.env
```

Rules:

- `~/.hermes/secrets` must be `chmod 700`.
- `model_keys.yaml` must be `chmod 600`.
- `~/.hermes/.env` must be `chmod 600` and should contain only provider env
  vars required by native Hermes, such as the active `GLM_API_KEY`.
- Never print `model_keys.yaml`.
- Use `scripts/hermes/model_inventory.py` for masked inventory output.

## Provider Matrix

| Provider | Credential strategy | Model strategy | Role |
|---|---|---|---|
| NVIDIA NIM | model-bound key per model | one key is tested only against its assigned model | reviewer, architect, bug hunt, long-context docs |
| Qwen/DashScope | single key, multiple models | one key can test Qwen coder/plus/flash models | main coding driver and fast fallback |
| GLM/ZAI | single key, multiple models | one key can test GLM models | Russian reasoning and fallback reports |
| DeepSeek official | single key, multiple models | one key can test `deepseek-chat` and `deepseek-reasoner` | cross-provider fallback |
| Google AI Studio | single key, multiple models | OpenAI-compatible Gemini endpoint | cheap fast fallback when present |

## Routing Roles

| Role | Primary | Fallback | Notes |
|---|---|---|---|
| Hermes main coding agent | Qwen coder/plus | NVIDIA Qwen Coder or MiniMax M2.7 | daily code edits and repo work |
| Architecture reviewer | NVIDIA MiniMax M2.7 | GLM/ZAI | independent review before risky changes |
| Patch validator | NVIDIA MiniMax M2.7 | Qwen | use for high-risk diffs |
| Bug hunt | NVIDIA DeepSeek V4/V3.2 | DeepSeek official reasoner | security and defect-oriented checks |
| Long-context docs | NVIDIA Kimi K2 or MiniMax M2.7 | Qwen Plus | specs, playbooks, long reports |
| Russian operator reports | GLM/ZAI | Qwen | compact Russian summaries |
| Telegram compact replies | Qwen/GLM flash models | deterministic scripts | short answers only |
| Trade/risk calculations | Python scripts | model explanation only | models must not calculate risk by intuition |

## Fallback Chain

Use the chain produced by:

```bash
python3 scripts/hermes/model_inventory.py --format json
```

Expected preference when probes confirm working models:

```text
Qwen coding driver -> NVIDIA MiniMax M2.7 reviewer -> GLM/ZAI fallback -> DeepSeek official fallback
```

If probes do not confirm a working Qwen model, Hermes may use the first
confirmed fallback as the active `model.default`, with the Qwen route preserved
as the preferred target to fix. If no provider returns `ok`, keep
`~/.hermes/config.yaml` minimal and keep the full routing matrix documented here
until keys/models are fixed.

## Commands

Masked JSON inventory:

```bash
python3 /mnt/d/WORED/scripts/hermes/model_inventory.py --format json --timeout 8 --output /tmp/wored_model_inventory.json --validate-json
```

Markdown summary:

```bash
python3 /mnt/d/WORED/scripts/hermes/model_inventory.py --format markdown --timeout 8
```

Skip network and validate structure only:

```bash
python3 /mnt/d/WORED/scripts/hermes/model_inventory.py --skip-network --format json --output /tmp/wored_model_inventory.json --validate-json
```

If a Hermes tool cannot preserve newlines or shell redirection, use the single
absolute-path command above. Do not use `../../../tmp/wored_model_inventory.json`;
`/tmp/wored_model_inventory.json` is already an absolute WSL path.

## Hermes Tool Selection

Use the Terminal tool for shell commands:

```bash
python3 /mnt/d/WORED/scripts/hermes/model_inventory.py --format json --timeout 8 --output /tmp/wored_model_inventory.json --validate-json
```

Use Execute Code only for Python source. If Terminal is unavailable, this is the
safe one-line Execute Code equivalent:

```python
exec(open("/mnt/d/WORED/scripts/hermes/run_model_inventory_execute_code.py", encoding="utf-8").read())
```

A `SyntaxError` from Execute Code for `python3 ...`, `cd ...`, `docker ...`, or
`git ...` means the command was sent to the wrong tool. Retry with Terminal.
If the one-line `exec(open(...).read())` form fails, the Execute Code runtime
does not have access to the WSL project path and Terminal is required.

## Safety Rules

- NVIDIA `nvapi-*` keys are model-bound; never cross-test them against unrelated models.
- Qwen, GLM, Google, and DeepSeek official entries are single-key multi-model providers.
- Raw keys belong only in host-level secret files: `~/.hermes/secrets/model_keys.yaml`
  and the minimal native Hermes `~/.hermes/.env`.
- `~/.hermes/config.yaml` may reference provider/model/base_url/secret ids, but must not contain raw keys.
- Models provide advisory text; deterministic Python scripts perform risk, scoring, trade sizing, and journal writes.
