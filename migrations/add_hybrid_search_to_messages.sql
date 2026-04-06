-- Add hybrid search columns and indexes to messages table.

-- Add embedding column
ALTER TABLE messages ADD COLUMN embedding vector(1536);

-- Add content_tsv column
ALTER TABLE messages ADD COLUMN content_tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', coalesce(content, ''))) STORED;

-- Create HNSW index on embedding
CREATE INDEX CONCURRENTLY ix_messages_embedding_hnsw ON messages USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);

-- GIN index on content_tsv (drop old if exists)
DROP INDEX IF EXISTS ix_messages_content_gin;
CREATE INDEX CONCURRENTLY ix_messages_content_gin ON messages USING GIN (content_tsv);
