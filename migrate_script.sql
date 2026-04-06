-- Migration Script for New Records (2026-04-04) from honcho_dev to hocho
-- Run as superuser on hocho DB. Assumes dblink extension for cross-DB queries.
-- Install dblink if needed: CREATE EXTENSION IF NOT EXISTS dblink;

-- Step 1: Base (workspaces - 0 rows)
INSERT INTO public.workspaces 
SELECT * FROM dblink('DB_CONN_STR', 'SELECT * FROM honcho_dev.public.workspaces WHERE created_at >= ''2026-04-04'' ORDER BY name') 
AS t(id text, name text, created_at timestamptz, metadata jsonb, internal_metadata jsonb, configuration jsonb)
ON CONFLICT (name) DO NOTHING;

-- Step 2: Independent (queue, active_queue_sessions - 0 rows)
INSERT INTO public.queue 
SELECT * FROM dblink('DB_CONN_STR', 'SELECT * FROM honcho_dev.public.queue WHERE created_at >= ''2026-04-04'' ORDER BY id') 
AS t(id bigint, session_id text, work_unit_key text, task_type text, payload jsonb, processed bool, error text, created_at timestamptz, workspace_name text, message_id bigint)
ON CONFLICT (id) DO NOTHING;

INSERT INTO public.active_queue_sessions 
SELECT * FROM dblink('DB_CONN_STR', 'SELECT * FROM honcho_dev.public.active_queue_sessions WHERE last_updated >= ''2026-04-04'' ORDER BY id') 
AS t(id text, work_unit_key text, last_updated timestamptz)
ON CONFLICT (id) DO NOTHING;

-- Step 3: Peers (0 rows)
INSERT INTO public.peers 
SELECT * FROM dblink('DB_CONN_STR', 'SELECT * FROM honcho_dev.public.peers WHERE created_at >= ''2026-04-04'' ORDER BY id') 
AS t(id text, name text, metadata jsonb, internal_metadata jsonb, created_at timestamptz, workspace_name text, configuration jsonb)
ON CONFLICT (id) DO NOTHING;

-- Step 4: Sessions (6 rows)
INSERT INTO public.sessions 
SELECT * FROM dblink('DB_CONN_STR', 'SELECT * FROM honcho_dev.public.sessions WHERE created_at >= ''2026-04-04'' ORDER BY id') 
AS t(id text, name text, is_active bool, metadata jsonb, internal_metadata jsonb, created_at timestamptz, workspace_name text, configuration jsonb)
ON CONFLICT (id) DO NOTHING;

-- Step 5: Session Peers (12 rows)
INSERT INTO public.session_peers 
SELECT * FROM dblink('DB_CONN_STR', 'SELECT * FROM honcho_dev.public.session_peers WHERE joined_at >= ''2026-04-04'' ORDER BY workspace_name, session_name') 
AS t(workspace_name text, session_name text, peer_name text, configuration jsonb, internal_metadata jsonb, joined_at timestamptz, left_at timestamptz)
ON CONFLICT DO NOTHING;  -- Composite PK

-- Step 6: Webhook Endpoints (0 rows)
INSERT INTO public.webhook_endpoints 
SELECT * FROM dblink('DB_CONN_STR', 'SELECT * FROM honcho_dev.public.webhook_endpoints WHERE created_at >= ''2026-04-04'' ORDER BY id') 
AS t(id text, workspace_name text, url text, created_at timestamptz)
ON CONFLICT (id) DO NOTHING;

-- Step 7: Messages (256 rows)
INSERT INTO public.messages 
SELECT * FROM dblink('DB_CONN_STR', 'SELECT * FROM honcho_dev.public.messages WHERE created_at >= ''2026-04-04'' ORDER BY id') 
AS t(id bigint, public_id text, session_name text, content text, metadata jsonb, internal_metadata jsonb, token_count int, seq_in_session bigint, created_at timestamptz, peer_name text, workspace_name text)
ON CONFLICT (id) DO NOTHING;

-- Step 8: Message Embeddings (assume 0-256; query showed 0 new)
INSERT INTO public.message_embeddings 
SELECT * FROM dblink('DB_CONN_STR', 'SELECT * FROM honcho_dev.public.message_embeddings WHERE created_at >= ''2026-04-04'' ORDER BY id') 
AS t(id bigint, content text, embedding vector, message_id text, workspace_name text, session_name text, peer_name text, created_at timestamptz, sync_state text, last_sync_at timestamptz, sync_attempts int)
ON CONFLICT (id) DO NOTHING;

-- Step 9: Collections (0 rows)
INSERT INTO public.collections 
SELECT * FROM dblink('DB_CONN_STR', 'SELECT * FROM honcho_dev.public.collections WHERE created_at >= ''2026-04-04'' ORDER BY id') 
AS t(id text, observer text, observed text, created_at timestamptz, metadata jsonb, internal_metadata jsonb, workspace_name text)
ON CONFLICT (id) DO NOTHING;

-- Step 10: Documents (non-explicit: 0 rows)
INSERT INTO public.documents 
SELECT * FROM dblink('DB_CONN_STR', 'SELECT * FROM honcho_dev.public.documents WHERE created_at >= ''2026-04-04' AND level != ''explicit'' ORDER BY id') 
AS t(id text, internal_metadata jsonb, content text, created_at timestamptz, workspace_name text, session_name text, observer text, observed text, level text, times_derived int, source_ids jsonb, deleted_at timestamptz, sync_state text, last_sync_at timestamptz, sync_attempts int, embedding vector, content_tsv tsvector)
ON CONFLICT (id) DO NOTHING;

-- Verification Queries (run after)
-- SELECT COUNT(*) FROM public.documents WHERE created_at >= '2026-04-04';
-- SELECT COUNT(*) FROM public.messages WHERE created_at >= '2026-04-04';