import json
import logging
from storage.postgres_client import get_pool

log = logging.getLogger(__name__)

async def get_recent_journal(limit: int = 2) -> list[dict]:
    """Fetch recent AI journal entries from Postgres."""
    pool = await get_pool()
    if not pool:
        return []
    query = """
    SELECT snapshot, indicators, market_context, timestamp
    FROM ai_journal
    ORDER BY timestamp DESC
    LIMIT $1
    """
    try:
        async with pool.acquire() as conn:
            records = await conn.fetch(query, limit)
            results = []
            for r in records:
                entry = {
                    "timestamp": r["timestamp"].strftime("%H:%M") if r["timestamp"] else "??:??",
                    "snapshot": json.loads(r["snapshot"]) if isinstance(r["snapshot"], str) else r["snapshot"],
                    "indicators": json.loads(r["indicators"]) if isinstance(r["indicators"], str) else (r["indicators"] or {}),
                    "market_context": r["market_context"] or "",
                }
                results.append(entry)
            return results
    except Exception as e:
        log.error(f"Failed to read ai_journal: {e}")
        return []


def format_journal_for_ai(entries: list[dict]) -> str:
    """Format journal entries into a compact AI-readable block."""
    if not entries:
        return "История журнала: данные пока не записаны.\n"

    lines = ["====== AI ЖУРНАЛ: ИСТОРИЯ РЫНКА ======"]
    for entry in entries:
        ts = entry["timestamp"]
        parts = []
        snapshot = entry.get("snapshot", {})
        indicators = entry.get("indicators", {})

        for sym, data in snapshot.items():
            price = data.get("price", "?")
            change = data.get("change_pct", 0)
            sym_upper = sym.upper()

            # Attach indicators if available for this symbol
            ind = indicators.get(sym, {})
            rsi_str = f" RSI={ind['rsi_14']:.0f}" if "rsi_14" in ind else ""
            macd_str = ""
            if "macd_hist" in ind:
                macd_val = ind["macd_hist"]
                macd_str = f" MACD={'↑' if macd_val > 0 else '↓'}{abs(macd_val):.2f}"

            parts.append(f"{sym_upper}: ${price}{' ' + f'{change:+.1f}%' if change else ''}{rsi_str}{macd_str}")

        line = f"[{ts}] " + " | ".join(parts)
        lines.append(line)

    lines.append("======================================")
    return "\n".join(lines)
