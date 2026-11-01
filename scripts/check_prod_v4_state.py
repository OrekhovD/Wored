#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Проверка состояния БД перед / после наката прод-миграций V4 (QD-15).

Только ЧТЕНИЕ. По умолчанию — dry-run против WORED_DB_DSN (если не задан,
печатает список ожидаемых объектов и ВЫХОДИТ, не подключаясь).

Вывод: таблица объект/есть/нет для критических V4-объектов:
  таблицы: paper_v2_experiments, paper_v2_domain_events, paper_v2_funding_*,
           paper_v2_fee_schedule_observations, paper_v2_protective_fee_decisions,
           owner_identity_links, paper_trading_runtime
  колонки: paper_v2_positions.experiment_id (P2), paper_v2_fills.contract_multiplier,
           paper_v2_positions.domain_event_id (P3), outbox-колонки в paper_v2_domain_events

Использование:
  WORED_DB_DSN=postgresql://... python scripts/check_prod_v4_state.py [--dsn DSN]
"""
import os
import sys

EXPECTED_TABLES = [
    # V1/V2 база (уже должна быть в проде)
    "paper_v2_owners", "paper_v2_accounts", "paper_v2_days", "paper_v2_positions",
    "paper_v2_orders", "paper_v2_fills", "paper_v2_postings", "paper_v2_commands",
    "paper_v2_events", "paper_v2_market_events",
    # V4 P2: experiment scope
    "paper_v2_experiments",
    # V4 P3: domain events
    "paper_v2_domain_events",
    # QD-10A: fee/funding
    "paper_v2_funding_calendars", "paper_v2_funding_rate_observations",
    "paper_v2_funding_settlements", "paper_v2_funding_history_pages",
    "paper_v2_funding_history_coverage",
    # QD-10C: fee provenance + protective
    "paper_v2_fee_schedule_observations", "paper_v2_execution_fee_provenance",
    "paper_v2_protective_fee_decisions",
    # P2: owner identity
    "owner_identity_links",
    # runtime flags
    "paper_trading_runtime",
]

EXPECTED_COLUMNS = [
    ("paper_v2_positions", "experiment_id"),          # P2
    ("paper_v2_fills", "contract_multiplier"),        # 20261023db
    ("paper_v2_fills", "qty"),                        # base
    ("paper_v2_domain_events", "telegram_message_id"),# P3 outbox (если есть)
    ("paper_v2_positions", "entry_fee"),              # база
    ("paper_v2_positions", "funding_cashflow"),
    ("paper_v2_positions", "exit_fee"),
]


def check(dsn: str) -> int:
    try:
        import asyncpg
    except ImportError:
        print("asyncpy не установлен; используй psycopg2 или устанави asyncpy")
        return 1
    import asyncio

    async def run():
        conn = await asyncpg.connect(dsn)
        try:
            print(f"Подключение: {dsn.split('@')[-1]}")
            print()
            # Таблицы
            rows = await conn.fetch(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='public'"
            )
            have = {r["table_name"] for r in rows}
            print(f"{'Таблица':<45} {'Статус':<10}")
            print("-" * 55)
            missing = []
            for t in EXPECTED_TABLES:
                status = "есть" if t in have else "НЕТ"
                if t not in have:
                    missing.append(t)
                print(f"{t:<45} {status:<10}")
            print()
            # Колонки
            rows = await conn.fetch(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema='public'"
            )
            have_cols = {(r["table_name"], r["column_name"]) for r in rows}
            print(f"{'Колонка':<45} {'Статус':<10}")
            print("-" * 60)
            missing_col = []
            for t, c in EXPECTED_COLUMNS:
                if t not in have:
                    continue  # таблица отсутствует — колонка тоже
                if (t, c) not in have_cols:
                    missing_col.append((t, c))
                    print(f"{t}.{c:<25} НЕТ")
            print()
            # Резюм
            if not missing and not missing_col:
                print("ВСЁ ЕСТЬ — миграции уже накатены")
            else:
                print(f"Отсутствует таблиц: {len(missing)}")
                print(f"Отсутствует колонок: {len(missing_col)}")
                print("→ требуется накат 20261031_prod_v4_batch.sql")
        finally:
            await conn.close()

    return asyncio.run(run())


def main():
    dsn = os.environ.get("WORED_DB_DSN") or ""
    args = sys.argv[1:]
    if "--dsn" in args:
        i = args.index("--dsn")
        if i + 1 < len(args):
            dsn = args[i + 1]
    if not dsn:
        print(__doc__)
        print("WORED_DB_DSN не задан — dry-run. Задай DSN или --dsn.")
        return 0
    # маскирую пароль в выводе
    masked = dsn
    if "@" in masked and "://" in masked:
        proto, rest = masked.split("://", 1)
        if "@" in rest:
            creds, host = rest.rsplit("@", 1)
            masked = f"{proto}://***@{host}"
    os.environ["WORED_DB_DSN"] = masked
    # подменяю DSN на маскированный для печати (asyncpg сам подключится к оригиналу)
    import asyncpg  # noqa
    return check(dsn)


if __name__ == "__main__":
    sys.exit(main())