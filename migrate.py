#!/usr/bin/env python
"""Simple migration script for honcho_dev database"""
import asyncio
import os
import sys

# Set env var before importing
os.environ['HONCHO_DB_CONNECTION_URI'] = 'postgresql+asyncpg://dsidlo:rexrabbit@127.0.0.1:5433/honcho_dev'
os.environ['LLM__EMBEDDING_PROVIDER'] = 'ollama'

sys.path.insert(0, '/home/dsidlo/workspace/honcho')

from src.db import init_db, Base, engine
from sqlalchemy import text

async def migrate():
    """Create schema and initialize database"""
    async with engine.begin() as conn:
        # Create schema
        await conn.execute(text('CREATE SCHEMA IF NOT EXISTS "honcho"'))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        print("Extensions and schema created")
    
    # Create all tables from metadata
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        print("Tables created from Base.metadata")
    
    print("Migration complete!")

if __name__ == "__main__":
    asyncio.run(migrate())
