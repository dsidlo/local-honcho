#!/usr/bin/env python3
#
# Standalone script to re-embed messages without embeddings.
# Avoids full config load.
#

import argparse
import asyncio
import logging
import os
from contextlib import suppress
from dotenv import load_dotenv

from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

load_dotenv('/home/dsidlo/.env')  # Load ~/.env

# Hardcoded - OpenAI base URL
DB_URI = 'postgresql+psycopg://dsidlo:rexrabbit@127.0.0.1:5433/honcho'
EMBED_URL = 'https://api.openai.com/v1/embeddings'
EMBED_MODEL = 'text-embedding-3-small'
DIM = 1536

log_file = '/tmp/re-embeddings.log'
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file, mode='a'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)
logger.info('Standalone re-embed PID %s using OpenAI base URL', os.getpid())

with open('/tmp/re-embed.pid', 'w') as f:
    f.write(str(os.getpid()))

def signal_handler(sig, frame):
    logger.info('Interrupted')
    with suppress(OSError):
        os.remove('/tmp/re-embed.pid')
    sys.exit(0)

import signal
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

import httpx

async def simple_embed(text: str) -> list[float] | None:
    logger.info(f'Embedding content via Ollama: {repr(text[:50])}...')
    url = 'http://localhost:11434/api/embeddings'  # Ollama local
    # Truncate to BGE-M3 max context (8192 tokens ~8k chars, safe 6k for code)
    if len(text) > 6000:
        text = text[:6000] + '... [truncated]'
        logger.warning(f'Truncated content for {msg_id} to 6000 chars')
    payload = {
        'model': 'bge-m3',  # BGE-M3: 1024 dim, 8k context
        'prompt': text,
        'options': {'temperature': 0.0}
    }
    # Pad to 1536 for Honcho
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(url, json=payload)
        logger.info(f'Ollama response status: {resp.status_code} for content: {repr(text[:30])}...')
        if resp.status_code == 200:
            data = resp.json()
            emb = data['embedding']
            # Pad if needed (Ollama may vary dim; Honcho expects 1536? Check config)
            if len(emb) < 1536:
                emb += [0.0] * (1536 - len(emb))
            elif len(emb) > 1536:
                emb = emb[:1536]
            logger.info(f'Success embedding {len(emb)} dim for content: {repr(text[:30])}...')
            return emb
        else:
            logger.error(f'HTTP {resp.status_code} for content: {repr(text[:50])}... response: {resp.text[:100]}')
            return None

async def re_embed_message(db: AsyncSession, msg_id: int, public_id: str, content: str, ws: str, sess: str, peer: str) -> bool:
    try:
        if not content.strip():
            logger.info(f'Skipping blank {msg_id}')
            return False

        emb = await simple_embed(content)
        if not emb:
            logger.warning(f'Failed to embed {msg_id}: no embedding returned')
            return False

        # Insert or update embedding (ON CONFLICT for refresh)
        insert_stmt = text('''
            INSERT INTO message_embeddings (message_id, workspace_name, session_name, peer_name, content, embedding)
            VALUES (:public_id, :ws, :sess, :peer, :content, :emb)
            ON CONFLICT (message_id, workspace_name) DO UPDATE SET
                embedding = EXCLUDED.embedding
        ''')
        await db.execute(insert_stmt, {'public_id': public_id, 'ws': ws, 'sess': sess, 'peer': peer, 'content': content, 'emb': emb})
        logger.info(f'Refreshed {msg_id} with {len(emb)} dim for {public_id}')
        return True
    except Exception as e:
        logger.error(f'Error {msg_id}: {e}')
        return False

async def main(batch_size: int = 50, limit: int | None = None):
    engine = create_async_engine(DB_URI, echo=False)
    AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
    async with AsyncSessionLocal() as db:
        # Base query - refresh ALL (even with embeddings)
        base_stmt = '''
            SELECT m.id, m.public_id, m.content, m.workspace_name, m.session_name, m.peer_name
            FROM messages m
            ORDER BY m.created_at ASC
        '''
        if limit:
            base_stmt += f' LIMIT {limit}'
        
        stmt = text(base_stmt)
        result = await db.execute(stmt)
        rows = result.fetchall()
        logger.info(f'Found {len(rows)} unembedded')
        
        success_count = 0
        total = 0
        for i in range(0, len(rows), batch_size):
            batch = rows[i:i + batch_size]
            batch_success = 0
            for row in batch:
                total += 1
                if await re_embed_message(db, *row):
                    batch_success += 1
            
            # Commit batch updates
            await db.commit()
            success_count += batch_success
            logger.info(f'Batch {i//batch_size + 1}: {batch_success}/{len(batch)}, total {success_count}/{total}')
        
        logger.info(f'Complete: {success_count}/{len(rows)} (no DB updates)')
    
    with suppress(OSError):
        os.remove('/tmp/re-embed.pid')
    logger.info('Done')

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Re-embed unembedded')
    parser.add_argument('--batch-size', type=int, default=50)
    parser.add_argument('--limit', type=int)
    args = parser.parse_args()
    
    asyncio.run(main(args.batch_size, args.limit))
