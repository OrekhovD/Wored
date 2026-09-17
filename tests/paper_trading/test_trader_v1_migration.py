"""Phase 7: Migration rehearsal for trader_v1_schema.sql."""
import asyncio
import asyncpg
import os


async def main():
    conn = await asyncpg.connect("postgresql://bot:botpassword@postgres:5432/trading")
    with open("migrations/trader_v1_schema.sql", "r") as f:
        schema = f.read()
    await conn.execute(schema)
    rows = await conn.fetch(
        "SELECT tablename FROM pg_tables WHERE schemaname='public' "
        "AND tablename LIKE 'trader_v1_%' ORDER BY tablename"
    )
    for r in rows:
        print(r["tablename"])
    # Verify FK to paper_v2
    fks = await conn.fetch(
        "SELECT tc.table_name, kcu.column_name, ccu.table_name AS fk_table "
        "FROM information_schema.table_constraints tc "
        "JOIN information_schema.key_column_usage kcu ON tc.constraint_name = kcu.constraint_name "
        "JOIN information_schema.constraint_column_usage ccu ON tc.constraint_name = ccu.constraint_name "
        "WHERE tc.table_name LIKE 'trader_v1_%' AND tc.constraint_type = 'FOREIGN KEY'"
    )
    print(f"\nFK constraints: {len(fks)}")
    for fk in fks:
        print(f"  {fk['table_name']}.{fk['column_name']} -> {fk['fk_table']}")
    await conn.close()


asyncio.run(main())