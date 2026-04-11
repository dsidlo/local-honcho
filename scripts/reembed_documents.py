#!/usr/bin/env python3
"""
Re-embed all existing documents using the current embedding model (qwen3-embedding:8b).
This script regenerates embeddings for pgvector-stored documents.

Uses the OpenAI-compatible /v1/embeddings endpoint with Matryoshka dimensions
truncation to produce 1536-dim vectors compatible with the database schema.

Run from /home/dsidlo/.local/lib/honcho directory:
    cd /home/dsidlo/.local/lib/honcho && python scripts/reembed_documents.py
"""

import asyncio
import logging
import os
import signal
import sys
from datetime import datetime, timezone

# Add src to path BEFORE other imports
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
HONCHO_ROOT = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, HONCHO_ROOT)

import psycopg

# Setup logging
os.makedirs('/tmp', exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('/tmp/reembed_documents.log')
    ]
)
logger = logging.getLogger('reembed')

# Now import honcho modules
from src.config import settings
from src.embedding_client import embedding_client

# Configuration
BATCH_SIZE = 50  # Smaller batch to avoid overwhelming Ollama
SLEEP_BETWEEN_BATCHES = 1.0  # seconds
SHUTDOWN_REQUESTED = False


def signal_handler(signum, frame):
    """Handle shutdown gracefully."""
    global SHUTDOWN_REQUESTED
    logger.info("Shutdown requested, finishing current batch...")
    SHUTDOWN_REQUESTED = True


signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


def get_connection():
    """Create a database connection."""
    conn_str = str(settings.DB.CONNECTION_URI).replace('postgresql+psycopg://', 'postgresql://')
    return psycopg.connect(conn_str)


def count_documents_with_embeddings(conn) -> int:
    """Count total documents that need re-embedding."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT COUNT(*) 
            FROM documents 
            WHERE embedding IS NOT NULL 
              AND deleted_at IS NULL
        """)
        return cur.fetchone()[0] or 0


def get_document_batch(conn, offset: int, limit: int) -> list[dict]:
    """Get a batch of documents that have embeddings."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, content
            FROM documents 
            WHERE embedding IS NOT NULL 
              AND deleted_at IS NULL
            ORDER BY id
            OFFSET %s 
            LIMIT %s
        """, (offset, limit))
        return list(cur.fetchall())


def update_document_embedding(conn, doc_id: str, embedding: list) -> None:
    """Update a document's embedding.
    
    Ensures all values are Python floats to avoid psycopg
    'cannot dump lists of mixed types' errors when embedding
    models return int values (e.g., 0 instead of 0.0).
    """
    # Cast all values to float to prevent mixed float/int serialization errors
    clean_embedding = [float(v) for v in embedding]
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE documents SET embedding = %s WHERE id = %s",
            (clean_embedding, doc_id)
        )


async def process_batch(documents: list[dict], batch_num: int, total_batches: int) -> tuple[int, int, list]:
    """
    Process a batch of documents: get texts, embed, update.
    Returns (success_count, failure_count, list of (id, embedding) tuples)
    """
    success_count = 0
    failure_count = 0
    updates = []
    
    texts = [doc[1] for doc in documents]  # content is second column
    doc_ids = [doc[0] for doc in documents]  # id is first column
    
    if not texts:
        return 0, 0, []
    
    logger.info(f"Batch {batch_num}/{total_batches}: Embedding {len(texts)} documents...")
    
    try:
        # Get embeddings from Ollama
        embeddings = await embedding_client.simple_batch_embed(texts)
        
        # Prepare updates
        for i, embedding in enumerate(embeddings):
            doc_id = doc_ids[i]
            updates.append((doc_id, embedding))
            success_count += 1
        
        logger.info(f"Batch {batch_num}: Successfully embedded {success_count} documents")
        
    except Exception as e:
        logger.error(f"Batch {batch_num}: Failed to process: {e}")
        failure_count += len(documents)
    
    return success_count, failure_count, updates


async def reembed_all_documents():
    """Main function to re-embed all documents."""
    logger.info("=" * 60)
    logger.info("Starting document re-embedding process")
    logger.info(f"Using model: {settings.LLM.OLLAMA_EMBEDDING_MODEL}")
    logger.info("=" * 60)
    
    conn = get_connection()
    
    try:
        # Get total count
        total_docs = count_documents_with_embeddings(conn)
        logger.info(f"Total documents to re-embed: {total_docs:,}")
        
        if total_docs == 0:
            logger.info("No documents found with embeddings. Exiting.")
            return
        
        total_batches = (total_docs + BATCH_SIZE - 1) // BATCH_SIZE
        logger.info(f"Will process in {total_batches} batches of {BATCH_SIZE}")
        
        # Process batches
        total_success = 0
        total_failed = 0
        start_time = datetime.now(timezone.utc)
        
        for batch_num in range(1, total_batches + 1):
            if SHUTDOWN_REQUESTED:
                logger.info("Shutdown requested, stopping after current batch")
                break
            
            offset = (batch_num - 1) * BATCH_SIZE
            documents = get_document_batch(conn, offset, BATCH_SIZE)
            
            if not documents:
                logger.info(f"Batch {batch_num}: No documents found, skipping")
                continue
            
            # Process embeddings (async)
            success, failed, updates = await process_batch(documents, batch_num, total_batches)
            
            # Apply updates (sync DB call)
            if updates:
                try:
                    for doc_id, embedding in updates:
                        update_document_embedding(conn, doc_id, embedding)
                    conn.commit()
                    total_success += success
                except Exception as e:
                    conn.rollback()
                    logger.error(f"Batch {batch_num}: Failed to save embeddings: {e}")
                    total_failed += len(documents)
            
            total_failed += failed
            
            # Calculate progress and ETA
            elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
            docs_per_sec = total_success / elapsed if elapsed > 0 else 0
            remaining_docs = total_docs - (total_success + total_failed)
            eta_seconds = remaining_docs / docs_per_sec if docs_per_sec > 0 else 0
            
            progress_pct = 100 * (total_success + total_failed) / total_docs
            
            logger.info(
                f"Progress: {progress_pct:.1f}% ({total_success + total_failed}/{total_docs}) | "
                f"Speed: {docs_per_sec:.1f} docs/sec | "
                f"ETA: {eta_seconds/60:.1f} minutes"
            )
            
            # Small delay to avoid overwhelming Ollama
            if batch_num < total_batches and not SHUTDOWN_REQUESTED:
                await asyncio.sleep(SLEEP_BETWEEN_BATCHES)
        
        # Final summary
        total_time = (datetime.now(timezone.utc) - start_time).total_seconds()
        logger.info("=" * 60)
        logger.info("Re-embedding complete!")
        logger.info(f"Total documents processed: {total_success + total_failed:,}")
        logger.info(f"Successful: {total_success:,}")
        logger.info(f"Failed: {total_failed:,}")
        if total_time > 0:
            logger.info(f"Total time: {total_time/60:.1f} minutes")
            logger.info(f"Average speed: {total_success/total_time:.1f} docs/sec")
        logger.info("=" * 60)
    
    finally:
        conn.close()


if __name__ == "__main__":
    try:
        asyncio.run(reembed_all_documents())
        sys.exit(0)
    except Exception as e:
        logger.exception("Fatal error in re-embedding process")
        sys.exit(1)
