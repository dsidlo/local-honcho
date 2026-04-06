# Hybrid Search for Messages Table

## Overview
Add hybrid search (vector similarity + full-text keyword/BM25-like) to the messages table, matching the documents table capabilities. This enables better search in agent tools (e.g., search_messages) by combining semantic and keyword matching.

## Goals
- Add embedding column and HNSW index to messages for vector search.
- Add tsvector for full-text search with GIN index.
- Update models.py, create Alembic migration.
- Extend crud/message.py with embed and hybrid query methods.
- Test with sample data and queries.
- No downtime, backward compatible.

## Tasks
1. Update src/models.py: Add embedding (Vector(1536)) and content_tsv (TSVECTOR computed) to Message class, plus indexes in __table_args__.
2. Create Alembic migration: Add columns/indexes to messages table (CONCURRENTLY).
3. Update src/crud/message.py: Add async embed_messages and search_messages_hybrid functions.
4. Update src/utils/search.py: Integrate hybrid for message search.
5. Add tests: tests/crud/test_message_hybrid.py for embed/query.
6. Run migration and test hybrid query on sample messages.

## Success Criteria
- Hybrid query returns relevant results (cosine + ts_rank).
- Embed 10 sample messages successfully.
- No performance regression (index build <5min).
- Backward compatible (old messages searchable via keyword).

## Risks
- Migration on large table (26k rows)—use CONCURRENTLY.
- Vector dim mismatch (if not 1536)—use OpenAI default.
- PGvector extension assumed enabled.
