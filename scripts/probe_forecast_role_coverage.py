"""Проверка агрегации полосы прогноза по ролям на реальных строках из прод-БД.

Одноразовая диагностика выросла из M7 (`docs/HERMES-ROLE-FALLBACK-REVIEW-20260921.md`):
карточка трейдера обязана показывать, сколько ролей реально ответило, а не рисовать
полосу из двух голосов как трёхголосую. Скрипт читает дамп точек и прогоняет его
через `trader_api._role_votes` + `aggregate_forecast_steps`. В БД не пишет и живой
процесс `webui` не трогает.

Дамп делается из прод-postgres через psql (сам скрипт его не создаёт):

    docker compose exec -T postgres sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
      < scripts/probe_forecast_role_coverage.sql

Сохранить вывод в файл и сдать вторым аргументом, либо указать путь к дампу явно:

    python scripts/probe_forecast_role_coverage.py [request_id ...] [--dump PATH]
"""
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "webui"))

import trader_api  # noqa: E402

DEFAULT_DUMP = ROOT / "scratch" / "p4_rows.json"


def _load_rows(path: Path) -> list[dict]:
    raw = path.read_text(encoding="utf-8-sig")
    # psql с "\pset format unaligned" печатает служебную строку перед JSON.
    return json.loads(raw[raw.index("["):])


def main(argv: list[str]) -> int:
    dump = DEFAULT_DUMP
    if "--dump" in argv:
        i = argv.index("--dump")
        dump = Path(argv[i + 1])
        argv = argv[:i] + argv[i + 2:]
    wanted = {int(a) for a in argv if a.isdigit()}

    if not dump.exists():
        print(f"дамп не найден: {dump}\n"
              f"сделайте его через scripts/probe_forecast_role_coverage.sql "
              f"или передайте --dump PATH", file=sys.stderr)
        return 2

    rows = _load_rows(dump)
    print(f"dump: {dump} rows: {len(rows)}")

    by_request: dict[int, list[dict]] = {}
    for row in rows:
        # Из asyncpg приходит datetime, в textual дампе это строка.
        if isinstance(row["target_time"], str):
            row["target_time"] = datetime.fromisoformat(row["target_time"])
        request_id = int(row["request_id"])
        if wanted and request_id not in wanted:
            continue
        by_request.setdefault(request_id, []).append(row)

    for request_id in sorted(by_request):
        steps, coverage = trader_api.aggregate_forecast_steps(
            trader_api._role_votes(by_request[request_id])
        )
        first = steps[0] if steps else {}
        print(
            f"#{request_id}: steps={len(steps)} basis={coverage['band_basis']} "
            f"present={coverage['roles_present']} missing={coverage['roles_missing']} "
            f"samples={coverage['min_samples_per_step']}-{coverage['max_samples_per_step']} "
            f"models={coverage['min_models_per_step']}-{coverage['max_models_per_step']} "
            f"| step1 close={first.get('close')} band={first.get('c10')}-{first.get('c90')} "
            f"wick={first.get('low')}-{first.get('high')} sigma={first.get('sigma')} "
            f"conf={first.get('confidence')}"
        )
        roles = {
            role: (info["present"], info["model_id"], info["steps"])
            for role, info in coverage["roles"].items()
        }
        print("   roles:", roles)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
