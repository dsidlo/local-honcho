-- ==========================================================
-- Fix Orphan Representation Work Units
-- Targets: unprocessed representation queue items with no linked messages
-- Action: Mark as processed=true with error message
-- ==========================================================

-- Count orphan representation work units BEFORE update
SELECT
    'BEFORE' as status,
    COUNT(*) as orphan_count,
    (SELECT COUNT(*) FROM queue WHERE task_type = 'representation' AND processed = false) as total_unprocessed_representation,
    (SELECT COUNT(*) FROM queue WHERE processed = false) as total_unprocessed
;

-- ==========================================================
-- UPDATE: Mark orphan representation work units as errored
-- Criteria:
--   - task_type = 'representation'
--   - processed = false
--   - message_id IS NULL (no linked message via JOIN)
-- ==========================================================

UPDATE queue
SET
    processed = true,
    error = 'Detected orphan: no linked messages'
WHERE task_type = 'representation'
  AND processed = false
  AND message_id IS NULL;

-- Count orphan representation work units AFTER update
SELECT
    'AFTER' as status,
    COUNT(*) as remaining_orphan_count,
    (SELECT COUNT(*) FROM queue WHERE task_type = 'representation' AND processed = false) as total_unprocessed_representation,
    (SELECT COUNT(*) FROM queue WHERE processed = false) as total_unprocessed
FROM queue
WHERE task_type = 'representation'
  AND processed = false
  AND message_id IS NULL;

-- Detailed view: Show recently marked orphan work units (this run)
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
LIMIT 20;
