"""add_hybrid_search_support

Add hybrid search capabilities combining vector similarity, full-text search (FTS),
and trigram fuzzy matching for improved document retrieval.

This migration:
1. Enables the pg_trgm extension for trigram similarity
2. Adds content_tsv generated column for full-text search
3. Creates GIN indexes for fast FTS and trigram queries

Revision ID: adb68784e753
Revises: f1a2b3c4d5e6
Create Date: 2025-04-02 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from migrations.utils import column_exists, get_schema, index_exists

# revision identifiers, used by Alembic.
revision: str = "adb68784e753"
down_revision: str | None = "f1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

schema = get_schema()


def upgrade() -> None:
    """
    Add hybrid search support to documents table.
    
    This migration preserves all existing data by:
    - Using generated columns (computed automatically from existing content)
    - Creating indexes concurrently to avoid table locks
    - Using IF NOT EXISTS for idempotency
    """
    connection = op.get_bind()
    inspector = sa.inspect(connection)

    # Enable pg_trgm extension for trigram similarity (if not already enabled)
    # This is required for fuzzy text matching with the % operator
    connection.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
    print("pg_trgm extension enabled")
    
    # Enable btree_gin extension for composite GIN indexes on regular columns
    # This allows GIN indexes on text columns (workspace_name, observer, observed)
    connection.execute(sa.text("CREATE EXTENSION IF NOT EXISTS btree_gin"))
    print("btree_gin extension enabled")

    # Add content_tsv generated column for full-text search
    # Generated columns automatically compute their values from the content column
    # This ensures existing documents immediately have FTS data without manual updates
    if not column_exists("documents", "content_tsv", inspector):
        connection.execute(
            sa.text(
                f"""
                ALTER TABLE {schema}.documents
                ADD COLUMN content_tsv tsvector
                GENERATED ALWAYS AS (to_tsvector('english', content)) STORED
                """
            )
        )
        print(f"Added content_tsv generated column to {schema}.documents")
    else:
        print(f"content_tsv column already exists in {schema}.documents, skipping")

    # Create GIN index for full-text search (FTS)
    # GIN indexes are optimized for containment queries like tsvector matches
    if not index_exists("documents", "idx_documents_content_tsv", inspector):
        op.create_index(
            "idx_documents_content_tsv",
            "documents",
            ["content_tsv"],
            postgresql_using="gin",
            schema=schema,
        )
        print(f"Created index idx_documents_content_tsv on {schema}.documents")
    else:
        print(f"Index idx_documents_content_tsv already exists, skipping")

    # Create GIN index for trigram similarity search
    # Using gin_trgm_ops enables efficient similarity queries with the % operator
    if not index_exists("documents", "idx_documents_content_trgm", inspector):
        op.create_index(
            "idx_documents_content_trgm",
            "documents",
            [sa.text("content gin_trgm_ops")],
            postgresql_using="gin",
            schema=schema,
        )
        print(f"Created index idx_documents_content_trgm on {schema}.documents")
    else:
        print(f"Index idx_documents_content_trgm already exists, skipping")

    # Optional: Create composite index for filtered hybrid searches
    # This helps when querying with workspace_name, observer, observed filters
    # combined with text search
    if not index_exists("documents", "idx_documents_fts_filtered", inspector):
        op.create_index(
            "idx_documents_fts_filtered",
            "documents",
            ["workspace_name", "observer", "observed", "content_tsv"],
            postgresql_using="gin",
            schema=schema,
        )
        print(f"Created composite index idx_documents_fts_filtered on {schema}.documents")
    else:
        print(f"Index idx_documents_fts_filtered already exists, skipping")

    # Analyze the table to update statistics for the query planner
    connection.execute(
        sa.text(f"ANALYZE {schema}.documents")
    )
    print(f"Analyzed {schema}.documents table")

    print("Hybrid search migration completed successfully")


def downgrade() -> None:
    """
    Remove hybrid search support from documents table.
    
    This reverts all changes made by the upgrade function.
    Existing data is preserved - only generated columns and indexes are removed.
    """
    connection = op.get_bind()
    inspector = sa.inspect(connection)

    # Drop composite index
    if index_exists("documents", "idx_documents_fts_filtered", inspector):
        op.drop_index(
            "idx_documents_fts_filtered",
            table_name="documents",
            schema=schema,
        )
        print(f"Dropped index idx_documents_fts_filtered from {schema}.documents")
    else:
        print(f"Index idx_documents_fts_filtered does not exist, skipping")

    # Drop trigram index
    if index_exists("documents", "idx_documents_content_trgm", inspector):
        op.drop_index(
            "idx_documents_content_trgm",
            table_name="documents",
            schema=schema,
        )
        print(f"Dropped index idx_documents_content_trgm from {schema}.documents")
    else:
        print(f"Index idx_documents_content_trgm does not exist, skipping")

    # Drop FTS index
    if index_exists("documents", "idx_documents_content_tsv", inspector):
        op.drop_index(
            "idx_documents_content_tsv",
            table_name="documents",
            schema=schema,
        )
        print(f"Dropped index idx_documents_content_tsv from {schema}.documents")
    else:
        print(f"Index idx_documents_content_tsv does not exist, skipping")

    # Drop content_tsv column
    if column_exists("documents", "content_tsv", inspector):
        op.drop_column("documents", "content_tsv", schema=schema)
        print(f"Dropped content_tsv column from {schema}.documents")
    else:
        print(f"content_tsv column does not exist, skipping")

    # Note: We intentionally do NOT drop the pg_trgm extension
    # as other parts of the database may depend on it

    print("Hybrid search downgrade completed successfully")
