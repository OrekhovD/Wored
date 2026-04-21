#!/usr/bin/env python3
"""Database initialization script."""
import asyncio
import sys

from core.config import AppConfiguration
from storage.database import init_db


async def main() -> int:
    config = AppConfiguration()
    try:
        await init_db(config)
        print("Database initialized successfully")
        return 0
    except Exception as e:
        print(f"Database initialization failed: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
