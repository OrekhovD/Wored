import logging
import json
from storage.postgres_client import get_pool

log = logging.getLogger(__name__)

async def write_entry(snapshot: dict, indicators: dict, context: str = ""):
    """Write AI journal entry to postgres."""
    pool = await get_pool()
    query = """
    INSERT INTO ai_journal (snapshot, indicators, market_context)
    VALUES ($1, $2, $3)
    """
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                query, 
                json.dumps(snapshot), 
                json.dumps(indicators), 
                context
            )
        log.info("AI journal entry written.")
    except Exception as e:
        log.error(f"Failed to write AI journal: {e}")
