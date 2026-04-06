-- ==========================================================
-- Fix Orphan Work Units Script
-- Targets: unprocessed queue items with no linked messages (message_id IS NULL)
-- Action: Mark as processed=true with error message
-- Primary: representation (as requested)
-- Optional: summary, reconciler, deletion, dream, webhook
-- ==========================================================

-- ==========================================================
-- STEP 1: Pre-Update Audit - Show all orphans by task type
-- ==========================================================
\echo '--- ORPHAN AUDIT (unprocessed + message_id IS NULL) ---'
SELECT 
    task_type,
    COUNT(*) as orphan_count,
    MIN(id) as min_id,
    MAX(id) as max_id,
    MIN(created_at) as oldest_created,
    MAX(created_at) as newest_created
FROM queue
WHERE processed = false
  AND message_id IS NULL
GROUP BY task_type
ORDER BY task_type;

-- ==========================================================
-- STEP 2: Count unprocessed representation work units BEFORE update
-- ==========================================================
\echo ''
\echo '--- REPRESENTATION STATUS BEFORE ---'
SELECT
    (SELECT COUNT(*) FROM queue WHERE task_type = 'representation' AND processed = false AND message_id IS NULL) as orphan_count,
    (SELECT COUNT(*) FROM queue WHERE task_type = 'representation' AND processed = false) as total_unprocessed,
    (SELECT COUNT(*) FROM queue WHERE task_type = 'representation' AND processed = false AND message_id IS NOT NULL) as valid_unprocessed;

-- ==========================================================
-- STEP 3: UPDATE representation orphans (if any exist)
-- Note: Only targets representation, unprocessed, with NULL message_id
-- ==========================================================
\echo ''
\echo '--- MARKING REPRESENTATION ORPHANS AS ERRORED ---'

WITH updated AS (
    UPDATE queue
    SET
        processed = true,
        error = 'Detected orphan: no linked messages'
    WHERE task_type = 'representation'
      AND processed = false
      AND message_id IS NULL
    RETURNING id, work_unit_key, created_at, workspace_name
)
SELECT 
    COUNT(*) as marked_count,
    string_agg(id::text, ', ') as affected_ids
FROM updated;

-- ==========================================================
-- STEP 4: Count unprocessed representation work units AFTER update
-- ==========================================================
\echo ''
\echo '--- REPRESENTATION STATUS AFTER ---'
SELECT
    (SELECT COUNT(*) FROM queue WHERE task_type = 'representation' AND processed = false AND message_id IS NULL) as remaining_orphan_count,
    (SELECT COUNT(*) FROM queue WHERE task_type = 'representation' AND processed = false) as total_unprocessed,
    (SELECT COUNT(*) FROM queue WHERE task_type = 'representation' AND processed = true AND error = 'Detected orphan: no linked messages') as total_errored_orphans;

-- ==========================================================
-- STEP 5: Show recently marked orphan work units
-- ==========================================================
\echo ''
\echo '--- RECENTLY MARKED ORPHAN REPRESENTATIONS ---'
SELECT
    id,
    work_unit_key,
    task_type,
    processed,
    error,
    created_at,
    workspace_name,
    session_id,
    message_id
FROM queue
WHERE task_type = 'representation'
  AND error = 'Detected orphan: no linked messages'
  AND processed = true
ORDER BY id DESC
LIMIT 10;

-- ==========================================================
-- STEP 6: Final Queue Health Summary
-- ==========================================================
\echo ''
\echo '--- FINAL QUEUE HEALTH SUMMARY ---'
SELECT 
    task_type,
    processed,
    COUNT(*) as count,
    COUNT(message_id) as with_message,
    COUNT(*) - COUNT(message_id) as without_message
FROM queue
GROUP BY task_type, processed
ORDER BY task_type, processed;
