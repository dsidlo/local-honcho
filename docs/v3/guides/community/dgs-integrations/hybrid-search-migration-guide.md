# Hybrid Search Migration Guide

This guide documents the migration process for adding hybrid search capabilities (Vector + Full-Text Search + Trigram) to Honcho's document retrieval system.

## Overview

This migration adds:
- `pg_trgm` PostgreSQL extension for trigram similarity matching
- `content_tsv` tsvector column for full-text search
- GIN indexes for efficient FTS and trigram queries

**Impact**: Database schema change with preserved existing data.

---

## Prerequisites

Before running the migration, ensure:

1. **Backup your database** - Always backup before schema changes
2. **PostgreSQL version >= 14** - Required for generated columns
3. **pg_trgm extension available** - Check with your database provider
4. **Sufficient disk space** - New indexes require ~25-30% additional storage

---

## Migration Process

### Step 1: Create Database Backup

```bash
# For local PostgreSQL
pg_dump -h localhost -U postgres -d honcho > honcho_backup_$(date +%Y%m%d_%H%M%S).sql

# For Supabase (using connection string)
pg_dump -h db.xxxxx.supabase.co -p 5432 -U postgres -d postgres > honcho_backup_$(date +%Y%m%d_%H%M%S).sql
```

### Step 2: Run Alembic Migration

The migration is managed through Alembic:

```bash
# From project root
cd /home/dsidlo/workspace/honcho

# Run migrations
alembic upgrade head

# Or if using honcho's setup
uv run alembic upgrade head
```

### Step 3: What the Migration Does

The migration performs the following:

1. **Enables pg_trgm extension**
   ```sql
   CREATE EXTENSION IF NOT EXISTS pg_trgm;
   ```

2. **Adds content_tsv column** (generated column - automatically populated)
   ```sql
   ALTER TABLE documents 
   ADD COLUMN content_tsv tsvector 
   GENERATED ALWAYS AS (to_tsvector('english', content)) STORED;
   ```

3. **Creates GIN indexes** (using CONCURRENTLY to avoid table locks)
   ```sql
   CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_documents_content_tsv 
   ON documents USING GIN (content_tsv);

   CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_documents_content_trgm 
   ON documents USING GIN (content gin_trgm_ops);
   ```

4. **Analyzes table for query planner**
   ```sql
   ANALYZE documents;
   ```

### Step 4: Migration Duration

| Table Size | Estimated Duration |
|------------|-------------------|
| < 10K rows | < 1 minute |
| 100K rows | 2-5 minutes |
| 1M rows | 10-20 minutes |
| 10M+ rows | 1-2 hours |

*Note: Using `CONCURRENTLY` allows reads/writes during index creation but takes longer.*

---

## Verification

### 1. Verify Existing Data is Preserved

Check document count before and after:

```sql
-- Document count should remain unchanged
SELECT COUNT(*) FROM documents;

-- Check sample documents
SELECT id, content, LEFT(content, 100) as content_preview 
FROM documents 
LIMIT 5;
```

**Expected Result**: Document count matches pre-migration count.

### 2. Verify tsvector Column is Populated

The `content_tsv` column is automatically populated for all existing rows:

```sql
-- Verify tsvector generation
SELECT 
    id,
    LEFT(content, 50) as content_preview,
    content_tsv
FROM documents 
LIMIT 5;

-- Check that tsvector is populated for all rows
SELECT 
    COUNT(*) as total_rows,
    COUNT(content_tsv) as rows_with_tsv
FROM documents;
```

**Expected Result**: `rows_with_tsv` equals `total_rows`.

### 3. Verify Indexes Exist

```sql
-- List new indexes
SELECT 
    indexname,
    indexdef
FROM pg_indexes
WHERE tablename = 'documents'
AND indexname IN (
    'idx_documents_content_tsv',
    'idx_documents_content_trgm'
);
```

**Expected Result**: Both indexes should appear with `USING gin`.

### 4. Verify FTS Works

```sql
-- Test full-text search
SELECT 
    id,
    LEFT(content, 100) as content_preview,
    ts_rank_cd(content_tsv, query) as rank
FROM documents,
    plainto_tsquery('english', 'webhook endpoint') query
WHERE content_tsv @@ query
ORDER BY rank DESC
LIMIT 5;
```

**Expected Result**: Returns relevant documents ranked by relevance.

### 5. Verify Trigram Works

```sql
-- Test trigram similarity (handles typos)
SELECT 
    id,
    LEFT(content, 100) as content_preview,
    similarity(content, 'webhok endoint') as sim
FROM documents
WHERE content % 'webhok endoint'
ORDER BY similarity(content, 'webhok endoint') DESC
LIMIT 5;
```

**Expected Result**: Returns documents similar to the misspelled query.

### 6. Verify Extension is Enabled

```sql
-- Check pg_trgm extension
SELECT * FROM pg_extension WHERE extname = 'pg_trgm';
```

**Expected Result**: One row showing pg_trgm as installed.

---

## Testing Hybrid Search

After migration, test the hybrid search functionality:

```python
import asyncio
from sqlalchemy.ext.asyncio import AsyncSession
from src.crud.document import query_documents_hybrid
from src.dependencies import db

async def test_hybrid():
    async with db() as session:
        # Test RRF method
        results = await query_documents_hybrid(
            db=session,
            workspace_name="your-workspace",
            query="API key webhook",
            observer="assistant",
            observed="user",
            top_k=10,
            method="rrf"
        )
        print(f"Found {len(results)} documents")
        for doc in results:
            print(f"  - {doc.id}: {doc.content[:80]}...")

# Run test
asyncio.run(test_hybrid())
```

---

## Rollback Procedure

If you need to rollback the migration:

### Option 1: Use Alembic Downgrade

```bash
# Get current revision
alembic current

# Downgrade one revision
alembic downgrade -1

# Or downgrade to specific revision
alembic downgrade <previous_revision_id>
```

### Option 2: Manual Rollback

If Alembic fails, manually rollback:

```sql
-- Remove indexes
DROP INDEX IF EXISTS idx_documents_content_tsv;
DROP INDEX IF EXISTS idx_documents_content_trgm;
DROP INDEX IF EXISTS idx_documents_fts_filtered;  -- if exists

-- Remove column
ALTER TABLE documents DROP COLUMN IF EXISTS content_tsv;

-- Optionally remove extension (check if other databases/schemas use it)
-- DROP EXTENSION IF EXISTS pg_trgm;
```

### Option 3: Restore from Backup

```bash
# Drop and recreate database (extreme case)
dropdb -h localhost -U postgres honcho
createdb -h localhost -U postgres honcho

# Restore from backup
psql -h localhost -U postgres -d honcho < honcho_backup_YYYYMMDD_HHMMSS.sql
```

---

## Troubleshooting

### Issue: Migration hangs on large tables

**Solution**: The `CONCURRENTLY` option in the migration should prevent long locks. If still hanging:

```sql
-- Check for locks
SELECT 
    relation::regclass,
    mode,
    granted
FROM pg_locks
WHERE relation::regclass::text = 'documents';
```

Cancel long-running transactions if necessary, then retry.

### Issue: tsvectors not populating

**Solution**: Generated columns should auto-populate. If not:

```sql
-- Force table rewrite to populate generated column
ALTER TABLE documents ALTER COLUMN content TYPE TEXT;

-- Or for large tables, use VACUUM and ANALYZE
VACUUM ANALYZE documents;
```

### Issue: Slow queries after migration

**Solution**: Update table statistics:

```sql
ANALYZE documents;
```

### Issue: Disk space issues

**Solution**: Monitor index size:

```sql
SELECT 
    pg_size_pretty(pg_relation_size('idx_documents_content_tsv')) as tsv_index_size,
    pg_size_pretty(pg_relation_size('idx_documents_content_trgm')) as trgm_index_size;
```

If space is critical, consider using `ONLINE` (if supported) or scheduling during low-traffic times.

---

## Performance Monitoring

After migration, monitor these metrics:

```sql
-- Index usage statistics
SELECT 
    schemaname,
    tablename,
    indexname,
    idx_scan,
    idx_tup_read,
    idx_tup_fetch
FROM pg_stat_user_indexes
WHERE tablename = 'documents';
```

**Key metrics to watch**:
- `idx_scan`: Number of times index was used
- `idx_tup_read`: Number of tuples read from index
- Query latency on the `/context` endpoint

---

## Post-Migration Checklist

- [ ] Database backup created before migration
- [ ] Migration completed without errors
- [ ] Document count matches pre-migration
- [ ] tsvector column populated for all rows
- [ ] Both GIN indexes created successfully
- [ ] FTS queries return expected results
- [ ] Trigram queries return expected results
- [ ] Application /context endpoint works correctly
- [ ] Hybrid search enabled via feature flag (if applicable)
- [ ] Performance metrics baseline established

---

## Feature Flags

To control rollout of hybrid search:

```python
# In your application code
from src.config import settings

# Check if hybrid search is enabled
if settings.HYBRID_SEARCH.ENABLED:
    use_hybrid = True
else:
    use_hybrid = False
```

Or per-request override:

```python
# Force vector-only search even if migration is applied
representation = await get_working_representation(
    workspace_name="ws",
    observer="assistant",
    observed="user",
    include_semantic_query="webhook API",
    use_hybrid=False,  # Override
)
```

---

## References

- [PostgreSQL Full-Text Search Documentation](https://www.postgresql.org/docs/current/textsearch.html)
- [pg_trgm Extension](https://www.postgresql.org/docs/current/pgtrgm.html)
- [Honcho Hybrid Search Specification](./Honcho-Vector-FTS-Trigram.md)
- [Alembic Documentation](https://alembic.sqlalchemy.org/)

---

## Support

If you encounter issues during migration:

1. Check application logs for SQL errors
2. Verify database user has permissions to create extensions
3. Ensure PostgreSQL version compatibility
4. Review [Troubleshooting](#troubleshooting) section above
